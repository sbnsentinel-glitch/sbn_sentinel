import uuid
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ContextBuilder:
    """
    AIS-002: Context Builder
    Responsible for assembling the Decision Context Package structure:
    Identity, Evidence, Operational Context, and Governance metadata.
    """

    def __init__(self, db_session):
        self.db = db_session

    def build(self, event_id: str, normalized_evidence: list, org_id: str = None, clinic_id: str = None, correlation_id: str = None) -> dict:
        """
        Builds the foundational Decision Context Package without generating recommendations.
        """
        logger.info(f"[{event_id}] ContextBuilder: Assembling Decision Context Package")

        # Derive from normalized evidence if not directly supplied
        if not org_id or not clinic_id:
            for item in normalized_evidence:
                if isinstance(item, dict):
                    org_id = org_id or item.get("organization_id") or item.get("org_id")
                    clinic_id = clinic_id or item.get("clinic_id")

        context_package = {
            "identity": {
                "context_id": str(uuid.uuid4()),
                "organization_id": org_id,
                "clinic_id": clinic_id,
                "correlation_id": correlation_id or event_id,
                "timestamp": datetime.utcnow().isoformat()
            },
            "evidence": {
                "used": normalized_evidence,
                "missing": [],  # Populated by ContextValidator
                "freshness": {},  # Populated by ContextValidator
                "provenance": {},  # Mapped here or in normalizer
                "conflicts": []  # Populated by ContextValidator
            },
            "operational_context": {
                "workflow": "Unknown",
                "current_state": "Pending Evaluation",
                "dependencies": [],
                "constraints": []
            },
            "governance": {
                "sufficiency_status": "Unevaluated",
                "governance_status": "Pending",
                "authority_boundary": "Unknown"
            }
        }

        # Stub logic to assign basic workflow state if obvious evidence exists
        for item in normalized_evidence:
            if isinstance(item, dict) and item.get("fact_value") == "BOOKED":
                context_package["operational_context"]["workflow"] = "Appointment Scheduling"

        return context_package
