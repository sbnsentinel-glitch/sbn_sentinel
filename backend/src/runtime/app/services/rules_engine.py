from app.services.governance_registry import governance_registry, RuleEvaluationRecord, RuleVersion
from app.services.base_service import BaseService
import logging
from datetime import datetime
import uuid
from typing import Dict, Any

logger = logging.getLogger(__name__)


class RulesEngine(BaseService):
    """
    SESR-003 Compliant Rules Engine (RE).
    Evaluates governed deterministic rules based on Applicable Version Resolution.
    Enforces strict Input Definitions and outputs Rule Evaluation Records.
    """

    def __init__(self):
        self.registry = governance_registry
        self._handlers = {}

    @property
    def service_name(self) -> str:
        return "RulesEngine"

    @property
    def version(self) -> str:
        return "v2.0"

    def _process(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluates operational context against governed rules.
        Payload expects: {"decision_context": Dict, "policy_result": Dict}
        """
        decision_context = payload.get("decision_context", {})
        policy_result = payload.get("policy_result", {})
        # SESR-008: Extract journey_id from payload — must be the originating event's correlation_id
        journey_id = payload.get("journey_id")

        # 1. PRR-005: Validate Policy Governance State
        if not policy_result or not getattr(policy_result, "is_permitted", False):
            if isinstance(policy_result, dict) and not policy_result.get("is_permitted", False):
                return {
                    "rule_result": "NOT_EVALUABLE",
                    "reason": "Policy engine blocked execution or returned invalid context."
                }
            elif not isinstance(policy_result, dict) and not getattr(policy_result, "is_permitted", False):
                return {
                    "rule_result": "NOT_EVALUABLE",
                    "reason": "Policy engine blocked execution."
                }

        now = datetime.utcnow()
        findings = []

        # We assume the policy result includes evaluated policy objects or IDs.
        # For simplicity, we just ask the registry for applicable policies for this eval_time.
        applicable_policies = self.registry.get_applicable_policies(now)

        for policy in applicable_policies:
            # 2. PRR-004: Resolve Applicable Rule Version
            applicable_rules = self.registry.get_applicable_rules_for_policy(
                policy_id=policy.policy_id,
                policy_version=policy.version,
                eval_time=now
            )

            for rule in applicable_rules:
                # 3. PRO-006: Load Defined Rule Inputs & Check
                input_values = {}
                missing_inputs = []
                for req_input in rule.inputs:
                    val = decision_context.get(req_input.input_name)
                    if val is None and req_input.required:
                        missing_inputs.append(req_input.input_name)
                    else:
                        input_values[req_input.input_name] = val

                if missing_inputs:
                    result_state = "NOT_EVALUABLE"
                else:
                    # 4. PGC-015: Deterministic Rule Logic execution
                    result_state = self._execute_rule_logic(rule, input_values)

                # PRO-014: Fail closed if context identity is missing (SESR-009)
                decision_context_id = decision_context.get("id")
                if not decision_context_id:
                    raise ValueError("CRITICAL_FAULT: Decision Context ID is missing. Cannot persist Rule Evaluation.")

                # 5. PRO-014: Rule Evaluation Record
                eval_record = RuleEvaluationRecord(
                    evaluation_id=str(uuid.uuid4()),
                    decision_context_id=decision_context_id,
                    policy_id=policy.policy_id,
                    policy_version=policy.version,
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    result=result_state,
                    evaluation_timestamp=now,
                    input_values=input_values,
                    # SESR-008: Stamp journey identity on this record
                    journey_id=journey_id
                )
                self.registry.record_evaluation(eval_record)

                findings.append({
                    "rule_id": rule.rule_id,
                    "rule_version": rule.version,
                    "result": result_state,
                    "evaluation_id": eval_record.evaluation_id
                })

        return {"findings": findings}

    def _execute_rule_logic(self, rule: RuleVersion, inputs: Dict[str, Any]) -> str:
        """Deterministically evaluates rule conditions."""

        if not self._handlers:
            def eval_sch_001_v1(inputs: Dict[str, Any]) -> str:
                if inputs.get("primary_context") == "Operational" and inputs.get("secondary_context") == "Provider Schedule Gap":
                    return "CONDITION_MET"
                return "CONDITION_NOT_MET"

            def eval_sch_002_v1(inputs: Dict[str, Any]) -> str:
                if inputs.get("primary_context") == "Operational" and inputs.get("secondary_context") == "Queue Congestion":
                    return "CONDITION_MET"
                return "CONDITION_NOT_MET"

            def eval_sch_003_v1(inputs: Dict[str, Any]) -> str:
                if inputs.get("primary_context") == "Operational":
                    return "CONDITION_MET"
                return "CONDITION_NOT_MET"

            self._handlers[("RULE-SCH-001", "1.0")] = eval_sch_001_v1
            self._handlers[("RULE-SCH-002", "1.0")] = eval_sch_002_v1
            self._handlers[("RULE-SCH-003", "1.0")] = eval_sch_003_v1

        handler = self._handlers.get((rule.rule_id, rule.version))
        if not handler:
            return "NOT_EVALUABLE"

        return handler(inputs)

    def register_handler(self, rule_id: str, handler: callable, version: str = "V1") -> None:
        """Register a custom handler for testing or dynamic rules."""
        self._handlers[(rule_id, version)] = handler


rules_engine = RulesEngine()
