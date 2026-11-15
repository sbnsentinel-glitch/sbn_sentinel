import pytest
from app.services.decision_context_engine import decision_context_engine
from app.services.policy_engine import policy_engine
from app.services.base_service import ServiceRequest


@pytest.mark.integration
def test_cp02_cp04_unresolved_evidence_causes_pending_evidence_and_policy_block():
    """
    CP-02 / CP-04 (Tickets 01 & 03):
    Missing / unresolved evidence reference causes PENDING_EVIDENCE.
    Decision context is marked decision-ineligible.
    Policy Engine blocks recommendation generation (is_permitted=False).
    """
    req = ServiceRequest(
        correlation_id="test-corr-pending",
        calling_module="TestRunner",
        target_service="DecisionContextEngine",
        payload={
            "eos_003_package": {
                "evidence_items": [],
                "evidence_references": ["ref-does-not-exist-12345"],
                "facts": {}
            },
            "event_type": "EHR",
            "event_id": "evt-pending-01",
            "org_id": "org-test-1",
            "clinic_id": "clinic-test-1"
        }
    )

    resp = decision_context_engine.invoke(req)
    result = resp.result_payload

    # CP-04: Unresolved ref causes PENDING_EVIDENCE
    assert result.get("sufficiency_status") == "PENDING_EVIDENCE"
    assert result.get("is_decision_eligible") is False

    # Check that Context Package identity has authoritative org/clinic
    pkg = result.get("ais_002_decision_context_package", {})
    identity = pkg.get("identity", {})
    assert identity.get("organization_id") == "org-test-1"
    assert identity.get("clinic_id") == "clinic-test-1"

    # CP-02: Pass to Policy Engine — must fail closed and deny rule evaluation
    pol_req = ServiceRequest(
        correlation_id="test-corr-pending",
        calling_module="TestRunner",
        target_service="PolicyEngine",
        payload={
            "decision_context": {
                "id": "ctx-test-pending",
                "evidence_package": {"evidence_references": ["ref-does-not-exist-12345"]},
                "event_type": "EHR",
                "source": "Practice Fusion",
                "primary_context": result.get("primary_context"),
                "sufficiency_status": result.get("sufficiency_status"),
                "is_decision_eligible": result.get("is_decision_eligible")
            }
        }
    )
    pol_resp = policy_engine.invoke(pol_req)
    pol_result = pol_resp.result_payload.get("policy_result")

    assert pol_result.is_permitted is False
    assert "GOV-SUFFICIENCY-GATE" in pol_result.failed_policies

