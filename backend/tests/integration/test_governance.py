# flake8: noqa: E501

import pytest
from fastapi.testclient import TestClient
from app.main import app


client = TestClient(app)


@pytest.mark.governance
def test_a024_session_invalidation():
    """
    A-024: Test that a suspended user or stale token cannot access endpoints.
    """
    from app.db.database import SessionLocal
    from app.models.user import User, UserRole
    from app.core.security import get_password_hash
    import uuid

    db = SessionLocal()
    test_email = f"e2e_{uuid.uuid4().hex[:6]}@sbnsentinel.com"
    user = User(
        email=test_email,
        hashed_password=get_password_hash("Test@123"),
        full_name="Suspension Test",
        role=UserRole.SYSTEM_ADMINISTRATOR.value,
        is_active=True
    )
    db.add(user)
    db.commit()

    try:
        # 1. Login
        res = client.post("/api/v1/auth/login", json={"email": test_email, "password": "Test@123"})
        assert res.status_code == 200, "Login failed"
        token = res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 2. Access Protected Route (Should Succeed)
        clinics_res = client.get("/api/v1/clinics", headers=headers)
        assert clinics_res.status_code == 200, "Protected route access failed"

        # 3. Suspend User
        user.is_active = False
        db.commit()

        # 4. Access Protected Route Again (Should Fail)
        clinics_res_fail = client.get("/api/v1/clinics", headers=headers)
        assert clinics_res_fail.status_code in [400, 401, 403], f"Expected 400/401/403 for suspended user, got {clinics_res_fail.status_code}"

    finally:
        db.delete(user)
        db.commit()
        db.close()


@pytest.mark.governance
def test_a023_organization_clinic_scope_enforcement():
    """
    A-023: Test that cross-scope access is rejected (both API routes and create_action target resolution).
    """
    from app.db.database import SessionLocal
    from app.models.user import User, UserRole
    from app.models.organization import OrganizationClinicModel
    from app.models.encounter import EncounterModel
    from app.services.governance_registry import (
        governance_registry, DecisionType, DecisionStatus, HumanDecisionRecord
    )
    from app.services.operational_execution_engine import operational_execution_engine
    from app.core.security import get_password_hash
    import uuid

    db = SessionLocal()
    uid = uuid.uuid4().hex[:6]
    test_email = f"e2e_{uid}@sbnsentinel.com"
    user = User(
        email=test_email,
        hashed_password=get_password_hash("Test@123"),
        full_name="Scope Test",
        role=UserRole.CLINIC_MANAGER.value,
        org_id="ORG-A",
        is_active=True
    )
    clinic_a1 = OrganizationClinicModel(
        id=f"CLN-A1-{uid}", organization_id="ORG-A", name="Clinic A1", is_active=True)
    clinic_a2 = OrganizationClinicModel(
        id=f"CLN-A2-{uid}", organization_id="ORG-A", name="Clinic A2", is_active=True)
    clinic_b = OrganizationClinicModel(
        id=f"CLN-B-{uid}", organization_id="ORG-B", name="Clinic B", is_active=True)
    enc_a1 = EncounterModel(
        id=f"ENC-A1-{uid}",
        clinic_id=clinic_a1.id,
        patient_id=f"PAT-{uid}",
        provider_id="PROV-1",
        date="2026-09-05",
        type="Consultation",
        status="SCHEDULED"
    )

    db.add_all([user, clinic_a1, clinic_a2, clinic_b, enc_a1])
    db.commit()

    dec_id = f"DEC-{uid}"
    governance_registry.record_human_decision(HumanDecisionRecord(
        decision_id=dec_id,
        recommendation_id=f"REC-{uid}",
        actor_id=f"ACT-{uid}",
        decision_type=DecisionType.APPROVED,
        authority_basis="Valid",
        status=DecisionStatus.RECORDED,
        journey_id=f"JNY-{uid}"
    ))

    try:
        # 1. Login & API route test
        res = client.post("/api/v1/auth/login", json={"email": test_email, "password": "Test@123"})
        token = res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        response = client.get("/api/v1/clinics?org_id=ORG-B", headers=headers)
        assert response.status_code in [401, 403], "Should reject cross-scope access"

        # 2. Target Resolution & Scope Enforcement (Item 1 Frozen Criteria)
        # Positive case: Same-org, same-clinic -> PASS
        res_pass = operational_execution_engine.create_action(
            decision_id=dec_id,
            action_type_str="SEND_NOTIFICATION",
            target_reference=enc_a1.id,
            parameters={},
            initiator_scope={"org_id": "ORG-A", "clinic_id": clinic_a1.id}
        )
        assert res_pass["status"] == "SUCCESS"

        # Negative case: Cross-org -> FAIL
        res_cross_org = operational_execution_engine.create_action(
            decision_id=dec_id,
            action_type_str="SEND_NOTIFICATION",
            target_reference=clinic_b.id,
            parameters={},
            initiator_scope={"org_id": "ORG-A", "clinic_id": clinic_a1.id}
        )
        assert res_cross_org["status"] == "ERROR"
        assert "CROSS_ORG" in res_cross_org["message"]

        # Negative case: Cross-clinic -> FAIL
        res_cross_clinic = operational_execution_engine.create_action(
            decision_id=dec_id,
            action_type_str="SEND_NOTIFICATION",
            target_reference=clinic_a2.id,
            parameters={},
            initiator_scope={"org_id": "ORG-A", "clinic_id": clinic_a1.id}
        )
        assert res_cross_clinic["status"] == "ERROR"
        assert "CROSS_CLINIC" in res_cross_clinic["message"]

        # Negative case: Unknown / unresolvable target -> FAIL
        res_unknown = operational_execution_engine.create_action(
            decision_id=dec_id,
            action_type_str="SEND_NOTIFICATION",
            target_reference="UNKNOWN-TARGET-XYZ",
            parameters={},
            initiator_scope={"org_id": "ORG-A", "clinic_id": clinic_a1.id}
        )
        assert res_unknown["status"] == "ERROR"
        assert "TARGET_NOT_FOUND_OR_UNSUPPORTED" in res_unknown["message"]

    finally:
        db.delete(enc_a1)
        db.delete(clinic_a1)
        db.delete(clinic_a2)
        db.delete(clinic_b)
        db.delete(user)
        db.commit()
        db.close()


@pytest.mark.governance
def test_a020_failure_matrix():
    from app.services.operational_execution_engine import operational_execution_engine
    from app.services.governance_registry import OperationalActionRecord, ActionType

    action = OperationalActionRecord(
        action_id="TEST-FAIL-1",
        action_type=ActionType.SEND_NOTIFICATION,
        target_reference="API-UNKNOWN",
        authorization_reference="AUTH-1",
        parameters={}
    )
    result = operational_execution_engine._mock_connector_call(action)
    assert result["result"].value == "UNKNOWN"


@pytest.mark.governance
def test_a021_historical_reconstruction():
    """
    A-021: Test historical reconstruction via ReconstructionEngine.
    Proves determinism: reconstructs original V1 accurately even after a distinct V2 is active.
    """
    from app.services.reconstruction_engine import reconstruction_engine
    from app.services.governance_registry import (
        governance_registry, PolicyVersion, RuleVersion, LifecycleState,
        RecommendationMapping, AuthorityRequirement
    )
    from app.db.database import SessionLocal
    from app.models.governance_storage import RecommendationModel, RuleEvaluationModel
    import json
    import uuid
    from datetime import datetime

    uid = uuid.uuid4().hex[:6]
    policy_id = f"POL-TEST-{uid}"
    rule_id = f"RULE-SCH-{uid}"
    journey_id = f"JNY-{uid}"
    eval_id = f"EVAL-{uid}"
    rec_id = f"REC-{uid}"
    mapping_id = f"MAP-{uid}"

    # 1. Register historical V1 logic
    policy_v1 = PolicyVersion(
        policy_id=policy_id, version="V1", content="V1 Policy", lifecycle_state=LifecycleState.ACTIVE)
    rule_v1 = RuleVersion(
        rule_id=rule_id, version="V1", logic_description="", lifecycle_state=LifecycleState.ACTIVE,
        inputs=[], allowed_outputs=[], governing_policy_id=policy_id, governing_policy_version="V1")
    mapping_v1 = RecommendationMapping(
        mapping_id=mapping_id,
        version="V1",
        applicable_rule_id=rule_id,
        eligible_result="CONDITION_MET",
        recommendation_template="Test Action V1",
        authority_requirement=AuthorityRequirement.INFORMATIONAL,
        priority="High",
        lifecycle_state=LifecycleState.ACTIVE,
        business_impact_template="V1 Impact",
        expected_outcome_template="V1 Expected")

    governance_registry.register_policy(policy_v1)
    governance_registry.register_rule(rule_v1)
    governance_registry.register_recommendation_mapping(mapping_v1)

    # 2. Register materially different V2 logic
    policy_v2 = PolicyVersion(
        policy_id=policy_id, version="V2", content="V2 Policy", lifecycle_state=LifecycleState.ACTIVE)
    rule_v2 = RuleVersion(
        rule_id=rule_id, version="V2", logic_description="", lifecycle_state=LifecycleState.ACTIVE,
        inputs=[], allowed_outputs=[], governing_policy_id=policy_id, governing_policy_version="V2")
    mapping_v2 = RecommendationMapping(
        mapping_id=mapping_id,
        version="V2",
        applicable_rule_id=rule_id,
        eligible_result="CONDITION_MET",
        recommendation_template="Materially Different V2 Action",
        authority_requirement=AuthorityRequirement.APPROVAL_REQUIRED,
        priority="Critical",
        lifecycle_state=LifecycleState.ACTIVE,
        business_impact_template="V2 Impact",
        expected_outcome_template="V2 Expected")

    governance_registry.register_policy(policy_v2)
    governance_registry.register_rule(rule_v2)
    governance_registry.register_recommendation_mapping(mapping_v2)

    db = SessionLocal()
    try:
        # Inject historical state explicitly bound to V1
        db.add(RuleEvaluationModel(
            evaluation_id=eval_id, decision_context_id="CTX-1", policy_id=policy_id, policy_version="V1",
            rule_id=rule_id, rule_version="V1", result="CONDITION_MET",
            evaluation_timestamp=datetime.utcnow().isoformat(),
            input_values_json=json.dumps({"primary_context": "Operational"}), journey_id=journey_id
        ))
        db.add(RecommendationModel(
            recommendation_id=rec_id, decision_context_id="CTX-1", rule_evaluation_id=eval_id,
            journey_id=journey_id, mapping_id=mapping_id, mapping_version="V1", content="Test Action V1",
            status="ACTIVE", priority="High", generated_at=datetime.utcnow().isoformat()
        ))
        db.commit()

        # Invoke Reconstruction Engine: must strictly resolve and match V1
        result = reconstruction_engine.reproduce_decision(rec_id)
        print("REPRODUCTION RESULT:", result)
        assert result.status == "MATCH", f"Reconstruction failed: {result.differences}"
        assert result.reproduced["action"] == "Test Action V1"

        # Query unknown journey: must return NOT_REPRODUCIBLE
        unrec = reconstruction_engine.reproduce_decision("NONEXISTENT-REC-XYZ")
        assert unrec.status == "NOT_REPRODUCIBLE"
    finally:
        db.query(RecommendationModel).filter(RecommendationModel.recommendation_id == rec_id).delete()
        db.query(RuleEvaluationModel).filter(RuleEvaluationModel.evaluation_id == eval_id).delete()
        db.commit()
        db.close()


@pytest.mark.governance
def test_a022_synthetic_test_isolation():
    from app.core.config import Settings
    from pydantic import ValidationError

    # Try to initialize config with SYNTHETIC_TEST_ENABLED in PRODUCTION
    try:
        Settings(
            ENVIRONMENT="PRODUCTION",
            SYNTHETIC_TEST_ENABLED=True,
            CLINIC_TIMEZONE="UTC",
            SECRET_KEY="thisisverysecureandlongenough1234567")
        assert False, "Should have raised a validation error for synthetic test in production"
    except ValidationError as e:
        assert "CDI-006" in str(e) or "SYNTHETIC_TEST_ENABLED" in str(e)


@pytest.mark.governance
def test_e2e_authentic_journey(monkeypatch):
    """
    Audit 3 Item 20: Authentic, authenticated E2E journey test.
    """
    from app.db.database import SessionLocal
    from app.models.user import User, UserRole
    from app.core.security import get_password_hash
    import uuid

    db = SessionLocal()
    test_email = f"e2e_{uuid.uuid4().hex[:6]}@sbnsentinel.com"
    user = User(
        email=test_email,
        hashed_password=get_password_hash("Test@123"),
        full_name="E2E Test",
        role=UserRole.SYSTEM_ADMINISTRATOR.value,
        is_active=True
    )
    from app.models.connector import ConnectorModel
    from datetime import datetime
    pf = ConnectorModel(
        id=f"CONN-PF-{uuid.uuid4().hex[:6]}", name="Practice Fusion EHR", type="EHR",
        status="Healthy", latency_ms=45, last_sync=datetime.utcnow(), config={"client_id": "test", "base_url": "http://test"}
    )
    db.add(user)
    db.add(pf)
    db.commit()

    # 1. Login
    res = client.post("/api/v1/auth/login", json={"email": test_email, "password": "Test@123"})
    assert res.status_code == 200, f"Login failed: {res.text}"
    token = res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    from app.integrations.core.secrets import SigningKeyProvider
    monkeypatch.setattr(SigningKeyProvider, "is_configured", lambda: True)

    # 2. Check Readiness Gate
    ready_res = client.get("/api/v1/health/ready", headers=headers)
    assert ready_res.status_code == 200, f"Readiness gate failed: {ready_res.text}"

    # 3. Access Protected Route
    clinics_res = client.get("/api/v1/clinics", headers=headers)
    assert clinics_res.status_code == 200, "Protected route access failed"

    db.delete(user)
    db.delete(pf)
    db.commit()
    db.close()


@pytest.mark.governance
def test_a025_restart_safe_persistence_and_continuity():
    """
    Audit 4 Item 2: Test that relational persistence survives simulated restart,
    authoritative SQL getters restore records, and continuity validation succeeds
    without relying on in-memory caches.
    """
    from app.services.governance_registry import (
        governance_registry, RecommendationRecord, RecommendationStatus,
        AuthorityRequirement, HumanDecisionRecord, DecisionType, DecisionStatus,
        OperationalActionRecord, ActionType, ActionStatus, ExecutionResult,
        OperationalOutcomeRecord, OutcomeConfirmationState, OutcomeResolutionState,
        RuleEvaluationRecord
    )
    from app.db.database import SessionLocal
    from app.models.governance_storage import (
        RecommendationModel, HumanDecisionModel, OperationalActionModel,
        OperationalOutcomeModel, RuleEvaluationModel
    )
    import uuid
    from datetime import datetime

    uid = uuid.uuid4().hex[:6]
    jny_id = f"JNY-PERSIST-{uid}"
    eval_id = f"EVAL-{uid}"
    rec_id = f"REC-{uid}"
    dec_id = f"DEC-{uid}"
    act_id = f"ACT-{uid}"
    out_id = f"OUT-{uid}"

    # Record full governed chain into DB
    governance_registry.record_evaluation(RuleEvaluationRecord(
        evaluation_id=eval_id,
        decision_context_id="CTX-1",
        policy_id="POL-1",
        policy_version="V1",
        rule_id="RULE-1",
        rule_version="V1",
        result="CONDITION_MET",
        evaluation_timestamp=datetime.utcnow(),
        input_values={"test": 1},
        journey_id=jny_id
    ))
    governance_registry.record_recommendation(RecommendationRecord(
        recommendation_id=rec_id,
        mapping_id="MAP-1",
        mapping_version="V1",
        decision_context_id="CTX-1",
        rule_evaluation_id=eval_id,
        recommendation_content="Persisted Rec Content",
        status=RecommendationStatus.ACTIVE,
        authority_requirement=AuthorityRequirement.INFORMATIONAL,
        priority="High",
        journey_id=jny_id
    ))
    governance_registry.record_human_decision(HumanDecisionRecord(
        decision_id=dec_id,
        recommendation_id=rec_id,
        actor_id="ACT-PERSIST",
        decision_type=DecisionType.APPROVED,
        authority_basis="Valid",
        status=DecisionStatus.RECORDED,
        journey_id=jny_id
    ))
    governance_registry.record_operational_action(OperationalActionRecord(
        action_id=act_id,
        action_type=ActionType.SEND_NOTIFICATION,
        target_reference="TGT-1",
        authorization_reference=dec_id,
        parameters={"foo": "bar"},
        status=ActionStatus.COMPLETED,
        current_result=ExecutionResult.SUCCESS,
        journey_id=jny_id
    ))
    governance_registry.record_operational_outcome(OperationalOutcomeRecord(
        outcome_id=out_id,
        action_id=act_id,
        expected_outcome={"delivered": True},
        observed_outcome={"delivered": True},
        confirmation_state=OutcomeConfirmationState.CONFIRMED,
        resolution_state=OutcomeResolutionState.RESOLVED,
        journey_id=jny_id
    ))

    # Simulate process restart by completely clearing in-memory caches
    governance_registry._evaluations.clear()
    governance_registry._recommendations.clear()
    governance_registry._human_decisions.clear()
    governance_registry._operational_actions.clear()
    governance_registry._operational_outcomes.clear()

    try:
        # 1. Authoritative DB getters must successfully retrieve records after restart
        db_rec = governance_registry.get_recommendation(rec_id)
        assert db_rec is not None and db_rec.recommendation_id == rec_id
        assert db_rec.journey_id == jny_id
        assert db_rec.recommendation_content == "Persisted Rec Content"

        db_dec = governance_registry.get_human_decision(dec_id)
        assert db_dec is not None and db_dec.decision_id == dec_id

        db_act = governance_registry.get_operational_action(act_id)
        assert db_act is not None and db_act.action_id == act_id
        assert db_act.parameters.get("foo") == "bar"

        db_out = governance_registry.get_operational_outcome(out_id)
        assert db_out is not None and db_out.outcome_id == out_id
        assert db_out.expected_outcome == {"delivered": True}

        # 2. Continuity validation must succeed through DB queries even with empty in-memory caches
        governance_registry.validate_upstream_continuity(
            child_journey_id=jny_id,
            parent_id=dec_id,
            parent_type="decision"
        )
        governance_registry.validate_upstream_continuity(
            child_journey_id=jny_id,
            parent_id=act_id,
            parent_type="action"
        )
    finally:
        db = SessionLocal()
        db.query(OperationalOutcomeModel).filter(OperationalOutcomeModel.outcome_id == out_id).delete()
        db.query(OperationalActionModel).filter(OperationalActionModel.action_id == act_id).delete()
        db.query(HumanDecisionModel).filter(HumanDecisionModel.decision_id == dec_id).delete()
        db.query(RecommendationModel).filter(RecommendationModel.recommendation_id == rec_id).delete()
        db.query(RuleEvaluationModel).filter(RuleEvaluationModel.evaluation_id == eval_id).delete()
        db.commit()
        db.close()


@pytest.mark.governance
def test_a026_readiness_gate_positive_and_negative(monkeypatch):
    """
    Audit 4 Item 7: Test readiness gate positive case (200 OK with valid user, role, scope)
    and negative case (503 Service Unavailable when user lacks assigned operational role).
    """
    from app.db.database import SessionLocal
    from app.models.user import User, UserRole
    from app.core.security import get_password_hash
    import uuid

    db = SessionLocal()
    uid = uuid.uuid4().hex[:6]
    active_email = f"ready_active_{uid}@sbnsentinel.com"
    unassigned_email = f"ready_unassigned_{uid}@sbnsentinel.com"

    u_active = User(
        email=active_email,
        hashed_password=get_password_hash("Test@123"),
        full_name="Active Admin",
        role=UserRole.SYSTEM_ADMINISTRATOR.value,
        is_active=True
    )
    u_unassigned = User(
        email=unassigned_email,
        hashed_password=get_password_hash("Test@123"),
        full_name="Unassigned User",
        role=UserRole.UNASSIGNED.value,
        is_active=True
    )

    from app.models.connector import ConnectorModel
    from datetime import datetime
    pf = ConnectorModel(
        id=f"CONN-PF-{uuid.uuid4().hex[:6]}", name="Practice Fusion EHR", type="EHR",
        status="Healthy", latency_ms=45, last_sync=datetime.utcnow(), config={"client_id": "test", "base_url": "http://test"}
    )

    db.add_all([u_active, u_unassigned, pf])
    db.commit()

    from app.integrations.core.secrets import SigningKeyProvider
    monkeypatch.setattr(SigningKeyProvider, "is_configured", lambda: True)

    try:
        # Positive case: valid active operational role -> 200
        res = client.post("/api/v1/auth/login", json={"email": active_email, "password": "Test@123"})
        token = res.json()["access_token"]
        ready_res = client.get("/api/v1/health/ready", headers={"Authorization": f"Bearer {token}"})
        assert ready_res.status_code == 200
        assert ready_res.json()["ready"] is True

        # Negative case: unassigned role user -> 503
        res_un = client.post("/api/v1/auth/login", json={"email": unassigned_email, "password": "Test@123"})
        token_un = res_un.json()["access_token"]
        ready_un = client.get("/api/v1/health/ready", headers={"Authorization": f"Bearer {token_un}"})
        assert ready_un.status_code == 503
        assert ready_un.json()["detail"]["checks"]["role"] is False

        # Negative case: unauthenticated request -> 401
        ready_no_auth = client.get("/api/v1/health/ready")
        assert ready_no_auth.status_code in [401, 403]
    finally:
        db.delete(u_active)
        db.delete(u_unassigned)
        db.delete(pf)
        db.commit()
        db.close()


@pytest.mark.governance
def test_a025b_real_two_process_restart():
    """
    Audit 4 Item 2 (Completion Proof):
    Real two-process test for durable persistence.
    Process A creates governed chain and exits.
    Process B starts in fresh interpreter against same DB and retrieves exact data.
    """
    import subprocess
    import sys
    import os
    import json

    helpers_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../helpers"))
    write_script = os.path.join(helpers_dir, "write_governed_fixture.py")
    read_script = os.path.join(helpers_dir, "read_and_assert_fixture.py")

    # Process A
    res_a = subprocess.run([sys.executable, write_script], capture_output=True, text=True, check=True)
    out_a = res_a.stdout.strip().splitlines()[-1]
    data = json.loads(out_a)

    # Process B
    res_b = subprocess.run([sys.executable, read_script, data["jny_id"], data["rec_id"], data["dec_id"]], capture_output=True, text=True)
    assert res_b.returncode == 0, f"Process B failed: {res_b.stderr} {res_b.stdout}"
    assert "SUCCESS" in res_b.stdout

    # Forced DB failure during record_* must raise / not produce a false success.
    from app.services.governance_registry import governance_registry, RuleEvaluationRecord
    from datetime import datetime
    import warnings
    from sqlalchemy.exc import SAWarning

    raised = False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SAWarning)
            governance_registry.record_evaluation(RuleEvaluationRecord(
                evaluation_id=None,  # NULL primary key -> DB constraint violation
                decision_context_id="CTX-FAIL", policy_id="POL-FAIL", policy_version="V1",
                rule_id="RULE-FAIL", rule_version="V1", result="CONDITION_MET",
                evaluation_timestamp=datetime.utcnow(), input_values={}, journey_id="JNY-FAIL"
            ))
    except Exception:
        raised = True
    assert raised, "record_evaluation with NULL primary key must raise an exception."


@pytest.mark.governance
def test_a021_cross_process_historical_reconstruction():
    """
    Audit 4 Item 4: Real cross-process historical reconstruction.
    Process A writes V1 journey and V2 active rules.
    Process B reconstructs V1 journey in fresh interpreter.
    """
    import subprocess
    import sys
    import os
    import json

    helpers_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../helpers"))
    write_script = os.path.join(helpers_dir, "reconstruction_v1_write.py")
    read_script = os.path.join(helpers_dir, "reconstruction_v2_read.py")

    res_a = subprocess.run([sys.executable, write_script], capture_output=True, text=True, check=True)
    out_a = res_a.stdout.strip().splitlines()[-1]
    data = json.loads(out_a)

    res_b = subprocess.run([sys.executable, read_script, data["jny_id"], data["rec_id"], data["eval_id"]], capture_output=True, text=True)
    assert res_b.returncode == 0, f"Process B failed: {res_b.stderr} {res_b.stdout}"
    assert "SUCCESS" in res_b.stdout


@pytest.mark.governance
def test_a021b_reconstruction_missing_dependency():
    """
    Audit 4 Item 4 (Completion Proof):
    Explicit missing-exact-dependency cases must each return NOT_REPRODUCIBLE.
    Tests:
    - Missing Policy version in registry -> NOT_REPRODUCIBLE
    - Missing Rule version in registry -> NOT_REPRODUCIBLE
    - Missing Mapping version in registry -> NOT_REPRODUCIBLE
    No fallback to any current/active version is permitted.
    """
    from app.services.reconstruction_engine import reconstruction_engine
    from app.services.governance_registry import (
        governance_registry, PolicyVersion, RuleVersion, LifecycleState,
        RecommendationMapping, AuthorityRequirement
    )
    from app.db.database import SessionLocal
    from app.models.governance_storage import RecommendationModel, RuleEvaluationModel
    import json
    import uuid
    from datetime import datetime

    def _make_journey(
        db, uid_prefix, policy_id, policy_version,
        rule_id, rule_version, mapping_id, mapping_version
    ):
        """Helper: insert a minimal journey into the DB and return (journey_id, eval_id, rec_id)."""
        uid = uuid.uuid4().hex[:6]
        journey_id = f"JNY-MISS-{uid_prefix}-{uid}"
        eval_id = f"EVAL-MISS-{uid_prefix}-{uid}"
        rec_id = f"REC-MISS-{uid_prefix}-{uid}"
        db.add(RuleEvaluationModel(
            evaluation_id=eval_id,
            decision_context_id="CTX-MISS",
            policy_id=policy_id,
            policy_version=policy_version,
            rule_id=rule_id,
            rule_version=rule_version,
            result="CONDITION_MET",
            evaluation_timestamp=datetime.utcnow().isoformat(),
            input_values_json=json.dumps({"primary_context": "Op"}),
            journey_id=journey_id
        ))
        db.add(RecommendationModel(
            recommendation_id=rec_id,
            decision_context_id="CTX-MISS",
            rule_evaluation_id=eval_id,
            journey_id=journey_id,
            mapping_id=mapping_id,
            mapping_version=mapping_version,
            content="Test",
            status="ACTIVE",
            priority="High",
            generated_at=datetime.utcnow().isoformat()
        ))
        db.commit()
        return journey_id, eval_id, rec_id

    db = SessionLocal()
    created_evals = []
    created_recs = []

    uid_base = uuid.uuid4().hex[:6]
    pol_a = f"POL-MISS-A-{uid_base}"
    rule_a_id = f"RULE-MISS-A-{uid_base}"
    map_a_id = f"MAP-MISS-A-{uid_base}"

    pol_b = f"POL-MISS-B-{uid_base}"
    rule_b_id = f"RULE-MISS-B-{uid_base}"
    map_b_id = f"MAP-MISS-B-{uid_base}"

    pol_c = f"POL-MISS-C-{uid_base}"
    rule_c_id = f"RULE-MISS-C-{uid_base}"
    map_c_id = f"MAP-MISS-C-{uid_base}"

    try:
        # ---- Case A: Policy version NOT in registry, rule/mapping present ----
        rule_a = RuleVersion(
            rule_id=rule_a_id, version="V-A",
            logic_description="", lifecycle_state=LifecycleState.ACTIVE,
            inputs=[], allowed_outputs=[],
            governing_policy_id=pol_a, governing_policy_version="V-ABSENT"
        )
        mapping_a = RecommendationMapping(
            mapping_id=map_a_id, version="V-A",
            applicable_rule_id=rule_a_id,
            eligible_result="CONDITION_MET",
            recommendation_template="Case A Action",
            authority_requirement=AuthorityRequirement.INFORMATIONAL,
            priority="High",
            lifecycle_state=LifecycleState.ACTIVE
        )
        governance_registry.register_rule(rule_a)
        governance_registry.register_recommendation_mapping(mapping_a)
        # Deliberately do NOT register policy version "V-ABSENT"

        jny_a, eval_a, rec_a = _make_journey(
            db, "A",
            pol_a, "V-ABSENT",
            rule_a_id, "V-A",
            map_a_id, "V-A"
        )
        created_evals.append(eval_a)
        created_recs.append(rec_a)

        res_a = reconstruction_engine.reproduce_decision(rec_a)
        assert res_a.status == "NOT_REPRODUCIBLE", (
            f"Case A: expected NOT_REPRODUCIBLE when policy version absent, got {res_a.status}. "
            f"Diff: {res_a.differences}"
        )
        assert "policy" in str(res_a.differences).lower() or "NOT_REPRODUCIBLE" in res_a.status

        # ---- Case B: Rule version NOT in registry, policy/mapping present ----
        policy_b = PolicyVersion(
            policy_id=pol_b, version="V-B",
            content="Case B Policy", lifecycle_state=LifecycleState.ACTIVE
        )
        mapping_b = RecommendationMapping(
            mapping_id=map_b_id, version="V-B",
            applicable_rule_id=rule_b_id,
            eligible_result="CONDITION_MET",
            recommendation_template="Case B Action",
            authority_requirement=AuthorityRequirement.INFORMATIONAL,
            priority="High",
            lifecycle_state=LifecycleState.ACTIVE
        )
        governance_registry.register_policy(policy_b)
        governance_registry.register_recommendation_mapping(mapping_b)
        # Deliberately do NOT register rule version "V-ABSENT-RULE"

        jny_b, eval_b, rec_b = _make_journey(
            db, "B",
            pol_b, "V-B",
            rule_b_id, "V-ABSENT-RULE",
            map_b_id, "V-B"
        )
        created_evals.append(eval_b)
        created_recs.append(rec_b)

        res_b = reconstruction_engine.reproduce_decision(rec_b)
        assert res_b.status == "NOT_REPRODUCIBLE", (
            f"Case B: expected NOT_REPRODUCIBLE when rule version absent, got {res_b.status}. "
            f"Diff: {res_b.differences}"
        )

        # ---- Case C: Mapping version NOT in registry, policy/rule present ----
        policy_c = PolicyVersion(
            policy_id=pol_c, version="V-C",
            content="Case C Policy", lifecycle_state=LifecycleState.ACTIVE
        )
        rule_c = RuleVersion(
            rule_id=rule_c_id, version="V-C",
            logic_description="", lifecycle_state=LifecycleState.ACTIVE,
            inputs=[], allowed_outputs=[],
            governing_policy_id=pol_c, governing_policy_version="V-C"
        )
        governance_registry.register_policy(policy_c)
        governance_registry.register_rule(rule_c)
        # Deliberately do NOT register mapping version "V-ABSENT-MAP"

        jny_c, eval_c, rec_c = _make_journey(
            db, "C",
            pol_c, "V-C",
            rule_c_id, "V-C",
            map_c_id, "V-ABSENT-MAP"
        )
        created_evals.append(eval_c)
        created_recs.append(rec_c)

        res_c = reconstruction_engine.reproduce_decision(rec_c)
        assert res_c.status == "NOT_REPRODUCIBLE", (
            f"Case C: expected NOT_REPRODUCIBLE when mapping version absent, got {res_c.status}. "
            f"Diff: {res_c.differences}"
        )

    finally:
        from app.models.governance_storage import GovernedPolicyVersionModel
        governance_registry._policies = [p for p in governance_registry._policies if not p.policy_id.startswith("POL-MISS")]
        governance_registry._rules = [r for r in governance_registry._rules if not r.rule_id.startswith("RULE-MISS")]
        governance_registry._recommendation_mappings = [m for m in governance_registry._recommendation_mappings if not m.mapping_id.startswith("MAP-MISS")]
        db.query(GovernedPolicyVersionModel).filter(GovernedPolicyVersionModel.policy_id.like("POL-MISS%")).delete(synchronize_session=False)
        for eid in created_evals:
            db.query(RuleEvaluationModel).filter(RuleEvaluationModel.evaluation_id == eid).delete()
        for rid in created_recs:
            db.query(RecommendationModel).filter(RecommendationModel.recommendation_id == rid).delete()
        db.commit()
        db.close()


@pytest.mark.governance
def test_a026b_practice_fusion_readiness(monkeypatch):
    """
    Audit 4 Item 7: Practice Fusion Readiness
    - No PF ConnectorModel in DB -> 503, pf=false
    - Only unrelated connector -> 503, pf=false
    - PF record exists but no credential -> 503
    - PF status is Warning -> 503
    - Properly configured, real Healthy PF connector -> 200, pf=true
    """
    from app.db.database import SessionLocal
    from app.models.user import User, UserRole
    from app.core.security import get_password_hash
    from app.models.connector import ConnectorModel
    import uuid
    from datetime import datetime

    # We need TestClient
    from fastapi.testclient import TestClient
    from app.main import app
    local_client = TestClient(app)

    db = SessionLocal()
    uid = uuid.uuid4().hex[:6]
    admin_email = f"pf_ready_{uid}@sbnsentinel.com"

    u_admin = User(
        email=admin_email,
        hashed_password=get_password_hash("Test@123"),
        full_name="PF Admin",
        role=UserRole.SYSTEM_ADMINISTRATOR.value,
        is_active=True
    )
    db.add(u_admin)
    db.commit()

    from app.integrations.core.secrets import SigningKeyProvider
    monkeypatch.setattr(SigningKeyProvider, "is_configured", lambda: True)

    try:
        # Get auth token
        res = local_client.post("/api/v1/auth/login", json={"email": admin_email, "password": "Test@123"})
        token = res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 1. No PF ConnectorModel -> 503, pf=false
        db.query(ConnectorModel).delete()
        db.commit()
        r1 = local_client.get("/api/v1/health/ready", headers=headers)
        assert r1.status_code == 503
        assert r1.json()["detail"]["checks"]["pf"] is False

        # 2. Only an unrelated connector exists -> 503, pf=false
        db.add(ConnectorModel(
            id="CONN-OTHER", name="Random CRM", type="CRM", status="Healthy",
            latency_ms=10, last_sync=datetime.utcnow(), config={"client_id": "test", "base_url": "http://test"}
        ))
        db.commit()
        r2 = local_client.get("/api/v1/health/ready", headers=headers)
        assert r2.status_code == 503
        assert r2.json()["detail"]["checks"]["pf"] is False

        # 3. PF record exists but no credential/token -> 503
        pf1 = ConnectorModel(
            id="CONN-PF-TEST", name="Practice Fusion EHR", type="EHR", status="Healthy",
            latency_ms=10, last_sync=datetime.utcnow(), config={}
        )
        db.add(pf1)
        db.commit()
        r3 = local_client.get("/api/v1/health/ready", headers=headers)
        assert r3.status_code == 503

        # 4. PF status is Warning or Disconnected -> 503
        pf1.config = {"client_id": "test", "base_url": "http://test"}
        pf1.status = "Warning"
        db.commit()
        r4 = local_client.get("/api/v1/health/ready", headers=headers)
        assert r4.status_code == 503

        # 5. Properly configured, real Healthy PF connector -> 200, pf=true
        pf1.status = "Healthy"
        db.commit()
        r5 = local_client.get("/api/v1/health/ready", headers=headers)
        assert r5.status_code == 200
        assert r5.json()["ready"] is True

    finally:
        db.delete(u_admin)
        db.query(ConnectorModel).delete()
        db.commit()
        db.close()
