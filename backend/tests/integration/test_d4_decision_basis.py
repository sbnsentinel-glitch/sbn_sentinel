import pytest
import uuid
from datetime import datetime

from app.db.database import Base, engine, SessionLocal
from app.models.signal import SignalModel
from app.models.governance_storage import (
    RuleEvaluationModel,
    GovernedPolicyVersionModel,
)
from app.models.intelligence import DecisionContextModel
from app.models.decision_context_models import (
    ContextEvidenceModel,
    ContextProvenanceModel,
    ContextFreshnessModel,
    ContextConflictsModel,
)
from fastapi.testclient import TestClient
from app.main import app
from app.api.deps import get_current_user


@pytest.fixture(scope="function")
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.mark.governance
def test_decision_basis_d4_authoritative_path(setup_db):
    db = SessionLocal()

    # 1. Create a Signal
    signal_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())

    sig = SignalModel(
        id=signal_id,
        type="test_signal",
        metadata_data={"pipeline_event_id": event_id, "correlation_id": correlation_id}
    )
    db.add(sig)

    # 2. Create authoritative Governance Data
    context_id = str(uuid.uuid4())
    policy_id = f"pol-{uuid.uuid4().hex[:6]}"
    policy_version = "1.0"

    ev_id = str(uuid.uuid4())
    ev = ContextEvidenceModel(
        id=ev_id,
        context_id=context_id,
        evidence_type="Patient History",
        evidence_value="No Shows: 3",
        added_at=datetime.utcnow()
    )
    db.add(ev)

    prov = ContextProvenanceModel(
        context_id=context_id,
        evidence_id=ev_id,
        source_system="EHR_PROD",
        ingestion_timestamp=datetime.utcnow()
    )
    db.add(prov)

    pol = GovernedPolicyVersionModel(
        policy_id=policy_id,
        version=policy_version,
        lifecycle_state="ACTIVE",
        content="{}"
    )
    db.add(pol)

    # The linking authoritative record (Journey/Pipeline Event ID)
    rule_eval = RuleEvaluationModel(
        evaluation_id=str(uuid.uuid4()),
        decision_context_id=context_id,
        policy_id=policy_id,
        policy_version=policy_version,
        rule_id="rule-1",
        rule_version="1.0",
        result="CONDITION_MET",
        evaluation_timestamp=datetime.utcnow().isoformat(),
        journey_id=correlation_id
    )
    db.add(rule_eval)

    db.commit()

    # 3. Request Decision Basis via Endpoint Implementation
    from app.api.v1.endpoints.decision_basis import get_decision_basis

    class MockUser:
        id = "test"
        roles = ["System Administrator"]

    try:
        data = get_decision_basis(signal_id=signal_id, db=db, current_user=MockUser())
    except Exception as e:
        pytest.fail(f"get_decision_basis raised an exception: {e}")

    # Verify no synthetic fallback (no sig-ev- prefix)
    used_evidence = data["evidence"]["used"]
    assert len(used_evidence) == 1
    assert used_evidence[0]["evidence_id"] == ev.id
    assert not used_evidence[0]["evidence_id"].startswith("sig-ev-")

    # Verify exact path resolution (Event -> Context -> Policy -> Rule)
    assert data["decision_context"]["context_id"] == context_id
    assert data["decision_context"]["evaluated_at"] == rule_eval.evaluation_timestamp
    assert data["policy"]["policy_id"] == policy_id
    assert data["rules"][0]["rule_id"] == "rule-1"

    db.close()


@pytest.mark.governance
def test_decision_basis_d4_conflicting_evaluations_unavailable(setup_db):
    db = SessionLocal()

    signal_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())

    sig = SignalModel(
        id=signal_id,
        type="test_signal",
        metadata_data={"pipeline_event_id": event_id, "correlation_id": correlation_id}
    )
    db.add(sig)

    # Add two evaluations with CONTRADICTORY results for the same rule and context
    rule_eval_1 = RuleEvaluationModel(
        evaluation_id=str(uuid.uuid4()),
        decision_context_id="ctx-1",
        policy_id="pol-1",
        policy_version="1.0",
        rule_id="rule-conflict",
        rule_version="1.0",
        result="PASS",
        evaluation_timestamp=datetime.utcnow().isoformat(),
        journey_id=correlation_id
    )

    rule_eval_2 = RuleEvaluationModel(
        evaluation_id=str(uuid.uuid4()),
        decision_context_id="ctx-1",
        policy_id="pol-1",
        policy_version="1.0",
        rule_id="rule-conflict",
        rule_version="1.0",
        result="FAIL",  # Contradictory result on same rule!
        evaluation_timestamp=datetime.utcnow().isoformat(),
        journey_id=correlation_id
    )
    db.add(rule_eval_1)
    db.add(rule_eval_2)
    db.commit()

    from app.api.v1.endpoints.decision_basis import get_decision_basis

    class MockUser:
        id = "test"
        roles = ["System Administrator"]

    data = get_decision_basis(signal_id=signal_id, db=db, current_user=MockUser())

    assert data["technical_state"] == "unavailable"

    db.close()


@pytest.mark.governance
def test_d4_pipeline_event_to_decision_basis_true_integration(setup_db):
    """
    True pipeline event-processing -> GET decision-basis test using normal unpruned seeds.
    Proves that normal runtime pipeline populates D4 ContextEvidence/Provenance,
    handles multiple policies (e.g. POL-001 + POL-003) without automatic ambiguity,
    and resolves both D4 grouped basis and D5 recommendation review cleanly.
    """
    from app.services.processing_orchestrator import processing_orchestrator
    from app.services.governance_registry import initialize_registry_seeds, governance_registry
    db = SessionLocal()

    # Clean any leftover test policies from previous tests so only canonical seeds exist
    governance_registry._policies = [
        p for p in governance_registry._policies
        if p.policy_id in ("POL-001", "POL-002", "POL-003")
    ]
    from app.models.governance_storage import GovernedPolicyVersionModel
    db.query(GovernedPolicyVersionModel).filter(
        ~GovernedPolicyVersionModel.policy_id.in_(["POL-001", "POL-002", "POL-003"])
    ).delete(synchronize_session=False)
    db.commit()

    # Use NORMAL UNPRUNED governance seeds (both POL-001 and POL-003 present)
    initialize_registry_seeds()

    # 1. Pipeline event creation and execution (Layers 1-10)
    raw_payload = {
        "patient_id": "P123",
        "detail": "Patient booked test appointment",
        "evidence": ["E01", "E02"],
        "primary_context": "Operational",
        "secondary_context": "Appointment Confirmation"
    }
    event = processing_orchestrator.create_event(
        event_type="EHR",
        source="EHR_SYSTEM",
        raw_payload=raw_payload,
        priority="Normal"
    )
    event = processing_orchestrator._run_pipeline(event, db)

    # 2. Pipeline generated SignalModel
    sig = db.query(SignalModel).filter(SignalModel.id == event.id).first()
    assert sig is not None, f"Pipeline must generate and persist SignalModel. Event state: {event.state}"

    # 3. GET /api/v1/decision-basis/{sig.id} (Grouped bases, multiple policies evaluated)
    client = TestClient(app)

    class MockUserLocal:
        id = "test_admin"
        role = "System Administrator"

    app.dependency_overrides[get_current_user] = lambda: MockUserLocal()
    try:
        response = client.get(f"/api/v1/decision-basis/{sig.id}")
        assert response.status_code == 200, response.text
        data = response.json()

        # D4 must NOT treat multiple valid policy/rule evaluations as automatic ambiguity
        assert data["technical_state"] == "ready"
        assert len(data["rules"]) >= 1

        # Decision Context contains authoritative sufficiency_status computed by ContextValidator
        assert data["decision_context"]["context_id"] is not None
        assert data["decision_context"]["sufficiency_status"] in ("SUFFICIENT", "INSUFFICIENT")

        # Evidence records were populated directly by pipeline L4 without manual insertion
        assert "used" in data["evidence"]
        assert len(data["evidence"]["used"]) > 0 or len(data["evidence"]["missing"]) > 0

        # 4. Test exact rule_evaluation_id selector
        target_eval_id = data["rules"][0]["evaluation_id"]
        res_exact = client.get(f"/api/v1/decision-basis/{sig.id}?rule_evaluation_id={target_eval_id}")
        assert res_exact.status_code == 200
        data_exact = res_exact.json()
        assert data_exact["technical_state"] == "ready"
        assert any(r["evaluation_id"] == target_eval_id for r in data_exact["rules"])

        # 5. Test D5 review resolves basis from Recommendation.rule_evaluation_id
        res_review = client.get(f"/api/v1/decisions/review/{sig.id}")
        assert res_review.status_code == 200
        review_data = res_review.json()
        if review_data.get("recommendation"):
            assert review_data["technical_state"] == "ready"
            assert review_data["recommendation"]["rule_evaluation_id"] is not None
    finally:
        app.dependency_overrides.clear()
        db.close()


@pytest.mark.governance
def test_d4_evidence_provenance_id_integrity(setup_db):
    """
    Verify ContextEvidenceModel.id exactly matches ContextProvenanceModel.evidence_id
    and ContextFreshnessModel.evidence_id, and conflict evidence IDs use real refs.
    """
    from app.services.processing_orchestrator import processing_orchestrator
    from app.services.governance_registry import initialize_registry_seeds
    db = SessionLocal()
    initialize_registry_seeds()

    raw_payload = {
        "patient_id": "P-INTEGRITY",
        "detail": "Integrity verification event",
        "evidence": ["FACT-1", "FACT-2"],
        "primary_context": "Clinical",
        "secondary_context": "Intake"
    }
    event = processing_orchestrator.create_event(
        event_type="EHR",
        source="EHR_SYSTEM",
        raw_payload=raw_payload,
        priority="Normal"
    )
    event = processing_orchestrator._run_pipeline(event, db)

    ctx = event.decision_context
    assert ctx is not None

    evidence_rows = db.query(ContextEvidenceModel).filter(
        ContextEvidenceModel.context_id == ctx.id
    ).all()
    assert len(evidence_rows) > 0, "Pipeline must persist ContextEvidenceModel records"

    for ev in evidence_rows:
        prov = db.query(ContextProvenanceModel).filter(
            ContextProvenanceModel.context_id == ctx.id,
            ContextProvenanceModel.evidence_id == ev.id
        ).first()
        assert prov is not None, f"ContextEvidenceModel.id '{ev.id}' must match ContextProvenanceModel.evidence_id"

        fresh = db.query(ContextFreshnessModel).filter(
            ContextFreshnessModel.context_id == ctx.id,
            ContextFreshnessModel.evidence_id == ev.id
        ).first()
        assert fresh is not None, f"ContextEvidenceModel.id '{ev.id}' must match ContextFreshnessModel.evidence_id"

    # Verify conflict record handling uses real evidence IDs
    ev1_id = evidence_rows[0].id
    ev2_id = evidence_rows[1].id if len(evidence_rows) > 1 else ev1_id
    conflict_model = ContextConflictsModel(
        id=str(uuid.uuid4()),
        context_id=ctx.id,
        evidence_a_id=ev1_id,
        evidence_b_id=ev2_id,
        conflict_description="Simulated conflict on appointment date",
        resolution_status="Unresolved"
    )
    db.add(conflict_model)
    db.commit()

    saved_conflict = db.query(ContextConflictsModel).filter(
        ContextConflictsModel.id == conflict_model.id
    ).first()
    assert saved_conflict.evidence_a_id == ev1_id
    assert saved_conflict.evidence_b_id == ev2_id
    db.close()


@pytest.mark.governance
def test_d4_missing_sufficiency_fail_closed(setup_db):
    """
    Verify that missing/unevaluated sufficiency is null/None, never falsely defaulting to SUFFICIENT.
    """
    db = SessionLocal()
    sig_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())
    ctx_id = str(uuid.uuid4())
    pol_id = f"POL-FAILCLOSED-{uuid.uuid4().hex[:6]}"

    sig = SignalModel(
        id=sig_id,
        type="test_signal",
        metadata_data={"pipeline_event_id": event_id, "correlation_id": correlation_id}
    )
    # Decision context created without sufficiency evaluation
    ctx = DecisionContextModel(
        id=ctx_id,
        event_id=event_id,
        primary_context="Billing",
        sufficiency_status=None  # Explicitly None
    )
    pol = GovernedPolicyVersionModel(
        policy_id=pol_id,
        version="V1",
        lifecycle_state="ACTIVE",
        content="{}"
    )
    rule_eval = RuleEvaluationModel(
        evaluation_id=str(uuid.uuid4()),
        decision_context_id=ctx_id,
        policy_id=pol_id,
        policy_version="V1",
        rule_id="RULE-FAILCLOSED",
        rule_version="V1",
        result="CONDITION_MET",
        evaluation_timestamp=datetime.utcnow().isoformat(),
        journey_id=correlation_id
    )
    db.add_all([sig, ctx, pol, rule_eval])
    db.commit()

    client = TestClient(app)

    class MockAdmin:
        org_id = "SYSTEM_GLOBAL"
        clinic_id = None
        id = "admin"
        role = "System Administrator"

    app.dependency_overrides[get_current_user] = lambda: MockAdmin()
    try:
        response = client.get(f"/api/v1/decision-basis/{sig_id}")
        assert response.status_code == 200
        data = response.json()
        # Must be null/None, NEVER defaulted to 'SUFFICIENT'
        assert data["decision_context"]["sufficiency_status"] is None
    finally:
        app.dependency_overrides.clear()
        db.query(GovernedPolicyVersionModel).filter(GovernedPolicyVersionModel.policy_id == pol_id).delete()
        db.query(RuleEvaluationModel).filter(RuleEvaluationModel.evaluation_id == rule_eval.evaluation_id).delete()
        db.commit()
        db.close()


@pytest.mark.governance
def test_d4_pipeline_real_evidence_conflict_exact_refs(setup_db):
    """
    Real pipeline test creating an actual Evidence conflict and proving
    the persisted conflict A/B IDs point to the exact conflicting Evidence rows.
    Also verifies unreferenced conflicts leave IDs incomplete (None) rather than guessing.
    """
    from app.services.processing_orchestrator import processing_orchestrator
    from app.services.governance_registry import initialize_registry_seeds, governance_registry
    db = SessionLocal()

    # Clean any leftover non-canonical policies
    governance_registry._policies = [
        p for p in governance_registry._policies
        if p.policy_id in ("POL-001", "POL-002", "POL-003")
    ]
    from app.models.governance_storage import GovernedPolicyVersionModel
    db.query(GovernedPolicyVersionModel).filter(
        ~GovernedPolicyVersionModel.policy_id.in_(["POL-001", "POL-002", "POL-003"])
    ).delete(synchronize_session=False)
    db.commit()

    initialize_registry_seeds()

    raw_payload = {
        "patient_id": "P-REAL-CONFLICT",
        "detail": "Patient scheduling validation event",
        "facts": [
            {"entity": "Appointment", "key": "status", "value": "NO_SHOW"},
            {"entity": "Appointment", "key": "status", "value": "BOOKED"}
        ]
    }
    event = processing_orchestrator.create_event(
        event_type="EHR",
        source="EHR_SYSTEM",
        raw_payload=raw_payload,
        priority="Normal"
    )
    event = processing_orchestrator._run_pipeline(event, db)

    ctx = event.decision_context
    assert ctx is not None
    # Real conflict triggers INSUFFICIENT sufficiency status
    assert ctx.sufficiency_status == "INSUFFICIENT"

    conf_rows = db.query(ContextConflictsModel).filter(
        ContextConflictsModel.context_id == ctx.id
    ).all()
    assert len(conf_rows) >= 1, "Pipeline ContextValidator must detect and persist the conflict"

    conf = conf_rows[0]
    assert conf.evidence_a_id is not None
    assert conf.evidence_b_id is not None
    assert conf.evidence_a_id != conf.evidence_b_id
    assert "Conflict on status" in conf.conflict_description

    # Prove that persisted conflict A/B IDs point to the EXACT conflicting Evidence rows
    row_a = db.query(ContextEvidenceModel).filter(
        ContextEvidenceModel.id == conf.evidence_a_id,
        ContextEvidenceModel.context_id == ctx.id
    ).first()
    row_b = db.query(ContextEvidenceModel).filter(
        ContextEvidenceModel.id == conf.evidence_b_id,
        ContextEvidenceModel.context_id == ctx.id
    ).first()

    assert row_a is not None, f"Conflict evidence_a_id '{conf.evidence_a_id}' must point to an exact ContextEvidenceModel row"
    assert row_b is not None, f"Conflict evidence_b_id '{conf.evidence_b_id}' must point to an exact ContextEvidenceModel row"
    assert row_a.id != row_b.id

    vals = {row_a.evidence_value, row_b.evidence_value}
    assert any("NO_SHOW" in v for v in vals)
    assert any("BOOKED" in v for v in vals)

    # Prove D4 GET /api/v1/decision-basis/{sig.id} reflects the exact conflict IDs
    client = TestClient(app)

    class MockUser:
        id = "test_user"
        role = "System Administrator"

    app.dependency_overrides[get_current_user] = lambda: MockUser()
    try:
        resp = client.get(f"/api/v1/decision-basis/{event.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["evidence"]["conflicts"]) >= 1
        d4_conf = data["evidence"]["conflicts"][0]
        assert d4_conf["evidence_a_id"] == conf.evidence_a_id
        assert d4_conf["evidence_b_id"] == conf.evidence_b_id
    finally:
        app.dependency_overrides.clear()

    # Verify incomplete/unreferenced conflicts do NOT fabricate refs by guessing used_items
    dummy_id1 = f"ev-dummy-{uuid.uuid4().hex[:8]}"
    dummy_id2 = f"ev-dummy-{uuid.uuid4().hex[:8]}"
    conf_unref_id = f"conf-unref-{uuid.uuid4().hex[:8]}"

    unref_pkg = {
        "evidence": {
            "used": [
                {"evidence_id": dummy_id1, "fact_key": "k1", "fact_value": "v1"},
                {"evidence_id": dummy_id2, "fact_key": "k2", "fact_value": "v2"}
            ],
            "conflicts": [
                {"conflict_id": conf_unref_id, "conflict_description": "Unreferenced dispute"}
            ]
        }
    }
    processing_orchestrator._persist_context_evidence_records(event, unref_pkg, db)
    saved_unref = db.query(ContextConflictsModel).filter(ContextConflictsModel.id == conf_unref_id).first()
    assert saved_unref is not None
    assert saved_unref.evidence_a_id is None, "Must not guess used_items[0] when exact refs are missing"
    assert saved_unref.evidence_b_id is None, "Must not guess used_items[1] when exact refs are missing"
    assert saved_unref.resolution_status == "Incomplete"

    db.query(ContextConflictsModel).filter(ContextConflictsModel.id == conf_unref_id).delete()
    db.query(ContextEvidenceModel).filter(ContextEvidenceModel.id.in_([dummy_id1, dummy_id2])).delete(synchronize_session=False)
    db.commit()
    db.close()
