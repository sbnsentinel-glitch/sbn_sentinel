"""
SESR-001: Evidence Engine — Core Architecture Governance
Mandatory Implementation Standards for the Evidence Engine.

Runtime Sequence:
External Evidence -> ERP -> ERRM -> EVP -> Classification -> EOS-003 -> Decision Context Engine
"""
import json
from app.models.evidence import EvidenceModel
from app.db.database import SessionLocal
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from dataclasses import dataclass, field
from app.services.base_service import BaseService
import uuid

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# EOS-001: EVIDENCE OBJECT
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OperationalEvidence:
    """
    EOS-001: Evidence Object
    Immutable representation of an operational fact.
    """
    evidence_id: str
    canonical_entity: str
    fact_key: str
    fact_value: Any
    source_connector: str
    retrieval_timestamp: datetime
    evidence_type: Optional[str] = None  # Assigned during Classification
    metadata: Dict[str, Any] = field(default_factory=dict)
    version: int = 1
    previous_version_id: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# EOS-002: EVIDENCE REPOSITORY (ERRM)
# ─────────────────────────────────────────────────────────────────────────────


class EvidenceRepository:
    """
    EOS-002: Single source of truth for Evidence Objects.
    Manages storage and versioning using SQLAlchemy.
    """

    def store(self, evidence: OperationalEvidence):
        db = SessionLocal()
        try:
            db_fact_key = f"{evidence.fact_key}::{evidence.evidence_id}" if "::" not in evidence.fact_key else evidence.fact_key

            # 1. Check if exists by exact evidence_id
            existing = db.query(EvidenceModel).filter(
                EvidenceModel.evidence_id == evidence.evidence_id
            ).first()
            if existing:
                existing.canonical_entity = evidence.canonical_entity
                existing.fact_key = db_fact_key
                existing.fact_value_str = str(
                    evidence.fact_value) if evidence.fact_value is not None else None
                existing.source_connector = evidence.source_connector
                existing.retrieval_timestamp = evidence.retrieval_timestamp
                existing.evidence_type = evidence.evidence_type
                existing.metadata_json = json.dumps(
                    evidence.metadata) if evidence.metadata else None
                existing.version = evidence.version
                existing.previous_version_id = evidence.previous_version_id
                db.commit()
                return

            # 2. Add new record
            new_model = EvidenceModel(
                evidence_id=evidence.evidence_id,
                canonical_entity=evidence.canonical_entity,
                fact_key=db_fact_key,
                fact_value_str=str(
                    evidence.fact_value) if evidence.fact_value is not None else None,
                source_connector=evidence.source_connector,
                retrieval_timestamp=evidence.retrieval_timestamp,
                evidence_type=evidence.evidence_type,
                metadata_json=json.dumps(
                    evidence.metadata) if evidence.metadata else None,
                version=evidence.version,
                previous_version_id=evidence.previous_version_id)
            db.add(new_model)
            db.commit()
            logger.debug(f"[ERRM] Stored evidence {evidence.evidence_id} in DB")
        except Exception as e:
            db.rollback()
            if "UNIQUE constraint failed" in str(e) or "IntegrityError" in str(type(e).__name__):
                # Handle IntegrityError as idempotent duplicate
                try:
                    existing = db.query(EvidenceModel).filter(
                        EvidenceModel.source_connector == evidence.source_connector,
                        EvidenceModel.fact_key == db_fact_key
                    ).first()
                    if existing:
                        existing.fact_value_str = str(evidence.fact_value) if evidence.fact_value is not None else None
                        existing.retrieval_timestamp = evidence.retrieval_timestamp
                        if evidence.evidence_type:
                            existing.evidence_type = evidence.evidence_type
                        if evidence.metadata:
                            existing.metadata_json = json.dumps(evidence.metadata)
                        db.commit()
                        logger.debug(f"[ERRM] Idempotently updated evidence {evidence.evidence_id} in DB")
                        return
                except Exception:
                    db.rollback()
            logger.error(f"[ERRM] Failed to store evidence {evidence.evidence_id}: {e}")
            from app.core.exceptions import PersistenceError
            raise PersistenceError(f"Database error storing evidence: {e}")
        finally:
            db.close()

    def retrieve(self, evidence_id: str) -> Optional[OperationalEvidence]:
        db = SessionLocal()
        try:
            model = db.query(EvidenceModel).filter(EvidenceModel.evidence_id == evidence_id).first()
            if model:
                raw_fact_key = model.fact_key.split("::")[0] if model.fact_key and "::" in model.fact_key else model.fact_key
                return OperationalEvidence(
                    evidence_id=model.evidence_id,
                    canonical_entity=model.canonical_entity,
                    fact_key=raw_fact_key,
                    fact_value=model.fact_value_str,
                    source_connector=model.source_connector,
                    retrieval_timestamp=model.retrieval_timestamp,
                    evidence_type=model.evidence_type,
                    metadata=json.loads(model.metadata_json) if model.metadata_json else {},
                    version=model.version,
                    previous_version_id=model.previous_version_id
                )
            return None
        finally:
            db.close()


evidence_repository = EvidenceRepository()


# ─────────────────────────────────────────────────────────────────────────────
# EOS-003: EVIDENCE STATUS PACKAGE
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class EvidenceStatusPackage:
    """
    EOS-003: Evidence Status Package
    The ONLY output of the Evidence Engine. Sent to DCE.
    """
    package_id: str
    event_id: str
    evidence_references: List[str]  # List of evidence_ids
    validation_results: Dict[str, bool]
    classification_results: Dict[str, str]
    processing_status: str
    missing_evidence: List[str] = field(default_factory=list)
    evidence_conflicts: List[str] = field(default_factory=list)
    freshness_status: Dict[str, Any] = field(default_factory=dict)
    evidence_statuses: Dict[str, str] = field(default_factory=dict)
    retrieval_failures: List[Dict[str, str]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# EVIDENCE ENGINE RUNTIME
# ─────────────────────────────────────────────────────────────────────────────
class EvidenceEngine(BaseService):
    """
    SESR-001 Compliant Evidence Engine.
    Executes the strict runtime: ERP -> ERRM -> EVP -> Classification -> EOS-003.
    """

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.repository = evidence_repository

    @property
    def service_name(self) -> str:
        return "EvidenceEngine"

    @property
    def version(self) -> str:
        return "v1.0"

    def _process(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Entry point from processing_orchestrator.
        """
        canonical_event_data = payload.get("canonical_event", {})

        # ERP: Evidence Registration Pipeline
        raw_evidence_list = self._erp_registration(canonical_event_data)

        # ERRM: Evidence Repository Runtime Management
        for ev in raw_evidence_list:
            self.repository.store(ev)

        # EVP: Evidence Validation Pipeline
        validation_results = self._evp_validation(raw_evidence_list)

        # Classification (ETR)
        classified_evidence_list = self._classify_evidence(raw_evidence_list)

        # Update Repository with classified versions (Versioning Strategy)
        final_references = []
        classification_results = {}
        for ev in classified_evidence_list:
            self.repository.store(ev)
            final_references.append(ev.evidence_id)
            classification_results[ev.evidence_id] = ev.evidence_type

        missing = self._detect_missing(classified_evidence_list)
        stale_info = self._evaluate_freshness(classified_evidence_list)

        evidence_statuses = {}
        for ev in classified_evidence_list:
            if ev.evidence_id in stale_info.get("stale_records", []):
                evidence_statuses[ev.evidence_id] = "STALE"
            else:
                evidence_statuses[ev.evidence_id] = "USED"

        retrieval_failures = canonical_event_data.get("retrieval_failures", [])

        eos_003 = EvidenceStatusPackage(
            package_id=str(uuid.uuid4()),
            event_id=canonical_event_data.get("event_id", "UNKNOWN"),
            evidence_references=final_references,
            validation_results=validation_results,
            classification_results=classification_results,
            processing_status="Success",
            missing_evidence=missing,
            evidence_conflicts=self._detect_conflicts(classified_evidence_list),
            freshness_status=stale_info,
            evidence_statuses=evidence_statuses,
            retrieval_failures=retrieval_failures
        )

        self.logger.info(f"[EvidenceEngine] Generated EOS-003 Package: {eos_003.package_id}")

        # We return this dictionary mapping for the downstream orchestrator
        # The orchestrator will pass this to DCE
        return {"eos_003_package": eos_003}

    # --- Runtime Pipelines ---

    def _erp_registration(self, canonical_data: dict) -> List[OperationalEvidence]:
        """Creates initial EOS-001 objects."""
        from app.core.config import settings

        source = canonical_data.get("source_system", "Unknown")

        # Audit 3 Item 18: PF-Only Admission in Production
        if getattr(settings, "ENVIRONMENT", "DEVELOPMENT") == "PRODUCTION":
            if "practice_fusion" not in source.lower() and "practicefusion" not in source.lower() and source != "PF":
                self.logger.error(
                    f"[EvidenceEngine] Production admission blocked: Source {source} is not Practice Fusion.")
                raise ValueError(
                    "PRODUCTION_ADMISSION_REJECTED: Only Practice Fusion sources are permitted in V1 production.")

        evidence_items = []
        now = datetime.utcnow()
        metadata = canonical_data.get("canonical_metadata", {})
        detail = metadata.get("detail", "").lower()
        event_type = canonical_data.get("event_type", "Unknown")

        if event_type == "EHR":
            if "no-show" in detail:
                evidence_items.append(
                    self._create_eos_001(
                        "Appointment",
                        "status",
                        "NO_SHOW",
                        source,
                        now))
            elif "wait time" in detail:
                evidence_items.append(
                    self._create_eos_001(
                        "Appointment",
                        "status",
                        "WAIT_TIME_EXCEEDED",
                        source,
                        now))
            elif "booked" in detail or "appointment" in detail:
                evidence_items.append(
                    self._create_eos_001(
                        "Appointment",
                        "status",
                        "BOOKED",
                        source,
                        now))

        elif event_type == "Phone":
            if "missed call" in detail:
                evidence_items.append(
                    self._create_eos_001(
                        "PhoneInteraction",
                        "status",
                        "MISSED_CALL",
                        source,
                        now))

        elif event_type == "Email":
            if "lab" in detail:
                evidence_items.append(
                    self._create_eos_001(
                        "LabReport",
                        "status",
                        "PENDING_REVIEW",
                        source,
                        now))

        # Provenance
        evidence_items.append(
            self._create_eos_001(
                "OperationalEvent",
                "source",
                source,
                source,
                now
            )
        )
        # Check for explicit facts or custom evidence points
        extra_facts = metadata.get("facts", [])
        if isinstance(extra_facts, list):
            for f in extra_facts:
                if isinstance(f, dict):
                    evidence_items.append(
                        self._create_eos_001(
                            f.get("entity", "Appointment"),
                            f.get("key", "status"),
                            f.get("value"),
                            f.get("source", source),
                            now
                        )
                    )
        elif isinstance(extra_facts, dict):
            for k, v in extra_facts.items():
                evidence_items.append(
                    self._create_eos_001(
                        "Appointment",
                        k,
                        v,
                        source,
                        now
                    )
                )

        return evidence_items

    def _create_eos_001(
            self,
            entity: str,
            key: str,
            value: Any,
            source: str,
            ts: datetime) -> OperationalEvidence:
        return OperationalEvidence(
            evidence_id=str(uuid.uuid4()),
            canonical_entity=entity,
            fact_key=key,
            fact_value=value,
            source_connector=source,
            retrieval_timestamp=ts
        )

    def _evp_validation(self, evidence_list: List[OperationalEvidence]) -> Dict[str, bool]:
        """Validates structural completeness."""
        results = {}
        for ev in evidence_list:
            is_valid = bool(ev.canonical_entity and ev.fact_key and ev.source_connector)
            results[ev.evidence_id] = is_valid
            if not is_valid:
                self.logger.warning(f"[EVP] Validation failed for {ev.evidence_id}")
        return results

    def _classify_evidence(
            self,
            evidence_list: List[OperationalEvidence]) -> List[OperationalEvidence]:
        """Assigns EvidenceType from ETR and creates new versions."""
        classified = []
        for ev in evidence_list:
            ev_type = "OperationalEvent"
            if ev.canonical_entity == "Appointment":
                ev_type = "AppointmentStatus"
            elif ev.canonical_entity == "PhoneInteraction":
                ev_type = "CommunicationStatus"
            elif ev.canonical_entity == "LabReport":
                ev_type = "DocumentStatus"
            elif ev.canonical_entity == "OperationalEvent":
                ev_type = "EventProvenance"

            # Create new version (Immutability pattern)
            new_ev = OperationalEvidence(
                evidence_id=str(uuid.uuid4()),
                canonical_entity=ev.canonical_entity,
                fact_key=ev.fact_key,
                fact_value=ev.fact_value,
                source_connector=ev.source_connector,
                retrieval_timestamp=ev.retrieval_timestamp,
                evidence_type=ev_type,
                metadata=ev.metadata,
                version=ev.version + 1,
                previous_version_id=ev.evidence_id
            )
            classified.append(new_ev)

        return classified

    def _detect_missing(self, evidence_list: List[OperationalEvidence]) -> List[str]:
        found_entities = {ev.canonical_entity for ev in evidence_list}
        missing = []
        if "Appointment" not in found_entities and "OperationalEvent" not in found_entities:
            missing.append("No primary contextual entity (Appointment/Event) found in evidence.")
        return missing

    def _detect_conflicts(self, evidence_list: List[OperationalEvidence]) -> List[str]:
        facts = {}
        conflicts = []
        for ev in evidence_list:
            if ev.fact_key and ev.fact_value:
                if ev.fact_key in facts and facts[ev.fact_key] != ev.fact_value:
                    conflicts.append(
                        f"Conflict on {ev.fact_key}: {facts[ev.fact_key]} vs {ev.fact_value}")
                facts[ev.fact_key] = ev.fact_value
        return conflicts

    def _evaluate_freshness(self, evidence_list: List[OperationalEvidence]) -> Dict[str, Any]:
        from datetime import timedelta
        is_stale = False
        stale_records = []
        now = datetime.utcnow()
        for ev in evidence_list:
            if ev.retrieval_timestamp:
                if (now - ev.retrieval_timestamp) > timedelta(hours=24):
                    is_stale = True
                    stale_records.append(str(ev.evidence_id))
        return {"is_stale": is_stale, "stale_records": stale_records}


evidence_engine = EvidenceEngine()
