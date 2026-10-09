from typing import Dict, Any, List, AsyncGenerator
import logging
from app.integrations.fhir.url_validator import (
    UntrustedExternalUrl,
    UntrustedPaginationUrl,
    validate_fhir_url,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BundlePager",
    "InvalidFHIRResponse",
    "UntrustedExternalUrl",
    "UntrustedPaginationUrl",
]


class InvalidFHIRResponse(Exception):
    """Raised when a FHIR search response is not a valid Bundle or is malformed."""
    pass


class BundlePager:
    """
    Handles generic FHIR Bundle pagination for search operations using standard 'next' links.
    Validates that:
    1. The response is a valid FHIR Bundle.
    2. Any 'next' pagination link stays strictly within the approved FHIR origin.
    """

    def __init__(self, transport, headers: Dict[str, str], auth=None):
        self.transport = transport
        self.headers = headers
        self.auth = auth

    async def fetch_all(self, initial_url: str, params: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Fetches all resources across all pages of a bundle.
        """
        resources = []
        async for batch in self.iterate(initial_url, params):
            resources.extend(batch)
        return resources

    async def iterate(self, initial_url: str, params: Dict[str, Any] = None) -> AsyncGenerator[List[Dict[str, Any]], None]:
        """
        Yields batches of resources from each page.
        """
        url = initial_url
        current_params = params
        approved_base_url = initial_url

        while url:
            if self.auth and hasattr(self.auth, "get_valid_token"):
                token_lease = await self.auth.get_valid_token(min_validity_seconds=30)
                self.headers["Authorization"] = f"Bearer {token_lease.access_token}"
            try:
                response = await self.transport.get(url, headers=self.headers, params=current_params)
                data = response.json()

                if not isinstance(data, dict) or data.get("resourceType") != "Bundle":
                    res_type = data.get("resourceType") if isinstance(data, dict) else "unknown"
                    raise InvalidFHIRResponse(
                        f"FHIR search response must be a Bundle, got '{res_type}'"
                    )

                entries = data.get("entry", [])
                resources = [entry.get("resource", {}) for entry in entries if entry.get("resource")]

                if resources:
                    yield resources

                # Find next page link
                next_link = next(
                    (link.get("url") for link in data.get("link", []) if link.get("relation") == "next"),
                    None
                )
                if next_link:
                    # Enforce shared URL trust boundary
                    url = validate_fhir_url(next_link, approved_base_url)
                else:
                    url = None

                current_params = None  # Params are typically embedded in the next link

            except Exception as e:
                logger.error(f"Error paginating FHIR bundle at {url}: {e}")
                raise e
