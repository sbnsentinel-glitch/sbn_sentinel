import asyncio
import json
import logging
from enum import Enum
from typing import Dict, Any, AsyncGenerator, Optional
from app.integrations.fhir.url_validator import validate_fhir_url, UntrustedExternalUrl

logger = logging.getLogger(__name__)

__all__ = [
    "BulkExportMode",
    "BulkExportManager",
    "UntrustedExternalUrl",
]


class BulkExportMode(str, Enum):
    """Supported FHIR Bulk Data export modes."""
    PATIENT = "Patient"
    GROUP = "Group"


class BulkExportManager:
    """
    Handles FHIR Bulk Data ($export) operations (SMART Backend Services spec).
    Supports Patient/$export and Group/{id}/$export modes.
    All streaming routes through the shared HttpTransport (stream_lines).
    Enforces URL trust boundary on Content-Location, polling URL, and NDJSON URLs.
    kickoff → async polling → output manifest → NDJSON stream
    """

    def __init__(self, transport, headers: Dict[str, str], base_url: Optional[str] = None):
        self.transport = transport
        self.headers = {**headers}
        self.headers["Accept"] = "application/fhir+json"
        self.headers["Prefer"] = "respond-async"
        self.base_url = base_url

    def _build_export_url(
        self,
        base_url: str,
        mode: BulkExportMode,
        group_id: str = None,
    ) -> str:
        """
        Build the correct $export URL based on mode.
        Patient/$export — export all patients the system app can access.
        Group/{group_id}/$export — export a specific group's data.
        Does NOT use the generic Group/all assumption.
        """
        base = base_url.rstrip("/")
        if mode == BulkExportMode.PATIENT:
            return f"{base}/Patient/$export"
        elif mode == BulkExportMode.GROUP:
            if not group_id:
                raise ValueError(
                    "group_id is required for Group/$export mode"
                )
            return f"{base}/Group/{group_id}/$export"
        else:
            raise ValueError(f"Unsupported BulkExportMode: {mode}")

    async def kickoff(
        self,
        base_url: str,
        mode: BulkExportMode = BulkExportMode.PATIENT,
        group_id: str = None,
    ) -> str:
        """Starts the bulk export and returns the validated polling status endpoint."""
        self.base_url = base_url
        url = self._build_export_url(base_url, mode, group_id)

        response = await self.transport.get(url, headers=self.headers)

        if response.status_code == 202:
            location = response.headers.get("Content-Location")
            if not location:
                raise ValueError("Bulk export kickoff returned 202 but no Content-Location")
            # Enforce URL trust boundary on Content-Location before returning
            validated_location = validate_fhir_url(location, base_url)
            return validated_location

        raise ValueError(f"Failed to kickoff bulk export. Status: {response.status_code}")

    async def poll_until_complete(
        self, status_url: str, max_attempts: int = 20, approved_base_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """Polls the status endpoint until the manifest is ready, enforcing URL boundaries."""
        effective_base = approved_base_url or self.base_url
        if effective_base:
            status_url = validate_fhir_url(status_url, effective_base)

        attempt = 0
        while attempt < max_attempts:
            attempt += 1
            response = await self.transport.get(status_url, headers=self.headers)

            if response.status_code == 200:
                manifest = response.json()
                # Validate output NDJSON URLs in the manifest
                if effective_base and isinstance(manifest, dict) and "output" in manifest:
                    for item in manifest["output"]:
                        if isinstance(item, dict) and "url" in item:
                            item["url"] = validate_fhir_url(item["url"], effective_base)
                return manifest
            elif response.status_code == 202:
                retry_after = int(response.headers.get("Retry-After", 5))
                logger.info(f"Bulk export in progress. Waiting {retry_after}s.")
                await asyncio.sleep(retry_after)
            else:
                raise ValueError(
                    f"Unexpected bulk export status: {response.status_code}"
                )

        raise TimeoutError("Bulk export timed out after max polling attempts")

    async def stream_ndjson(
        self, file_url: str, approved_base_url: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Streams NDJSON output file line-by-line through the shared transport.
        Enforces trust boundary before initiating connection or sending Authorization header.
        """
        effective_base = approved_base_url or self.base_url
        if effective_base:
            file_url = validate_fhir_url(file_url, effective_base)

        # Stream via shared transport.stream_lines — no raw httpx client here
        async for line in self.transport.stream_lines(file_url, headers=self.headers):
            yield json.loads(line)

    async def process_ndjson_stream(
        self,
        connector_id: str,
        file_url: str,
        resource_type: str = "Unknown",
        vendor: str = "fhir",
        batch_size: int = 100,
        approved_base_url: Optional[str] = None,
        progress_callback=None,
    ) -> Dict[str, Any]:
        """
        Process NDJSON in bounded durable batches through F-21 idempotent ingress.
        Replay safety:
        - Deterministic ingestion identity for each record (fact_key = sha256)
        - Replay is guaranteed idempotent (duplicates skipped or unique constraint)
        - Progress is committed only after durable batch persistence
        """
        from app.services.ingress_service import canonical_ingress

        batch = []
        total_processed = 0
        lines_streamed = 0

        async for item in self.stream_ndjson(file_url, approved_base_url=approved_base_url):
            lines_streamed += 1
            batch.append({
                "context_type": item.get("resourceType", resource_type),
                "vendor": vendor,
                "detail": item,
            })

            if len(batch) >= batch_size:
                result = await canonical_ingress.submit_batch(connector_id, batch)
                total_processed += result.get("processed", 0)
                if progress_callback:
                    await progress_callback(lines_streamed)
                batch.clear()

        if batch:
            result = await canonical_ingress.submit_batch(connector_id, batch)
            total_processed += result.get("processed", 0)
            if progress_callback:
                await progress_callback(lines_streamed)
            batch.clear()

        return {
            "status": "Success",
            "lines_streamed": lines_streamed,
            "processed": total_processed,
        }
