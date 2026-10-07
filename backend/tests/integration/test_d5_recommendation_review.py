import pytest
from fastapi.testclient import TestClient
import uuid
import datetime
from app.main import app
from app.db.database import SessionLocal, Base
from app.models.signal import SignalModel
from app.models.governance_storage import RuleEvaluationModel, RecommendationModel, HumanDecisionModel
from app.services.governance_registry import governance_registry, AuthorityConfiguration, DecisionType

client = TestClient(app)


@pytest.fixture(scope="function")
def setup_db():
    Base.metadata.create_all(bind=SessionLocal().get_bind())
    yield
    # Clean up
    db = SessionLocal()
    db.query(SignalModel).delete()
    db.query(RuleEvaluationModel).delete()
    db.query(RecommendationModel).delete()
    db.query(HumanDecisionModel).delete()
    db.commit()
    db.close()
    governance_registry._recommendations.clear()
    governance_registry._human_decisions.clear()


@pytest.fixture(scope="function")
def mock_admin():
    from app.api.deps import get_current_user

    class MockAdmin:
        org_id = "SYSTEM_GLOBAL"
        clinic_id = None
        id = "admin123"
        role = "System Administrator"
        is_active = True

    app.dependency_overrides[get_current_user] = lambda: MockAdmin()

    governance_registry.register_authority_config(AuthorityConfiguration(
        role="System Administrator",
        allowed_decisions=[DecisionType.APPROVED, DecisionType.REJECTED],
        can_override=True,
        requires_reason_for=[DecisionType.REJECTED]
    ))

    yield
    app.dependency_overrides.clear()


@pytest.mark.governance
def test_d5_recommendation_review_endpoint_exact_match(setup_db, mock_admin):
    db = SessionLocal()
    signal_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())

    # 1. Setup Signal
    sig = SignalModel(
        id=signal_id,
        type="test_signal",
        org_id="SYSTEM_GLOBAL",
        metadata_data={"pipeline_event_id": event_id, "correlation_id": correlation_id}
    )
    db.add(sig)

    rec_id = str(uuid.uuid4())
    eval_id = str(uuid.uuid4())
    context_id = "ctx-test"

    # 2. Setup RuleEvaluation
    rule_eval = RuleEvaluationModel(
        evaluation_id=eval_id,
        decision_context_id=context_id,
        policy_id="pol-test",
        policy_version="1.0",
        rule_id="rule-test",
        rule_version="1.0",
        result="PASS",
        evaluation_timestamp=datetime.datetime.utcnow().isoformat(),
        journey_id=correlation_id
    )
    db.add(rule_eval)

    # 3. Setup Persisted Recommendation
    rec = RecommendationModel(
        recommendation_id=rec_id,
        decision_context_id=context_id,
        rule_evaluation_id=eval_id,
        journey_id=correlation_id,
        mapping_id="map-1",
        mapping_version="1.0",
        content="Test Recommendation Content",
        status="ACTIVE",
        authority_requirement="APPROVAL_REQUIRED",
        priority="High",
        generated_at=datetime.datetime.utcnow().isoformat()
    )
    db.add(rec)
    db.commit()

    # 4. Test the GET endpoint
    response = client.get(f"/api/v1/decisions/review/{signal_id}")
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["technical_state"] == "ready"
    assert data["recommendation"]["recommendation_id"] == rec_id
    assert data["recommendation"]["content"] == "Test Recommendation Content"
    assert data["authority"]["state"] == "AUTHORIZED"
    assert data["current_decision"] is None

    # 5. (Removed manual registry append; get_recommendation reads from DB now)
    # 6. Test POST Decision (Submit)
    post_resp = client.post("/api/v1/decisions/", json={
        "recommendation_id": rec_id,
        "decision_type": "APPROVED",
        "reason": "Looking good"
    })

    assert post_resp.status_code == 200, post_resp.text

    # 7. Clear session/registry to prove we read from DB
    db.close()
    governance_registry._human_decisions.clear()

    # 8. Verify GET endpoint shows the current decision exactly from DB
    get_again = client.get(f"/api/v1/decisions/review/{signal_id}")
    data_again = get_again.json()
    assert data_again["current_decision"] is not None
    assert data_again["current_decision"]["decision_type"] == "APPROVED"
    assert data_again["current_decision"]["status"] == "RECORDED"


@pytest.mark.governance
def test_d5_recommendation_ambiguity(setup_db, mock_admin):
    db = SessionLocal()
    signal_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())

    sig = SignalModel(id=signal_id, type="test_signal", org_id="SYSTEM_GLOBAL", metadata_data={"correlation_id": correlation_id})
    db.add(sig)

    # Add two rule evaluations and two recommendations for the same journey
    for i in range(2):
        eval_id = str(uuid.uuid4())
        db.add(RuleEvaluationModel(
            evaluation_id=eval_id,
            decision_context_id=f"ctx-{i}",
            policy_id="pol-test", policy_version="1.0", rule_id="rule-test", rule_version="1.0",
            result="PASS", evaluation_timestamp=datetime.datetime.utcnow().isoformat(),
            journey_id=correlation_id
        ))
        db.add(RecommendationModel(
            recommendation_id=str(uuid.uuid4()),
            decision_context_id=f"ctx-{i}",
            rule_evaluation_id=eval_id,
            journey_id=correlation_id,
            mapping_id="map-1", mapping_version="1.0", content="Content",
            status="ACTIVE", authority_requirement="APPROVAL_REQUIRED", priority="High", generated_at=datetime.datetime.utcnow().isoformat()
        ))
    db.commit()

    # Ambiguity check
    response = client.get(f"/api/v1/decisions/review/{signal_id}")
    assert response.status_code == 200
    assert response.json()["technical_state"] == "ambiguous"


@pytest.mark.governance
def test_d5_recommendation_lifecycle_expired(setup_db, mock_admin):
    db = SessionLocal()
    signal_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())

    db.add(SignalModel(id=signal_id, type="test_signal", org_id="SYSTEM_GLOBAL", metadata_data={"correlation_id": correlation_id}))

    eval_id = str(uuid.uuid4())
    db.add(RuleEvaluationModel(
        evaluation_id=eval_id, decision_context_id="ctx-test",
        policy_id="pol-test", policy_version="1.0", rule_id="rule-test", rule_version="1.0",
        result="PASS", evaluation_timestamp=datetime.datetime.utcnow().isoformat(),
        journey_id=correlation_id
    ))

    # Add EXPIRED recommendation
    db.add(RecommendationModel(
        recommendation_id=str(uuid.uuid4()),
        decision_context_id="ctx-test",
        rule_evaluation_id=eval_id,
        journey_id=correlation_id,
        mapping_id="map-1", mapping_version="1.0", content="Content",
        status="EXPIRED", authority_requirement="APPROVAL_REQUIRED", priority="High", generated_at=datetime.datetime.utcnow().isoformat()
    ))
    db.commit()

    response = client.get(f"/api/v1/decisions/review/{signal_id}")
    data = response.json()
    assert data["authority"]["state"] == "AUTHORIZED"
    assert data["authority"]["eligibility"] == "EXPIRED"
    assert len(data["authority"]["allowed_decisions"]) == 0


@pytest.mark.governance
def test_d5_restart_safe_duplicate_decision_prevention(setup_db, mock_admin):
    db = SessionLocal()
    signal_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())
    eval_id = str(uuid.uuid4())
    rec_id = str(uuid.uuid4())
    context_id = "ctx-restart-test"

    db.add(SignalModel(
        id=signal_id,
        type="test_signal",
        org_id="SYSTEM_GLOBAL",
        metadata_data={"correlation_id": correlation_id}
    ))
    db.add(RuleEvaluationModel(
        evaluation_id=eval_id,
        decision_context_id=context_id,
        policy_id="pol-test",
        policy_version="1.0",
        rule_id="rule-test",
        rule_version="1.0",
        result="PASS",
        evaluation_timestamp=datetime.datetime.utcnow().isoformat(),
        journey_id=correlation_id
    ))
    db.add(RecommendationModel(
        recommendation_id=rec_id,
        decision_context_id=context_id,
        rule_evaluation_id=eval_id,
        journey_id=correlation_id,
        mapping_id="map-1",
        mapping_version="1.0",
        content="Restart test content",
        status="ACTIVE",
        authority_requirement="APPROVAL_REQUIRED",
        priority="High",
        generated_at=datetime.datetime.utcnow().isoformat()
    ))
    db.commit()

    # 1. First decision submission
    resp1 = client.post("/api/v1/decisions/", json={
        "recommendation_id": rec_id,
        "decision_type": "APPROVED",
        "reason": "First approval"
    })
    assert resp1.status_code == 200

    # 2. Simulate restart / in-memory cache clear
    governance_registry._human_decisions.clear()

    # 3. Idempotent retry with same actor & decision_type
    resp_retry = client.post("/api/v1/decisions/", json={
        "recommendation_id": rec_id,
        "decision_type": "APPROVED",
        "reason": "First approval"
    })
    assert resp_retry.status_code == 200
    assert resp_retry.json()["message"] == "Decision already recorded."

    # 4. Attempt conflicting decision on same recommendation
    resp_conflict = client.post("/api/v1/decisions/", json={
        "recommendation_id": rec_id,
        "decision_type": "REJECTED",
        "reason": "Second conflicting decision"
    })
    assert resp_conflict.status_code == 400
    assert "already exists" in resp_conflict.json()["detail"]

    # 5. Verify exactly 1 durable HumanDecisionModel row exists in database
    decisions_count = db.query(HumanDecisionModel).filter(
        HumanDecisionModel.recommendation_id == rec_id
    ).count()
    assert decisions_count == 1
    db.close()


@pytest.mark.governance
def test_d5_authority_states_separation(setup_db, monkeypatch):
    from app.api.deps import get_current_user

    db = SessionLocal()
    signal_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())
    eval_id = str(uuid.uuid4())
    rec_id = str(uuid.uuid4())
    context_id = "ctx-auth-test"

    db.add(SignalModel(
        id=signal_id,
        type="test_signal",
        org_id="UNASSIGNED",
        metadata_data={"correlation_id": correlation_id}
    ))
    db.add(RuleEvaluationModel(
        evaluation_id=eval_id,
        decision_context_id=context_id,
        policy_id="pol-test",
        policy_version="1.0",
        rule_id="rule-test",
        rule_version="1.0",
        result="PASS",
        evaluation_timestamp=datetime.datetime.utcnow().isoformat(),
        journey_id=correlation_id
    ))
    db.add(RecommendationModel(
        recommendation_id=rec_id,
        decision_context_id=context_id,
        rule_evaluation_id=eval_id,
        journey_id=correlation_id,
        mapping_id="map-1",
        mapping_version="1.0",
        content="Authority test content",
        status="ACTIVE",
        authority_requirement="APPROVAL_REQUIRED",
        priority="High",
        generated_at=datetime.datetime.utcnow().isoformat()
    ))
    db.commit()
    db.close()

    # Case A: AUTHORITY_UNKNOWN when role is None or "UNKNOWN"
    class UnknownUser:
        org_id = "UNASSIGNED"
        clinic_id = None
        id = "unknown1"
        role = "UNKNOWN"
        is_active = True

    app.dependency_overrides[get_current_user] = lambda: UnknownUser()
    resp_unknown = client.get(f"/api/v1/decisions/review/{signal_id}")
    assert resp_unknown.status_code == 200
    assert resp_unknown.json()["authority"]["state"] == "AUTHORITY_UNKNOWN"

    # Case B: NOT_AUTHORIZED when role is known string but unconfigured
    class UnregisteredUser:
        org_id = "UNASSIGNED"
        clinic_id = None
        id = "viewer1"
        role = "AuditorGuest"
        is_active = True

    app.dependency_overrides[get_current_user] = lambda: UnregisteredUser()
    resp_unauth = client.get(f"/api/v1/decisions/review/{signal_id}")
    assert resp_unauth.status_code == 200
    assert resp_unauth.json()["authority"]["state"] == "NOT_AUTHORIZED"

    # Case C: AUTHORITY_CHECK_FAILED when check failure occurs (exception in authority lookup)
    class AdminUser:
        id = "admin1"
        role = "System Administrator"
        is_active = True

    def fail_lookup(role):
        raise RuntimeError("DB connection timeout during authority lookup")

    monkeypatch.setattr(governance_registry, "get_authority_config", fail_lookup)
    app.dependency_overrides[get_current_user] = lambda: AdminUser()
    resp_fail = client.get(f"/api/v1/decisions/review/{signal_id}")
    assert resp_fail.status_code == 200
    assert resp_fail.json()["authority"]["state"] == "AUTHORITY_CHECK_FAILED"

    app.dependency_overrides.clear()


@pytest.mark.governance
def test_d5_no_signal_recommendation_fallback(setup_db, mock_admin):
    db = SessionLocal()
    signal_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())

    sig = SignalModel(
        id=signal_id,
        type="test_signal",
        org_id="SYSTEM_GLOBAL",
        recommended_action="Legacy Fallback Action",
        metadata_data={"correlation_id": correlation_id}
    )
    db.add(sig)
    db.commit()
    db.close()

    response = client.get(f"/api/v1/decisions/review/{signal_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["technical_state"] == "unavailable"
    assert data["recommendation"] is None
