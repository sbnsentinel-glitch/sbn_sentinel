import pytest
from fastapi.testclient import TestClient
import uuid
import datetime
import json
from app.main import app
from app.db.database import SessionLocal, Base
from app.models.governance_storage import (
    HumanDecisionModel,
    OperationalActionModel,
    ExecutionAttemptModel,
    OperationalOutcomeModel,
    RecommendationModel,
    RuleEvaluationModel,
    GovernedRuleVersionModel,
    GovernedRecommendationMappingModel
)
from app.models.organization import OrganizationClinicModel
from app.models.encounter import EncounterModel
from app.services.governance_registry import governance_registry, DecisionType, DecisionStatus
from app.api.deps import get_current_user

client = TestClient(app)


@pytest.fixture(scope="function")
def setup_db():
    Base.metadata.drop_all(bind=SessionLocal().get_bind())
    Base.metadata.create_all(bind=SessionLocal().get_bind())
    yield
    db = SessionLocal()
    db.query(OperationalOutcomeModel).delete()
    db.query(ExecutionAttemptModel).delete()
    db.query(OperationalActionModel).delete()
    db.query(HumanDecisionModel).delete()
    db.query(OrganizationClinicModel).delete()
    db.query(EncounterModel).delete()
    db.query(RecommendationModel).delete()
    db.query(RuleEvaluationModel).delete()
    db.query(GovernedRuleVersionModel).delete()
    db.query(GovernedRecommendationMappingModel).delete()
    db.commit()
    db.close()
    governance_registry._human_decisions.clear()
    governance_registry._operational_actions.clear()
    governance_registry._execution_attempts.clear()
    governance_registry._operational_outcomes.clear()


@pytest.fixture(scope="function")
def mock_admin():
    class MockAdmin:
        org_id = "SYSTEM_GLOBAL"
        clinic_id = None
        id = "admin123"
        role = "System Administrator"
        is_active = True
    app.dependency_overrides[get_current_user] = lambda: MockAdmin()
    yield
    app.dependency_overrides.clear()


def setup_d6_context(db, decision_id, journey_id, allowed_outputs=["RESCHEDULE_APPOINTMENT", "SEND_NOTIFICATION"]):
    db.add(GovernedRuleVersionModel(
        rule_id="rule_1",
        version="1.0",
        governing_policy_id="pol_1",
        governing_policy_version="1.0",
        lifecycle_state="ACTIVE",
        allowed_outputs_json=json.dumps(allowed_outputs)
    ))
    db.add(RuleEvaluationModel(
        evaluation_id="eval_1",
        decision_context_id="ctx_1",
        policy_id="pol_1",
        policy_version="1.0",
        rule_id="rule_1",
        rule_version="1.0",
        result="ELIGIBLE",
        evaluation_timestamp=datetime.datetime.utcnow().isoformat(),
        journey_id=journey_id
    ))
    db.add(GovernedRecommendationMappingModel(
        mapping_id="map_1",
        version="1.0",
        applicable_rule_id="rule_1",
        eligible_result="ELIGIBLE",
        recommendation_template="{}",
        authority_requirement="SYSTEM_ADMIN",
        priority="NORMAL",
        lifecycle_state="ACTIVE",
        allowed_action_types_json=json.dumps(allowed_outputs)
    ))
    db.add(RecommendationModel(
        recommendation_id="rec_123",
        decision_context_id="ctx_1",
        rule_evaluation_id="eval_1",
        journey_id=journey_id,
        mapping_id="map_1",
        mapping_version="1.0",
        intended_target_reference="target_123"
    ))
    db.add(HumanDecisionModel(
        decision_id=decision_id,
        recommendation_id="rec_123",
        actor_id="admin123",
        decision_type=DecisionType.APPROVED.value,
        status=DecisionStatus.RECORDED.value,
        journey_id=journey_id,
        decision_timestamp=datetime.datetime.utcnow().isoformat()
    ))
    db.add(OrganizationClinicModel(
        id="target_123",
        organization_id="org_123",
        name="Test Clinic",
        is_active=True
    ))
    db.add(EncounterModel(
        id=journey_id,
        patient_id="pat_1",
        provider_id="prov_1",
        clinic_id="target_123",
        date="2026-09-01",
        type="Consultation",
        status="Completed"
    ))
    db.commit()


def test_action_lifecycle_empty_decision(setup_db, mock_admin):
    decision_id = f"dec_{uuid.uuid4().hex[:8]}"
    response = client.get(f"/api/v1/actions/lifecycle/{decision_id}")
    assert response.status_code == 200
    assert response.json()["technical_state"] == "unavailable"


def test_action_lifecycle_with_decision(setup_db, mock_admin):
    decision_id = f"dec_{uuid.uuid4().hex[:8]}"
    journey_id = f"journey_{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    setup_d6_context(db, decision_id, journey_id)
    db.close()

    response = client.get(f"/api/v1/actions/lifecycle/{decision_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["can_create"] is True
    assert "RESCHEDULE_APPOINTMENT" in data["creation"]["allowed_action_types"]
    assert len(data["creation"]["permitted_targets"]) > 0


def test_idempotency_create_action(setup_db, mock_admin):
    decision_id = f"dec_{uuid.uuid4().hex[:8]}"
    journey_id = f"journey_{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    setup_d6_context(db, decision_id, journey_id)
    db.close()

    payload = {
        "decision_id": decision_id,
        "action_type": "SEND_NOTIFICATION",
        "target_reference": "target_123",
        "parameters": {"k": "v"}
    }

    response = client.post("/api/v1/actions/", json=payload)
    assert response.status_code == 200
    assert response.json()["action_id"] is not None

    # Idempotency success
    response2 = client.post("/api/v1/actions/", json=payload)
    assert response2.status_code == 200
    assert response2.json()["status"] == "IDEMPOTENT"

    # Idempotency conflict (material intent)
    payload_conflict = dict(payload)
    payload_conflict["parameters"] = {"k": "different"}
    response3 = client.post("/api/v1/actions/", json=payload_conflict)
    assert response3.status_code == 409


def test_unknown_blocks_retry(setup_db, mock_admin):
    decision_id = f"dec_{uuid.uuid4().hex[:8]}"
    journey_id = f"journey_{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    setup_d6_context(db, decision_id, journey_id)

    action_id = f"act_{uuid.uuid4().hex[:8]}"
    db.add(OperationalActionModel(
        action_id=action_id,
        authorization_reference=decision_id,
        journey_id=journey_id,
        action_type="SEND_NOTIFICATION",
        target_reference="target_123",
        status="EXECUTING",
        current_result="UNKNOWN",
        created_at=datetime.datetime.utcnow().isoformat()
    ))
    db.add(ExecutionAttemptModel(
        attempt_id=f"att_{uuid.uuid4().hex[:8]}",
        action_id=action_id,
        journey_id=journey_id,
        result="UNKNOWN",
        attempt_number=1,
        connector="MOCK_PRACTICE_FUSION_CONNECTOR",
        attempt_timestamp=datetime.datetime.utcnow().isoformat()
    ))
    db.commit()
    db.close()

    response = client.get(f"/api/v1/actions/lifecycle/{decision_id}")
    data = response.json()
    action_data = data["actions"][0]
    assert action_data["can_retry"] is False


def test_outcome_persistence(setup_db, mock_admin):
    decision_id = f"dec_{uuid.uuid4().hex[:8]}"
    journey_id = f"journey_{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    setup_d6_context(db, decision_id, journey_id)

    action_id = f"act_{uuid.uuid4().hex[:8]}"
    db.add(OperationalActionModel(
        action_id=action_id,
        authorization_reference=decision_id,
        journey_id=journey_id,
        action_type="SEND_NOTIFICATION",
        target_reference="target_123",
        status="COMPLETED",
        current_result="SUCCESS",
        created_at=datetime.datetime.utcnow().isoformat()
    ))
    db.commit()
    db.close()

    from app.services.governance_registry import OperationalOutcomeRecord, OutcomeConfirmationState, OutcomeResolutionState

    outcome_id = f"out_{uuid.uuid4().hex[:8]}"
    outcome = OperationalOutcomeRecord(
        outcome_id=outcome_id,
        action_id=action_id,
        journey_id=journey_id,
        expected_outcome={"status": "ok"},
        observed_outcome={"status": "ok"},
        confirmation_state=OutcomeConfirmationState.CONFIRMED,
        resolution_state=OutcomeResolutionState.RESOLVED,
        source_reference="sysA",
        closure_reason="done",
        confirmed_at=datetime.datetime.utcnow()
    )
    governance_registry.record_operational_outcome(outcome)

    # Retrieve from DB to check persistence
    db = SessionLocal()
    saved_outcome = db.query(OperationalOutcomeModel).filter_by(outcome_id=outcome_id).first()
    assert saved_outcome.source_reference == "sysA"
    assert saved_outcome.closure_reason == "done"
    assert saved_outcome.confirmed_at is not None
    db.close()

    # Retrieve from registry to check restoration
    restored = governance_registry.get_operational_outcome(outcome_id)
    assert restored.source_reference == "sysA"
    assert restored.closure_reason == "done"
    assert restored.confirmed_at is not None


def test_governance_target_and_type_rejection(setup_db, mock_admin):
    decision_id = f"dec_{uuid.uuid4().hex[:8]}"
    journey_id = f"journey_{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    setup_d6_context(db, decision_id, journey_id)
    db.close()

    # Test invalid action type
    payload_invalid_type = {
        "decision_id": decision_id,
        "action_type": "INVALID_TYPE",
        "target_reference": "target_123",
        "parameters": {}
    }
    response = client.post("/api/v1/actions/", json=payload_invalid_type)
    assert response.status_code == 403
    assert "not permitted by governance mapping" in response.json()["detail"]

    # Test invalid target
    payload_invalid_target = {
        "decision_id": decision_id,
        "action_type": "SEND_NOTIFICATION",
        "target_reference": "target_INVALID",
        "parameters": {}
    }
    response2 = client.post("/api/v1/actions/", json=payload_invalid_target)
    assert response2.status_code == 403
    assert "match intended target" in response2.json()["detail"]


def test_failed_retry_limits(setup_db, mock_admin):
    decision_id = f"dec_{uuid.uuid4().hex[:8]}"
    journey_id = f"journey_{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    setup_d6_context(db, decision_id, journey_id)

    action_id = f"act_{uuid.uuid4().hex[:8]}"
    db.add(OperationalActionModel(
        action_id=action_id,
        authorization_reference=decision_id,
        journey_id=journey_id,
        action_type="SEND_NOTIFICATION",
        target_reference="target_123",
        status="EXECUTING",
        current_result="FAILED",
        created_at=datetime.datetime.utcnow().isoformat()
    ))
    # N = max_retries = 3
    for i in range(1, 4):
        db.add(ExecutionAttemptModel(
            attempt_id=f"att_{uuid.uuid4().hex[:8]}",
            action_id=action_id,
            journey_id=journey_id,
            result="FAILED",
            attempt_number=i,
            connector="MOCK_PRACTICE_FUSION_CONNECTOR",
            attempt_timestamp=datetime.datetime.utcnow().isoformat()
        ))
    db.commit()
    db.close()

    # Attempt execute
    response = client.post("/api/v1/actions/execute", json={"action_id": action_id})
    # Since attempts = 3, it should exceed limit or return error
    assert response.status_code in (400, 409)
    assert "MAX_RETRIES" in response.json()["detail"] or "limit" in response.json()["detail"].lower()


def test_production_fail_closed_executor(setup_db, mock_admin, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "SYNTHETIC_TEST_ENABLED", False)

    decision_id = f"dec_{uuid.uuid4().hex[:8]}"
    journey_id = f"journey_{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    setup_d6_context(db, decision_id, journey_id)

    action_id = f"act_{uuid.uuid4().hex[:8]}"
    db.add(OperationalActionModel(
        action_id=action_id,
        authorization_reference=decision_id,
        journey_id=journey_id,
        action_type="SEND_NOTIFICATION",
        target_reference="target_123",
        status="READY",
        current_result="NOT_ATTEMPTED",
        created_at=datetime.datetime.utcnow().isoformat()
    ))
    db.commit()
    db.close()

    response = client.post("/api/v1/actions/execute", json={"action_id": action_id})
    # Production should block it, actions.py translates BLOCKED to 409
    assert response.status_code == 409
    assert "not implemented" in response.json()["detail"].lower()


def test_outcome_mismatch_behavior(setup_db, mock_admin):
    decision_id = f"dec_{uuid.uuid4().hex[:8]}"
    journey_id = f"journey_{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    setup_d6_context(db, decision_id, journey_id)

    action_id = f"act_{uuid.uuid4().hex[:8]}"
    db.add(OperationalActionModel(
        action_id=action_id,
        authorization_reference=decision_id,
        journey_id=journey_id,
        action_type="SEND_NOTIFICATION",
        target_reference="target_123",
        status="COMPLETED",
        current_result="SUCCESS",
        created_at=datetime.datetime.utcnow().isoformat()
    ))
    db.commit()
    db.close()

    from app.services.governance_registry import OperationalOutcomeRecord, OutcomeConfirmationState, OutcomeResolutionState
    outcome = OperationalOutcomeRecord(
        outcome_id=f"out_{uuid.uuid4().hex[:8]}",
        action_id=action_id,
        journey_id=journey_id,
        expected_outcome={"status": "ok"},
        observed_outcome={"status": "fail"},
        confirmation_state=OutcomeConfirmationState.MISMATCH,
        resolution_state=OutcomeResolutionState.UNRESOLVED,
        source_reference="sysB",
        closure_reason=None,
        confirmed_at=datetime.datetime.utcnow()
    )
    governance_registry.record_operational_outcome(outcome)

    response = client.get(f"/api/v1/actions/lifecycle/{decision_id}")
    data = response.json()
    action_data = data["actions"][0]
    assert action_data["outcome"]["confirmation_state"] == "MISMATCH"
    assert action_data["outcome"]["resolution_state"] == "UNRESOLVED"
