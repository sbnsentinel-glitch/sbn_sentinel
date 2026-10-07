import logging
import uuid
from typing import Dict, Any
from app.services.base_service import BaseService
from app.services.governance_registry import (
    governance_registry, RecommendationStatus, DecisionType,
    DecisionStatus, HumanDecisionRecord, ContinuityViolationError
)

logger = logging.getLogger(__name__)


class HumanDecisionEngine(BaseService):
    """
    SESR-005 Compliant Human Decision Engine.
    Validates authority, recommendation state, and records human decisions.
    """

    @property
    def service_name(self) -> str:
        return "HumanDecisionEngine"

    @property
    def version(self) -> str:
        return "v1.0"

    def _process(self, input_data: Any) -> Any:
        return None

    def record_decision(
        self,
        actor_id: str,
        actor_role: str,
        recommendation_id: str,
        decision_type_str: str,
        reason: str = None
    ) -> Dict[str, Any]:
        """
        Takes decision attempt payload and returns either success or failure.
        """
        # Journey ID is extracted securely from the DB record later.
        # SESR-008: Reject client-provided journey_ids.

        # 1. Basic Validation
        if not all([actor_id, actor_role, recommendation_id, decision_type_str]):
            return {"status": "ERROR", "message": "Missing required decision fields."}

        try:
            decision_type = DecisionType(decision_type_str.upper())
        except ValueError:
            return {"status": "ERROR", "message": f"Invalid decision type: {decision_type_str}"}

        # 2. Re-Validate Recommendation (HDA-015, HDA-018)
        rec_record = governance_registry.get_recommendation(recommendation_id)
        if not rec_record:
            return {"status": "ERROR", "message": f"Recommendation {recommendation_id} not found."}

        if rec_record.status != RecommendationStatus.ACTIVE:
            return {"status": "ERROR",
                    "message": f"Recommendation is not decision-eligible. Status: {rec_record.status.value}"}

        # Extract authentic journey identity (Fix for Issue #1 - broken chain)
        journey_id = rec_record.journey_id

        # 3. Validate Authority (HDA-003, HDA-004, HDA-005, HDA-009)
        auth_config = governance_registry.get_authority_config(actor_role)
        if not auth_config:
            # Safe Default Deny (ADG-012)
            logger.warning(
                f"[HumanDecisionEngine] UNAUTHORIZED: No authority config for role {actor_role}.")
            return {"status": "ERROR", "message": "NOT AUTHORIZED."}

        if decision_type not in auth_config.allowed_decisions:
            logger.warning(
                f"[HumanDecisionEngine] UNAUTHORIZED: Role {actor_role} cannot make decision {decision_type.value}.")
            return {"status": "ERROR", "message": "NOT AUTHORIZED FOR THIS DECISION TYPE."}

        is_override = decision_type == DecisionType.OVERRIDDEN
        if is_override and not auth_config.can_override:
            logger.warning(
                f"[HumanDecisionEngine] UNAUTHORIZED: Role {actor_role} attempted override without permission.")
            return {"status": "ERROR", "message": "OVERRIDE PERMISSION REQUIRED."}

        # 4. Enforce Reason Requirements (HDA-021)
        if decision_type in auth_config.requires_reason_for and not reason:
            return {
                "status": "ERROR",
                "message": f"A reason is required for decision: {decision_type.value}"}

        # 5. Idempotency / Concurrency Check (HDA-025, HDA-026)
        # Query durable HumanDecisionModel first so restart/retry cannot create a second current Decision
        from app.db.database import SessionLocal
        from app.models.governance_storage import HumanDecisionModel

        db = SessionLocal()
        try:
            durable_decisions = db.query(HumanDecisionModel).filter(
                HumanDecisionModel.recommendation_id == recommendation_id,
                HumanDecisionModel.status == "RECORDED"
            ).all()
        finally:
            db.close()

        if durable_decisions:
            for d in durable_decisions:
                if d.actor_id == actor_id and d.decision_type == decision_type.value:
                    logger.info("[HumanDecisionEngine] Idempotent retry detected from durable storage.")
                    return {
                        "status": "SUCCESS",
                        "decision_id": d.decision_id,
                        "message": "Decision already recorded."
                    }
            return {
                "status": "ERROR",
                "message": "A current decision already exists for this recommendation."
            }

        # Fallback check in-memory if not committed yet
        existing_decisions = [d for d in governance_registry._human_decisions if d.recommendation_id ==
                              recommendation_id and d.status == DecisionStatus.RECORDED]
        if existing_decisions:
            for d in existing_decisions:
                if d.actor_id == actor_id and d.decision_type == decision_type:
                    logger.info("[HumanDecisionEngine] Idempotent retry detected.")
                    return {
                        "status": "SUCCESS",
                        "decision_id": d.decision_id,
                        "message": "Decision already recorded."}

            return {
                "status": "ERROR",
                "message": "A current decision already exists for this recommendation."}

        # 5b. SESR-008: Validate upstream continuity before persisting this decision
        try:
            governance_registry.validate_upstream_continuity(
                child_journey_id=journey_id,
                parent_id=recommendation_id,
                parent_type="recommendation"
            )
        except ContinuityViolationError as cve:
            logger.error(f"[SESR-008][HumanDecisionEngine] Continuity violation: {cve}")
            return {"status": "ERROR", "message": f"CONTINUITY_VIOLATION: {str(cve)}"}

        # 6. Create and Persist Human Decision Record (HDO-001)
        decision_id = f"HD-{uuid.uuid4().hex[:8].upper()}"
        decision_record = HumanDecisionRecord(
            decision_id=decision_id,
            recommendation_id=recommendation_id,
            actor_id=actor_id,
            decision_type=decision_type,
            authority_basis=f"Role:{actor_role}",
            status=DecisionStatus.RECORDED,
            reason=reason,
            override_indicator=is_override,
            # SESR-008: Propagate journey identity
            journey_id=journey_id
        )

        try:
            governance_registry.record_human_decision(decision_record)
        except Exception as e:
            logger.warning(f"[HumanDecisionEngine] Atomic constraint failed: {e}")
            return {"status": "ERROR", "message": str(e)}

        return {
            "status": "SUCCESS",
            "decision_id": decision_id,
            "message": "Human decision successfully governed and recorded."
        }


human_decision_engine = HumanDecisionEngine()
