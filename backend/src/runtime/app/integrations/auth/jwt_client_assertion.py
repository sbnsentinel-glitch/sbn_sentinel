import uuid
import time
import jwt
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from app.integrations.core.contracts import AuthStrategy
from app.connectors.base_connector import ConnectorException

logger = logging.getLogger(__name__)


@dataclass
class TokenLease:
    """
    Durable token lease object encapsulating access token, token type,
    expiration timestamp, and granted scopes.
    """
    access_token: str
    token_type: str
    expires_at: datetime
    scopes: List[str] = field(default_factory=list)
    raw_response: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self, buffer_seconds: int = 30) -> bool:
        """Returns True if the token is expired or within buffer_seconds of expiration."""
        now = datetime.now(timezone.utc)
        return (self.expires_at - now).total_seconds() <= buffer_seconds

    def __getitem__(self, item: str):
        if hasattr(self, item):
            return getattr(self, item)
        return self.raw_response.get(item)

    def get(self, item: str, default=None):
        if hasattr(self, item):
            return getattr(self, item)
        return self.raw_response.get(item, default)


class JwtClientAssertionAuth(AuthStrategy):
    """
    Implements OAuth 2.0 Client Credentials Grant using a JWT Client Assertion
    (RFC 7523) to authenticate to external FHIR servers.
    The token_endpoint is ALWAYS sourced from SMART discovery at runtime.
    """

    def __init__(
        self,
        client_id: str,
        private_key: str,
        key_id: str,
        token_endpoint: str,
        scopes: List[str] = None,
        algorithm: str = "RS384",
    ):
        self.client_id = client_id
        self.private_key = private_key
        self.key_id = key_id
        self.token_endpoint = token_endpoint
        # Scopes must be minimum-necessary, derived from manifest — never wildcard
        self.scopes = scopes or []
        self.algorithm = algorithm
        self._current_lease: Optional[TokenLease] = None

    def _generate_jwt_assertion(self) -> str:
        """Generates a signed JWT client assertion with compliant header."""
        now = int(time.time())
        payload = {
            "iss": self.client_id,
            "sub": self.client_id,
            "aud": self.token_endpoint,
            "exp": now + 300,  # 5-minute window
            "iat": now,
            # jti MUST be globally unique — never reuse client_id+timestamp
            "jti": str(uuid.uuid4()),
        }

        token = jwt.encode(
            payload,
            self.private_key,
            algorithm=self.algorithm,
            headers={
                "kid": self.key_id,
                "typ": "JWT",
            },
        )
        return token

    async def get_valid_token(self, min_validity_seconds: int = 30) -> TokenLease:
        """
        Returns active token lease, refreshing automatically if near expiry.
        """
        if self._current_lease is None or self._current_lease.is_expired(buffer_seconds=min_validity_seconds):
            await self.authenticate()
        return self._current_lease

    async def authenticate(self) -> TokenLease:
        """
        Authenticates against the token endpoint.
        Validates the token response and returns a structured TokenLease.
        Scopes are minimum-necessary as derived from manifest resources.
        """
        assertion = self._generate_jwt_assertion()

        from app.integrations.core.transport import HttpTransport
        transport = HttpTransport()

        scope_string = " ".join(self.scopes) if self.scopes else ""

        data = {
            "grant_type": "client_credentials",
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": assertion,
        }
        if scope_string:
            data["scope"] = scope_string

        response = await transport.post(self.token_endpoint, data=data)
        token_json = response.json() if hasattr(response, "json") else response

        # Validate token response before use
        if not isinstance(token_json, dict) or not token_json.get("access_token"):
            raise ConnectorException(
                "Invalid token response: missing or empty access_token",
                failure_code="AUTHENTICATION_FAILED",
            )

        access_token = str(token_json["access_token"])

        # Token type must be present and supported (Bearer)
        token_type = token_json.get("token_type")
        if not token_type or str(token_type).lower() != "bearer":
            raise ConnectorException(
                f"Unsupported token type: {token_type}",
                failure_code="AUTHENTICATION_FAILED",
            )

        # Expiry must be a valid integer
        try:
            expires_in = int(token_json["expires_in"])
        except (KeyError, ValueError, TypeError):
            raise ConnectorException(
                "Invalid token expiry",
                failure_code="AUTHENTICATION_FAILED",
            )

        # Granted scopes must satisfy required minimum scopes
        if self.scopes:
            raw_scope = token_json.get("scope")
            if not raw_scope:
                raise ConnectorException(
                    "Required scopes were not granted: missing scope in response",
                    failure_code="AUTHORIZATION_FAILED",
                )
            if isinstance(raw_scope, str):
                granted = set(raw_scope.split())
            elif isinstance(raw_scope, (list, set)):
                granted = set(raw_scope)
            else:
                granted = set()

            required = set(self.scopes)
            if not required.issubset(granted):
                raise ConnectorException(
                    f"Required scopes were not granted: missing {required - granted}",
                    failure_code="AUTHORIZATION_FAILED",
                )
            granted_scopes = list(granted)
        else:
            raw_scope = token_json.get("scope", "")
            if isinstance(raw_scope, str):
                granted_scopes = raw_scope.split() if raw_scope else []
            elif isinstance(raw_scope, (list, set)):
                granted_scopes = list(raw_scope)
            else:
                granted_scopes = []

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        lease = TokenLease(
            access_token=access_token,
            token_type=token_type,
            expires_at=expires_at,
            scopes=granted_scopes,
            raw_response=token_json,
        )
        self._current_lease = lease
        return lease
