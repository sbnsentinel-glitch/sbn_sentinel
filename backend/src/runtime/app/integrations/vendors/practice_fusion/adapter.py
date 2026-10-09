from app.integrations.core.contracts import IntegrationAdapter
import logging
import time
from typing import Dict, Any, List, TYPE_CHECKING

if TYPE_CHECKING:
    from app.integrations.fhir.capability_snapshot import CapabilitySnapshot

logger = logging.getLogger(__name__)


class ConfigurationInvalid(Exception):
    """Raised when required adapter configuration is missing."""


class PracticeFusionAdapter(IntegrationAdapter):
    """
    Thin adapter for Practice Fusion FHIR.
    Relies on shared FHIR behaviors, auth, and transport layers.
    All configuration must be present — no guessed defaults.
    SMART discovery drives the token endpoint at runtime.
    Iterates over manifest-enabled resources gated by CapabilitySnapshot.
    """

    REQUIRED_CONFIG = ["base_url", "client_id"]

    def __init__(self, auth, manifest, config: Dict[str, Any]):
        for field in self.REQUIRED_CONFIG:
            if not config.get(field):
                raise ConfigurationInvalid(
                    f"Practice Fusion requires '{field}' in configuration"
                )

        # auth may be None when created by the registry before SMART discovery.
        # Call _ensure_auth() before any method that needs a token.
        self.auth = auth
        self.manifest = manifest
        self.config = config
        self.connector_id = config.get("id", "PF-SYS")
        self._base_url = config.get("base_url")

    def _ensure_auth(self) -> None:
        """Raise if auth has not yet been resolved via SMART discovery."""
        if self.auth is None:
            raise ConfigurationInvalid(
                "PracticeFusionAdapter: auth not yet resolved. "
                "Call sync() first to perform SMART discovery and build auth."
            )

    async def _resolve_base_url(self) -> str:
        return self._base_url.rstrip("/")

    async def get_capability_statement(self) -> "CapabilitySnapshot":
        """
        Fetch vendor CapabilityStatement and return a rich CapabilitySnapshot.
        """
        from app.integrations.fhir.capability_snapshot import CapabilitySnapshot
        from app.integrations.core.transport import HttpTransport

        base_url = await self._resolve_base_url()

        transport = HttpTransport()
        response = await transport.get(f"{base_url}/metadata")
        data = response.json()
        return CapabilitySnapshot.from_fhir_metadata(data)

    async def get_resource(
        self,
        resource_type: str,
        query_params: Dict[str, Any] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch a single resource type from the FHIR server."""
        self._ensure_auth()
        token_data = await self.auth.authenticate()

        from app.integrations.core.transport import HttpTransport
        transport = HttpTransport()

        base_url = await self._resolve_base_url()

        headers = {
            "Authorization": f"Bearer {token_data['access_token']}",
            "Accept": "application/fhir+json",
        }
        response = await transport.get(
            f"{base_url}/{resource_type}", headers=headers, params=query_params
        )
        data = response.json()

        if data.get("resourceType") == "Bundle":
            return [entry.get("resource", {}) for entry in data.get("entry", [])]
        raise ValueError(f"Search for {resource_type} did not return a Bundle.")

    async def sync(self) -> Dict[str, Any]:
        """
        Main sync entrypoint.
        1. Resolve token_endpoint via SMART discovery.
        2. Fetch CapabilitySnapshot.
        3. Iterate manifest-enabled resources, gated by capability + mapper.
        4. Commit cursor only after durable persistence succeeds.
        """
        from app.integrations.fhir.discovery import SmartDiscovery
        from app.integrations.fhir.bundle_pager import BundlePager
        from app.integrations.core.transport import HttpTransport
        from app.services.cursor_store import cursor_store
        from app.services.ingress_service import canonical_ingress

        base_url = await self._resolve_base_url()

        # Step 1: Resolve token_endpoint from SMART discovery
        discovery = SmartDiscovery(base_url)
        token_endpoint = await discovery.get_token_endpoint()

        # Build auth via manifest factory AFTER SMART discovery has resolved endpoint.
        # This is the clean production sequence:
        #   Registry.create() -> adapter (no auth yet)
        #   adapter.sync() -> SMART discovery -> manifest.build_auth() -> token exchange
        self.auth = self.manifest.build_auth(
            config=self.config,
            token_endpoint=token_endpoint,
        )

        # Step 2: Fetch capability snapshot
        capabilities = await self.get_capability_statement()

        transport = HttpTransport()
        total_processed = 0
        start_time = time.time()

        token_data = await self.auth.authenticate()
        headers = {
            "Authorization": f"Bearer {token_data['access_token']}",
            "Accept": "application/fhir+json",
        }
        pager = BundlePager(transport, headers, auth=self.auth)

        # Step 3: Iterate manifest-enabled resources
        for resource_type, resource_config in self.manifest.resources.items():
            if not capabilities.supports_search(resource_type):
                logger.info(
                    f"[{self.connector_id}] Skipping {resource_type}: "
                    "not in CapabilityStatement or search not supported"
                )
                continue

            # Gate: mapper must exist
            if not resource_config.mapper:
                logger.info(
                    f"[{self.connector_id}] Skipping {resource_type}: no mapper defined"
                )
                continue

            checkpoint = cursor_store.get(self.connector_id, resource_type)
            params = {}

            # Only use _lastUpdated param if server supports it
            if checkpoint and capabilities.supports_last_updated(resource_type):
                params["_lastUpdated"] = f"gt{checkpoint}"

            url = f"{base_url}/{resource_type}"
            newest_checkpoint = checkpoint

            # Process in bounded batches (page by page) instead of loading all pages into memory
            async for batch in pager.iterate(url, params):
                if not batch:
                    continue

                canonical_records = []
                for r in batch:
                    canonical_records.append(
                        {
                            "context_type": resource_type,
                            "vendor": self.manifest.vendor_name,
                            "detail": r,
                        }
                    )
                    last_updated = r.get("meta", {}).get("lastUpdated")
                    if last_updated:
                        if not newest_checkpoint or last_updated > newest_checkpoint:
                            newest_checkpoint = last_updated

                if canonical_records:
                    result = await canonical_ingress.submit_batch(
                        self.connector_id, canonical_records
                    )
                    total_processed += result.get("processed", 0)

                    # Step 4: Commit cursor AFTER durable persistence of the batch
                    if newest_checkpoint:
                        cursor_store.commit(
                            self.connector_id, resource_type, newest_checkpoint
                        )

        return {
            "status": "Success",
            "duration_ms": int((time.time() - start_time) * 1000),
            "processed": total_processed,
        }
