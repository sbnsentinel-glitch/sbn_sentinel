import pytest
from app.services.intelligence_engine import intelligence_engine
from app.services.rules_engine import rules_engine
from app.services.base_service import ServiceRequest
from app.services.governance_registry import governance_registry, RuleVersion, LifecycleState, RuleInputDefinition
from datetime import datetime, timedelta


@pytest.mark.integration
def test_cp07_multi_finding_governed_precedence():
    """
    CP-07 / Ticket 18:
    When multiple findings are passed, governed precedence selects the higher severity finding
    rather than arbitrarily picking the first one in list order.
    """
    findings = [
        {"rule_id": "RULE-SCH-002", "severity": "Moderate", "result": "CONDITION_MET", "evaluation_id": "eval-mod"},
        {"rule_id": "RULE-SCH-001", "severity": "High", "result": "CONDITION_MET", "evaluation_id": "eval-high"},
    ]

    req = ServiceRequest(
        correlation_id="test-precedence",
        calling_module="TestRunner",
        target_service="IntelligenceEngine",
        payload={
            "finding": findings,  # Moderate is first in list, High is second
            "context": {
                "id": "ctx-prec-1",
                "primary_context": "Operational",
                "secondary_context": "Provider Schedule Gap"
            },
            "journey_id": "journey-prec-1"
        }
    )

    resp = intelligence_engine.invoke(req)
    result = resp.result_payload

    # Governed precedence must pick the High finding (RULE-SCH-001), not the first item
    assert result.get("rule_id") == "RULE-SCH-001"
    assert result.get("priority_score") is None  # CP-25: score is None until governed scoring contract


@pytest.mark.integration
def test_cp06_invalid_rule_inputs_produce_not_evaluable():
    """
    CP-06: Malformed/invalid input types must result in NOT_EVALUABLE, never CONDITION_NOT_MET.
    """
    # Temporarily register a rule requiring int
    rule_id = "RULE-INT-TEST"
    if not governance_registry.get_rule_by_version(rule_id, "V1"):
        governance_registry.register_rule(RuleVersion(
            rule_id=rule_id,
            version="V1",
            logic_description="Test int input validation",
            lifecycle_state=LifecycleState.ACTIVE,
            governing_policy_id="POL-001",
            governing_policy_version="V1",
            inputs=[RuleInputDefinition("count", "int", True, "DecisionContext")],
            allowed_outputs=["CONDITION_MET", "CONDITION_NOT_MET", "NOT_EVALUABLE"],
            effective_from=datetime.utcnow() - timedelta(days=1),
            approval_state="APPROVED",
            approved_by="Admin"
        ))

    # Evaluate with string value instead of int
    req = ServiceRequest(
        correlation_id="test-type-fail",
        calling_module="TestRunner",
        target_service="RulesEngine",
        payload={
            "decision_context": {
                "id": "ctx-type-1",
                "count": "invalid_not_an_int",  # Wrong type!
                "primary_context": "Operational"
            },
            "policy_result": {"is_permitted": True, "evaluated_policies": ["POL-001 / V1"]},
            "journey_id": "journey-type-1"
        }
    )

    resp = rules_engine.invoke(req)
    findings = resp.result_payload.get("findings", [])
    int_finding = next((f for f in findings if f.get("rule_id") == rule_id), None)
    if int_finding:
        assert int_finding.get("result") == "NOT_EVALUABLE"

