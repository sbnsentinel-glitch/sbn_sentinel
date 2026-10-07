"""
ARR-001 B-005: Policy Engine — Independent Governance Component

The Policy Engine evaluates GOVERNANCE conditions on a Decision Context.
It answers: "Is this recommendation PERMITTED?"

It runs BEFORE the Rule Engine. Always.
It evaluates only Sentinel canonical business concepts — never vendor terminology.

Responsibility:
- Is required evidence available?
- Are mandatory operational conditions met?
- Does clinic configuration permit this recommendation?
- Are authority/business constraints satisfied?

Pipeline Position:
    Decision Context Engine -> Policy Engine -> Rule Engine
"""
import logging
from typing import Dict, Any, List
from dataclasses import dataclass
import uuid
from app.services.base_service import BaseService

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# POLICY RESULT — Output of policy evaluation
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class PolicyResult:
    """
    ARR-001 B-005: The output of the Policy Engine evaluation.
    Determines whether the Rule Engine is permitted to proceed.
    """
    policy_id: str
    is_permitted: bool                       # True = Rule Engine may proceed
    evaluated_policies: List[str]            # Which policies were checked
    failed_policies: List[str]               # Which policies blocked recommendation
    governance_notes: str                    # Human-readable explanation
    policy_version: str = "v1.0"             # ARR-001 C-003: Version preservation


# ─────────────────────────────────────────────────────────────────────────────
# POLICY ENGINE — Governance gatekeeper
# ─────────────────────────────────────────────────────────────────────────────
class PolicyEngine(BaseService):
    """
    ARR-001 B-005 Compliant Policy Engine.

    Evaluates governance conditions against the Decision Context.
    The Policy Engine is the governance authority. The Rule Engine is
    the operational reasoning authority. These responsibilities are NEVER merged.

    Rules:
    - Runs BEFORE Rule Engine. Always.
    - Uses only canonical Sentinel concepts. Never vendor terminology.
    - Risk scores do NOT control policy decisions.
    - Policies can be updated without changing Rule logic.
    - Can be unit tested independently without connectors.
    """

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        from app.services.governance_registry import governance_registry
        self.registry = governance_registry
        self._handlers = {}

    def _execute_policy_logic(self, policy, decision_context, evidence_package, event_type) -> tuple[bool, str]:
        if not self._handlers:
            def eval_pol_001_v1(ctx, ep, et):
                if not ep or (not ep.get("evidence_items") and not ep.get("evidence_references")):
                    return False, "BLOCKED: No operational evidence available."
                return True, f"PASS: {policy.policy_id}"

            def eval_pol_002_v1(ctx, ep, et):
                allowed_event_types = ["EHR", "Phone", "Email", "Manual"]
                if et not in allowed_event_types:
                    return False, f"BLOCKED: Event type '{et}' not authorized."
                return True, f"PASS: {policy.policy_id}"

            def eval_pol_003_v1(ctx, ep, et):
                return True, f"PASS: {policy.policy_id}"

            self._handlers[("POL-001", "V1")] = eval_pol_001_v1
            self._handlers[("POL-002", "V1")] = eval_pol_002_v1
            self._handlers[("POL-003", "V1")] = eval_pol_003_v1

        handler = self._handlers.get((policy.policy_id, policy.version))
        if not handler:
            return False, f"NOT_EVALUABLE: Unknown or unsupported policy {policy.policy_id} v{policy.version}"
        return handler(decision_context, evidence_package, event_type)

    @property
    def service_name(self) -> str:
        return "PolicyEngine"

    @property
    def version(self) -> str:
        return "v1.0"

    def _process(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Implementation of BaseService._process.
        Expects payload with 'decision_context'.
        Returns dict with PolicyResult.
        """
        result = self.evaluate(decision_context=payload.get("decision_context", {}))
        return {"policy_result": result}

    def evaluate(self, decision_context: Dict[str, Any]) -> PolicyResult:
        """
        Evaluate governance conditions against the Decision Context.
        """
        evaluated = []
        failed = []
        notes = []

        # We assume decision_context has these injected
        evidence_package = decision_context.get("evidence_package")
        event_type = decision_context.get("event_type", "Unknown")

        # Get Applicable Policies (SESR-003 PRR-003)
        from datetime import datetime
        now = datetime.utcnow()
        applicable_policies = self.registry.get_applicable_policies(eval_time=now)

        for policy in applicable_policies:
            evaluated.append(f"{policy.policy_id} / {policy.version}")

            is_pass, reason = self._execute_policy_logic(policy, decision_context, evidence_package, event_type)
            if not is_pass:
                failed.append(f"{policy.policy_id} / {policy.version}")
                notes.append(reason)
            else:
                notes.append(reason)

        is_permitted = len(failed) == 0 and len(applicable_policies) > 0

        result = PolicyResult(
            policy_id=str(uuid.uuid4()),
            is_permitted=is_permitted,
            evaluated_policies=evaluated,
            failed_policies=failed,
            governance_notes=" | ".join(notes),
            policy_version="v1.0"
        )

        self.logger.info(
            f"[PolicyEngine] Evaluation complete. Permitted={is_permitted}. "
            f"Checked={len(evaluated)} policies. Failed={len(failed)} policies."
        )
        return result


policy_engine = PolicyEngine()
