import hashlib
import json
import logging
import uuid
from datetime import datetime
from typing import Dict, Any, List
from sqlalchemy import exc

logger = logging.getLogger(__name__)


def _extract_canonical_facts(
    context_type: str, resource: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Extract only the minimum canonical facts Sentinel needs.
    Does NOT store the full FHIR payload — avoids excessive PHI in fact_value_str.
    Stores only: resource_type, id, status, last_updated, source_version.
    """
    meta = resource.get("meta", {})
    facts = {
        "resource_type": context_type,
        "resource_id": resource.get("id", ""),
        "status": resource.get("status", ""),
        "last_updated": meta.get("lastUpdated", ""),
        "version_id": meta.get("versionId", ""),
    }
    # Add resource-type-specific minimal fields
    if context_type == "Patient":
        names = resource.get("name")
        if isinstance(names, list) and names and isinstance(names[0], dict):
            facts["family"] = names[0].get("family", "")
        elif isinstance(names, str):
            facts["family"] = names
        else:
            facts["family"] = ""
        facts["birth_date"] = resource.get("birthDate", "")
    elif context_type == "Encounter":
        facts["class"] = resource.get("class", {}).get("code", "")
        facts["period_start"] = resource.get("period", {}).get("start", "")
        facts["subject_reference"] = resource.get("subject", {}).get("reference", "")
    elif context_type == "Coverage":
        facts["payor"] = (
            resource.get("payor", [{}])[0].get("reference", "")
            if resource.get("payor") else ""
        )
        facts["beneficiary_reference"] = resource.get("beneficiary", {}).get("reference", "")
    return facts


def _compute_source_version(resource: Dict[str, Any]) -> str:
    """
    Determines the most reliable source version for idempotency.
    Priority: versionId > lastUpdated > deterministic payload hash.
    Prevents duplicate ingestion even when lastUpdated is absent.
    """
    meta = resource.get("meta", {})
    version_id = meta.get("versionId", "")
    last_updated = meta.get("lastUpdated", "")

    if version_id:
        return f"vid:{version_id}"
    if last_updated:
        return f"ts:{last_updated}"

    # Fallback: deterministic hash of the sorted payload
    payload_bytes = json.dumps(resource, sort_keys=True).encode()
    return f"hash:{hashlib.sha256(payload_bytes).hexdigest()[:16]}"


class CanonicalIngressService:
    """
    Canonical Ingress Service (Evidence Input).
    Handles ingestion of canonical evidence records from external connectors.
    Separates raw data fetch from operational event generation (no auto D7 events).
    Implements strong idempotency: connector_id + resource_type + resource_id + source_version.
    Stores only minimum canonical facts — not raw FHIR payloads.
    """

    def __init__(self):
        self.logger = logging.getLogger("CanonicalIngress")

    async def submit_batch(
        self, connector_id: str, records: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Accepts a batch of canonical evidence context records.
        Implements idempotency via fact_key (source_hash).
        """
        if not records:
            return {"status": "Success", "processed": 0}

        from app.models.evidence import EvidenceModel
        from app.db.database import SessionLocal

        processed = 0
        with SessionLocal() as db:
            for r in records:
                context_type = r.get("context_type", "Unknown")
                detail = r.get("detail", {})
                resource_id = detail.get("id", "")

                # Robust source version: versionId > lastUpdated > payload hash
                source_version = _compute_source_version(detail)

                # Idempotency key: connector + type + id + version
                source_key_str = (
                    f"{connector_id}_{context_type}_{resource_id}_{source_version}"
                )
                fact_key = hashlib.sha256(source_key_str.encode()).hexdigest()

                # Deduplication check
                existing = db.query(EvidenceModel).filter(
                    EvidenceModel.source_connector == connector_id,
                    EvidenceModel.fact_key == fact_key,
                ).first()

                if existing:
                    continue  # ALREADY_PROCESSED — idempotent

                # Extract only minimum-necessary canonical facts
                canonical_facts = _extract_canonical_facts(context_type, detail)
                # Store as compact JSON — NOT str(full_fhir_resource)
                fact_value = json.dumps(canonical_facts)

                evidence_id = f"evd_{uuid.uuid4().hex}"
                evidence = EvidenceModel(
                    evidence_id=evidence_id,
                    canonical_entity=context_type,
                    fact_key=fact_key,
                    fact_value_str=fact_value,
                    source_connector=connector_id,
                    retrieval_timestamp=datetime.utcnow(),
                    evidence_type="Sync",
                    metadata_json=json.dumps({
                        "source_version": source_version,
                        "resource_id": resource_id,
                    }),
                )
                try:
                    with db.begin_nested():
                        db.add(evidence)
                    processed += 1
                except exc.IntegrityError:
                    pass  # Idempotent duplicate

            db.commit()

        duplicates_skipped = len(records) - processed
        self.logger.info(
            f"[{connector_id}] Successfully ingested {processed} canonical records ({duplicates_skipped} duplicates skipped)."
        )
        return {
            "status": "Success",
            "processed": processed,
            "duplicates_skipped": duplicates_skipped,
            "connector_id": connector_id,
        }


canonical_ingress = CanonicalIngressService()
