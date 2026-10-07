from typing import Dict, Any
from app.services.base_service import BaseService


class DecisionContextEngine(BaseService):
    """
    MS-006 / SESR-001 Compliant Decision Context Engine (DCE).
    Consumes EOS-003 (EvidenceStatusPackage) from the Evidence Engine.
    Resolves evidence from the repository via evidence_references.
    No predictive AI or self-learning. No raw evidence access.
    """

    @property
    def service_name(self) -> str:
        return "DecisionContextEngine"

    @property
    def version(self) -> str:
        return "v1.0"

    def __init__(self):
        pass

    def _process(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        SESR-001: Classifies operational context from EOS-003 EvidenceStatusPackage.
        Payload expects: {"eos_003_package": EvidenceStatusPackage, "event_type": str}
        Resolves EvidenceObjects from the EvidenceRepository using evidence_references.
        """
        from app.services.evidence_engine import evidence_repository

        eos_003 = payload.get("eos_003_package")
        if not eos_003:
            raise ValueError(
                "[SESR-001] EOS-003 EvidenceStatusPackage is mandatory for DecisionContextEngine.")

        event_type = payload.get("event_type", "Unknown")

        # Resolve evidence objects from the repository using EOS-003 references
        evidence_items = []
        refs = eos_003.get(
            "evidence_references",
            []) if isinstance(
            eos_003,
            dict) else getattr(
            eos_003,
            "evidence_references",
            [])

        for ref_id in refs:
            ev = evidence_repository.retrieve(ref_id)
            if ev:
                evidence_items.append(ev)

        evidence_package = eos_003  # Pass EOS-003 as the package payload

        def get_fact_value(e):
            return getattr(
                e, 'fact_value', None) if not isinstance(
                e, dict) else e.get('fact_value')

        has_no_show = any(get_fact_value(e) == "NO_SHOW" for e in evidence_items)
        has_wait_time = any(get_fact_value(e) == "WAIT_TIME_EXCEEDED" for e in evidence_items)
        has_booked = any(get_fact_value(e) == "BOOKED" for e in evidence_items)
        has_missed_call = any(get_fact_value(e) == "MISSED_CALL" for e in evidence_items)
        has_pending_review = any(get_fact_value(e) == "PENDING_REVIEW" for e in evidence_items)

        # Default Context
        context = {
            "primary_context": "Context Unknown",
            "secondary_context": "Not Available",
            "reason": "Insufficient information to classify context.",
            "evidence_package": evidence_package,
            "event_type": event_type
        }

        # Check for Audit 3 Item 1 failures: STALE or TECHNICAL_RETRIEVAL_FAILURE
        retrieval_failures = getattr(
            eos_003,
            "retrieval_failures",
            []) if not isinstance(
            eos_003,
            dict) else eos_003.get(
            "retrieval_failures",
            [])
        evidence_statuses = getattr(
            eos_003,
            "evidence_statuses",
            {}) if not isinstance(
            eos_003,
            dict) else eos_003.get(
            "evidence_statuses",
            {})

        has_stale = any(status == "STALE" for status in evidence_statuses.values())

        if retrieval_failures:
            context = {
                "primary_context": "NOT_EVALUABLE",
                "secondary_context": "TECHNICAL_RETRIEVAL_FAILURE",
                "reason": f"Technical retrieval failure encountered: {retrieval_failures}",
                "evidence_package": evidence_package,
                "event_type": event_type
            }
            # Skip normal classification
            has_no_show = has_wait_time = has_booked = has_missed_call = has_pending_review = False

        elif has_stale:
            context = {
                "primary_context": "INSUFFICIENT",
                "secondary_context": "STALE_EVIDENCE",
                "reason": "One or more required operational facts are stale.",
                "evidence_package": evidence_package,
                "event_type": event_type
            }
            # Skip normal classification
            has_no_show = has_wait_time = has_booked = has_missed_call = has_pending_review = False

        if has_no_show:
            context = {
                "primary_context": "Operational",
                "secondary_context": "Provider Schedule Gap",
                "reason": "Appointment marked as No-Show creates immediate unused capacity in provider schedule.",
                "evidence_package": evidence_package,
                "event_type": event_type}
        elif has_wait_time:
            context = {
                "primary_context": "Operational",
                "secondary_context": "Queue Congestion",
                "reason": "Patient waiting >45 minutes indicates provider bottleneck or capacity surge.",
                "evidence_package": evidence_package,
                "event_type": event_type}
        elif has_booked:
            context = {
                "primary_context": "Administrative",
                "secondary_context": "Routine Booking",
                "reason": "Standard forward-looking schedule addition.",
                "evidence_package": evidence_package,
                "event_type": event_type
            }
        elif has_missed_call:
            context = {
                "primary_context": "Operational",
                "secondary_context": "Staff Overload",
                "reason": "Inbound communication failure indicates front-desk saturation or understaffing.",
                "evidence_package": evidence_package,
                "event_type": event_type}
        elif has_pending_review:
            context = {
                "primary_context": "Clinical Workflow",
                "secondary_context": "Documentation Dependency",
                "reason": "Lab report delivered but lacks provider signature for closure.",
                "evidence_package": evidence_package,
                "event_type": event_type
            }

        # AIS-002: Build New Decision Context Package
        from app.services.context_builder import ContextBuilder
        from app.services.context_validator import ContextValidator
        from app.services.context_serializer import ContextSerializer

        # We run this synchronously to avoid breaking the existing BaseService caller
        import asyncio  # noqa
        builder = ContextBuilder(None)
        validator = ContextValidator(None)
        serializer = ContextSerializer()

        class DecisionContextInvalid(Exception):
            pass

        new_package = builder.build(event_type, evidence_items)
        validated = validator.validate(new_package)

        # F-12: Hard gate
        if hasattr(validated, "is_valid") and not validated.is_valid:
            raise DecisionContextInvalid(getattr(validated, "errors", "Validation failed"))

        serialized_package = serializer.serialize(validated)

        # Inject AIS-002 Package into the legacy response to avoid breaking downstream
        context["ais_002_decision_context_package"] = serialized_package

        from fastapi.encoders import jsonable_encoder
        return jsonable_encoder(context)


decision_context_engine = DecisionContextEngine()
