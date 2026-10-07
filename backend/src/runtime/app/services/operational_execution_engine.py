import logging
import uuid
from typing import Dict, Any
from datetime import datetime
from app.services.base_service import BaseService
from app.services.governance_registry import (
    governance_registry,
    ActionType, ActionStatus, ExecutionResult,
    OperationalActionRecord, ExecutionAttemptRecord, DecisionStatus,
    ContinuityViolationError
)
from app.core.config import settings

logger = logging.getLogger(__name__)


class OperationalExecutionEngine(BaseService):
    """
    SESR-006 Compliant Operational Execution Engine.
    Validates eligibility and attempts governed execution.
    """

    @property
    def service_name(self) -> str:
        return "OperationalExecutionEngine"

    @property
    def version(self) -> str:
        return "v1.0"

    def _process(self, input_data: Any) -> Any:
        # Required by BaseService
        return None

    def create_action(self, decision_id: str, action_type_str: str, target_reference: str,
                      parameters: Dict[str, Any], initiator_scope: Dict[str, Any] = None, intent_hash: str = None) -> Dict[str, Any]:
        """Creates an operational action based on an approved decision."""
        # Validate decision exists and is RECORDED
        decision = governance_registry.get_human_decision(decision_id)
        if not decision:
            return {"status": "ERROR", "message": f"Decision {decision_id} not found."}

        if decision.status != DecisionStatus.RECORDED:
            return {
                "status": "ERROR",
                "message": f"Decision is not in a valid state for action creation. Status: {decision.status.value}"}

        from app.services.governance_registry import DecisionType
        if decision.decision_type in [DecisionType.REJECTED, DecisionType.RETURNED_FOR_REVIEW]:
            return {
                "status": "ERROR",
                "message": f"Cannot create action for decision type: {decision.decision_type.value}"}

        try:
            # F-26: Exact enum lookup without case conversion
            action_type = ActionType(action_type_str)
        except ValueError:
            return {"status": "ERROR", "message": f"Invalid action type: {action_type_str}"}

        # P0-05 / P0-06 / SESR-005: Trusted Scope Enforcement (Item 1)
        if initiator_scope:
            from app.db.database import SessionLocal
            from app.models.encounter import EncounterModel
            from app.models.organization import OrganizationClinicModel

            db = SessionLocal()
            try:
                # Resolve authoritative target ownership
                target_org_id = None
                target_clinic_id = None
                target_journey_id = None

                # F-23: Explicit scope verification before processing mock postfix
                base_target = target_reference
                if getattr(settings, "SYNTHETIC_TEST_ENABLED", False):
                    for postfix in ["-FAIL", "-UNKNOWN", "-UNAVAILABLE"]:
                        if target_reference.endswith(postfix):
                            base_target = target_reference[:-len(postfix)]
                            break

                encounter = db.query(EncounterModel).filter(EncounterModel.id == base_target).first()
                if encounter:
                    target_clinic_id = encounter.clinic_id
                    target_journey_id = getattr(encounter, "journey_id", None)
                    if target_clinic_id:
                        org_clinic = db.query(OrganizationClinicModel).filter(
                            OrganizationClinicModel.id == target_clinic_id).first()
                        if org_clinic:
                            target_org_id = org_clinic.organization_id
                else:
                    # Check if target is a clinic directly
                    org_clinic = db.query(OrganizationClinicModel).filter(
                        OrganizationClinicModel.id == base_target).first()
                    if org_clinic:
                        target_org_id = org_clinic.organization_id
                        target_clinic_id = org_clinic.id

                # Fail-Closed Invariant: target must be positively resolved
                if not target_org_id:
                    return {
                        "status": "ERROR",
                        "message": f"TARGET_NOT_FOUND_OR_UNSUPPORTED: Target '{target_reference}' cannot be authoritatively resolved."
                    }

                org_scope = initiator_scope.get("org_id")
                clinic_scope = initiator_scope.get("clinic_id")

                if not org_scope or org_scope == "UNASSIGNED":
                    return {"status": "ERROR", "message": "MISSING_SCOPE: Initiator lacks required organization scope."}

                if org_scope != "SYSTEM_GLOBAL":
                    if target_org_id != org_scope:
                        return {
                            "status": "ERROR",
                            "message": f"CROSS_ORG: Action target (true_org={target_org_id}) is outside user's governed scope ({org_scope})."
                        }
                    if clinic_scope and target_clinic_id and target_clinic_id != clinic_scope:
                        return {
                            "status": "ERROR",
                            "message": f"CROSS_CLINIC: Action target (true_clinic={target_clinic_id}) is outside user's clinic scope ({clinic_scope})."
                        }

                if target_journey_id and decision.journey_id and target_journey_id != decision.journey_id:
                    return {
                        "status": "ERROR",
                        "message": f"TARGET_JOURNEY_MISMATCH: Target journey {target_journey_id} does not match decision journey {decision.journey_id}."
                    }
            finally:
                db.close()

        # SESR-008: Inherit journey_id from the authorizing decision
        journey_id = decision.journey_id

        # SESR-008: Validate continuity chain (action must belong to the same
        # journey as the decision)
        try:
            governance_registry.validate_upstream_continuity(
                child_journey_id=journey_id,
                parent_id=decision_id,
                parent_type="decision"
            )
        except ContinuityViolationError as cve:
            logger.error(
                f"[SESR-008][OperationalExecutionEngine] Continuity violation on create_action: {cve}")
            return {"status": "ERROR", "message": f"CONTINUITY_VIOLATION: {str(cve)}"}

        action_id = f"ACT-{uuid.uuid4().hex[:8].upper()}"
        action = OperationalActionRecord(
            action_id=action_id,
            action_type=action_type,
            target_reference=target_reference,
            authorization_reference=decision_id,
            parameters=parameters,
            status=ActionStatus.READY,
            current_result=ExecutionResult.NOT_ATTEMPTED,
            # SESR-008: Propagate journey identity
            journey_id=journey_id,
            intent_hash=intent_hash
        )

        governance_registry.record_operational_action(action)
        return {"status": "SUCCESS", "action_id": action_id}

    def evaluate_action_capabilities(self, status_str: str, result_str: str, attempt_count: int) -> Dict[str, Any]:
        """
        Computes current capability signals from state.
        These are READ signals — they tell the frontend what is currently possible.
        """
        can_execute = status_str in ("READY", "CREATED")

        # D6 Correction: UNKNOWN must not expose retry to prevent duplicate external side effects
        terminal_statuses = {"COMPLETED", "CANCELLED", "EXPIRED", "BLOCKED"}
        can_retry = (
            status_str not in terminal_statuses
            and result_str in ("FAILED", "PARTIAL")  # UNKNOWN explicitly removed
            and attempt_count > 0
            and attempt_count < 3  # Enforce max 3 retries
        )

        blocked_reason = None
        if status_str == "BLOCKED":
            blocked_reason = "Action is in BLOCKED state — review governance constraints before retrying."
        elif status_str == "EXPIRED":
            blocked_reason = "Action has expired — a new action must be authorized via a new Human Decision."
        elif status_str == "CANCELLED":
            blocked_reason = "Action was cancelled and cannot be executed."
        elif attempt_count >= 3:
            blocked_reason = "MAX_RETRIES exceeded. Action is permanently blocked."

        return {
            "can_execute": can_execute,
            "can_retry": can_retry,
            "blocked_reason": blocked_reason,
        }

    def _pre_execution_validation(self, action: OperationalActionRecord, attempts_count: int) -> Dict[str, Any]:
        """EXV-001 to EXV-039: Execution Eligibility Controls"""
        caps = self.evaluate_action_capabilities(
            status_str=action.status.value,
            result_str=action.current_result.value,
            attempt_count=attempts_count
        )

        if not caps["can_execute"] and not caps["can_retry"]:
            return {
                "eligible": False,
                "reason": caps["blocked_reason"] or f"NOT_ELIGIBLE (Status: {action.status.value}, Result: {action.current_result.value})"
            }

        # EXV-004: Validate required authorization
        decision = governance_registry.get_human_decision(action.authorization_reference)
        if not decision:
            return {"eligible": False, "reason": "AUTHORIZATION_INVALID"}

        # EXV-007: Invalidated Authorization
        if decision.status != DecisionStatus.RECORDED:
            if decision.status.value in ["REVOKED", "EXPIRED", "REJECTED"]:
                return {"eligible": False, "reason": f"AUTHORIZATION_REVOKED (Status: {decision.status.value})"}
            return {
                "eligible": False,
                "reason": f"AUTHORIZATION_NOT_RECORDED (Status: {decision.status.value})"}

        # EXV-009: Action expiration
        if action.execute_by and datetime.utcnow() > action.execute_by:
            return {"eligible": False, "reason": "ACTION_EXPIRED"}

        # EXV-010: Validate target reference exists
        from app.db.database import SessionLocal
        from app.models.encounter import EncounterModel
        from app.models.organization import OrganizationClinicModel

        db = SessionLocal()
        try:
            target_exists = False
            base_target = action.target_reference
            if getattr(settings, "SYNTHETIC_TEST_ENABLED", False):
                for postfix in ["-FAIL", "-UNKNOWN", "-UNAVAILABLE"]:
                    if action.target_reference.endswith(postfix):
                        base_target = action.target_reference[:-len(postfix)]
                        break

            if db.query(EncounterModel).filter(EncounterModel.id == base_target).first():
                target_exists = True
            elif db.query(OrganizationClinicModel).filter(OrganizationClinicModel.id == base_target).first():
                target_exists = True

            if not target_exists:
                return {"eligible": False, "reason": "TARGET_NOT_FOUND_OR_UNSUPPORTED"}
        finally:
            db.close()

        return {"eligible": True}

    def execute_action(self, action_id: str) -> Dict[str, Any]:
        """
        Attempts to execute the action if eligible.
        """
        action = governance_registry.get_operational_action(action_id)
        if not action:
            return {"status": "ERROR", "message": f"Action {action_id} not found."}

        # 1. Pre-execution Validation
        terminal_or_uncertain = [ActionStatus.COMPLETED, ActionStatus.CANCELLED, ActionStatus.FAILED, ActionStatus.EXPIRED]

        attempts = governance_registry.get_execution_attempts(action_id)
        attempt_number = len(attempts) + 1

        eligibility = self._pre_execution_validation(action, attempts_count=len(attempts))
        if not eligibility["eligible"]:
            if action.status in terminal_or_uncertain:
                logger.warning(f"Rejected execute request for terminal action {action_id}: {eligibility['reason']}")
                return {"status": "REJECTED", "current_status": action.status.value, "message": f"Execution rejected: {eligibility['reason']}"}

            new_status = ActionStatus.EXPIRED if eligibility["reason"] == "ACTION_EXPIRED" else ActionStatus.BLOCKED
            governance_registry.update_operational_action(
                action_id, new_status, ExecutionResult.NOT_ATTEMPTED, receipt=None)
            return {"status": "REJECTED", "current_status": new_status.value, "message": f"Execution rejected: {eligibility['reason']}"}

        # Atomic claim
        if not governance_registry.claim_operational_action(action_id):
            return {"status": "ERROR", "message": "Execution claiming failed. Action may already be executing."}

        # Reload action after claim
        action = governance_registry.get_operational_action(action_id)

        # 2. Execution Attempt
        attempt_id = f"ATT-{uuid.uuid4().hex[:6].upper()}"

        logger.info(
            f"[{self.service_name}] Executing Attempt {attempt_number} for Action {action_id}")

        # MOCK CONNECTOR EXECUTION (AEX-020 to AEX-024)
        if getattr(settings, "SYNTHETIC_TEST_ENABLED", False):
            mock_result = self._mock_connector_call(action)
        else:
            # In V1 production, real connector logic goes here.
            # If not implemented, it fails securely rather than spoofing success.
            # Audit 3 Item 5: Use NOT_ATTEMPTED/BLOCKED for missing executors
            mock_result = {
                "result": ExecutionResult.NOT_ATTEMPTED,
                "error": "NOT_IMPLEMENTED",
                "message": "Real connector not implemented for V1 production."}

        # D6 Correction: Do not persist MOCK_PRACTICE_FUSION_CONNECTOR outside synthetic mode
        actual_connector = "MOCK_PRACTICE_FUSION_CONNECTOR" if getattr(settings, "SYNTHETIC_TEST_ENABLED", False) else "UNAVAILABLE"

        # Record Attempt
        attempt = ExecutionAttemptRecord(
            attempt_id=attempt_id,
            action_id=action_id,
            attempt_number=attempt_number,
            connector=actual_connector,
            result=mock_result["result"],
            error_message=mock_result.get("error"),
            # SESR-008: Propagate journey identity from action
            journey_id=action.journey_id
        )
        governance_registry.record_execution_attempt(attempt)

        # 3. Handle Result & Update Action State
        if mock_result["result"] == ExecutionResult.SUCCESS:
            new_status = ActionStatus.COMPLETED
        elif mock_result["result"] == ExecutionResult.FAILED:
            new_status = ActionStatus.FAILED
        elif mock_result["result"] == ExecutionResult.NOT_ATTEMPTED and mock_result.get("error") == "NOT_IMPLEMENTED":
            new_status = ActionStatus.BLOCKED
        else:  # UNKNOWN
            new_status = ActionStatus.EXECUTING

        # F-09 Fix: Do not overwrite terminal states if somehow re-evaluated
        current_action = governance_registry.get_operational_action(action_id)
        if current_action.status not in (ActionStatus.COMPLETED, ActionStatus.CANCELLED):
            receipt = {
                "connector": actual_connector,
                "intent_hash": action.intent_hash
            }
            governance_registry.update_operational_action(action_id, new_status, mock_result["result"], receipt=receipt)

        return {
            "status": new_status.value,
            "execution_result": mock_result["result"].value,
            "attempt_id": attempt_id,
            "message": mock_result.get("message", "Execution finished.")
        }

    def _mock_connector_call(self, action: OperationalActionRecord) -> Dict[str, Any]:
        """
        Simulates an external API call that might succeed, fail, or time out (UNKNOWN).
        """
        # We can use the action target to deterministically mock failures for testing.
        target = action.target_reference

        if target.endswith("-FAIL"):
            return {
                "result": ExecutionResult.FAILED,
                "error": "TARGET_REJECTED",
                "message": "External system rejected the operation."
            }
        elif target.endswith("-UNKNOWN"):
            return {
                "result": ExecutionResult.UNKNOWN,
                "error": "CONNECTION_TIMEOUT",
                "message": "Connection lost before response was received."
            }
        elif target.endswith("-UNAVAILABLE"):
            return {
                "result": ExecutionResult.FAILED,
                "error": "CONNECTOR_UNAVAILABLE",
                "message": "Connector is offline."
            }
        else:
            return {
                "result": ExecutionResult.SUCCESS,
                "error": None,
                "message": "Operation successfully confirmed."
            }


operational_execution_engine = OperationalExecutionEngine()
