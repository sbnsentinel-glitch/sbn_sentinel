import logging
import uuid
from typing import Dict, Any, Optional
from datetime import datetime

from app.services.base_service import BaseService
from app.services.governance_registry import (
    governance_registry,
    ActionType, OutcomeConfirmationState, OutcomeResolutionState,
    OperationalOutcomeRecord, ContinuityViolationError
)

logger = logging.getLogger(__name__)


class OperationalOutcomeEngine(BaseService):
    """
    SESR-007 Compliant Operational Outcome Engine.
    Validates outcomes, determines confirmation state, and manages closure.
    """

    @property
    def service_name(self) -> str:
        return "OperationalOutcomeEngine"

    @property
    def version(self) -> str:
        return "v1.0"

    def _process(self, input_data: Any) -> Any:
        return None

    def _determine_expected_outcome(self, action: Any) -> Dict[str, Any]:
        """
        Derives the deterministic expected outcome from the governed action parameters.
        """
        if action.action_type == ActionType.RESCHEDULE_APPOINTMENT:
            return {
                "target": action.target_reference,
                "status": "RESCHEDULED",
                "new_time": action.parameters.get("new_time")
            }
        elif action.action_type == ActionType.SEND_NOTIFICATION:
            return {
                "target": action.target_reference,
                "status": "DELIVERED"
            }
        # Fallback
        return {"target": action.target_reference}

    def _evaluate_match(self, expected: Dict[str, Any],
                        observed: Dict[str, Any]) -> OutcomeConfirmationState:
        """
        Deterministically evaluates if observed matches expected.
        """
        if not observed:
            return OutcomeConfirmationState.UNKNOWN

        # Basic exact match on keys that exist in expected
        for k, v in expected.items():
            if k not in observed or observed[k] != v:
                # E.g., if observed target doesn't match expected target
                return OutcomeConfirmationState.MISMATCH

        return OutcomeConfirmationState.CONFIRMED

    def process_outcome(self,
                        action_id: str,
                        observed_outcome: Dict[str,
                                               Any],
                        source_reference: Optional[str] = None) -> Dict[str,
                                                                        Any]:
        """
        Processes an incoming observation event against an operational action.
        """
        # 1. Fetch related action
        action = governance_registry.get_operational_action(action_id)
        if not action:
            return {"status": "ERROR", "message": f"Action {action_id} not found."}

        # 2. Check if outcome record already exists
        existing_outcome = governance_registry.get_operational_outcome_by_action(action_id)

        # Stale Event Protection (Idempotency)
        if existing_outcome:
            if existing_outcome.resolution_state == OutcomeResolutionState.RESOLVED:
                # Immutable once resolved
                return {
                    "status": "IGNORED",
                    "message": "Outcome is already resolved. Ignored stale event.",
                    "outcome_id": existing_outcome.outcome_id
                }

            # If already confirmed but unresolved (e.g. pending manual review)
            if existing_outcome.confirmation_state == OutcomeConfirmationState.CONFIRMED:
                return {
                    "status": "IGNORED",
                    "message": "Outcome is already confirmed. Ignoring redundant event.",
                    "outcome_id": existing_outcome.outcome_id
                }

        # 3. Determine Expected Outcome
        expected_outcome = self._determine_expected_outcome(action)

        # SESR-008: Derive journey_id from parent action — outcome belongs to the same journey
        journey_id = action.journey_id

        # SESR-008: Validate upstream continuity: outcome -> action
        try:
            governance_registry.validate_upstream_continuity(
                child_journey_id=journey_id,
                parent_id=action_id,
                parent_type="action"
            )
        except ContinuityViolationError as cve:
            logger.error(f"[SESR-008][OperationalOutcomeEngine] Continuity violation: {cve}")
            return {"status": "ERROR", "message": f"CONTINUITY_VIOLATION: {str(cve)}"}

        # 4. Compare
        confirmation_state = self._evaluate_match(expected_outcome, observed_outcome)

        # 5. Determine Resolution
        resolution_state = OutcomeResolutionState.OPEN
        closure_reason = None
        closed_at = None

        from app.services.governance_registry import ActionStatus
        
        if confirmation_state == OutcomeConfirmationState.CONFIRMED and action.status == ActionStatus.COMPLETED:
            resolution_state = OutcomeResolutionState.RESOLVED
            closure_reason = "SUCCESS_CONFIRMED"
            closed_at = datetime.utcnow()
        elif confirmation_state == OutcomeConfirmationState.MISMATCH or action.status == ActionStatus.FAILED:
            resolution_state = OutcomeResolutionState.UNRESOLVED
            closure_reason = "CONFIRMED_MISMATCH" if confirmation_state == OutcomeConfirmationState.MISMATCH else "ACTION_FAILED"

        # 6. Save or Update Record
        if existing_outcome:
            outcome_id = existing_outcome.outcome_id
            governance_registry.update_operational_outcome(
                outcome_id,
                observed_outcome=observed_outcome,
                confirmation_state=confirmation_state,
                resolution_state=resolution_state,
                closure_reason=closure_reason,
                closed_at=closed_at,
                source_reference=source_reference,
                confirmed_at=datetime.utcnow() if confirmation_state != OutcomeConfirmationState.PENDING else None)
        else:
            outcome_id = f"OUT-{uuid.uuid4().hex[:8].upper()}"
            record = OperationalOutcomeRecord(
                outcome_id=outcome_id,
                action_id=action_id,
                expected_outcome=expected_outcome,
                observed_outcome=observed_outcome,
                confirmation_state=confirmation_state,
                resolution_state=resolution_state,
                closure_reason=closure_reason,
                source_reference=source_reference,
                confirmed_at=datetime.utcnow() if confirmation_state != OutcomeConfirmationState.PENDING else None,
                closed_at=closed_at,
                # SESR-008: Propagate journey identity from action
                journey_id=journey_id
            )
            governance_registry.record_operational_outcome(record)

        return {
            "status": "SUCCESS",
            "outcome_id": outcome_id,
            "confirmation_state": confirmation_state.value,
            "resolution_state": resolution_state.value
        }


operational_outcome_engine = OperationalOutcomeEngine()
