# flake8: noqa: E501
import uuid
import pytest
from datetime import datetime
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.main import app
from app.db.database import SessionLocal
from app.models.user import User
from app.api.deps import get_current_user

from app.models.governance_storage import (
    RecommendationModel, RuleEvaluationModel, HumanDecisionModel,
    OperationalActionModel, ExecutionAttemptModel, OperationalOutcomeModel,
    GovernedPolicyVersionModel, GovernedRuleVersionModel, GovernedRecommendationMappingModel
)
from app.models.evidence import EvidenceModel
from app.models.decision_context_models import ContextEvidenceModel

from app.services.governance_registry import (
    governance_registry, PolicyVersion, RuleVersion, LifecycleState,
    RecommendationMapping, AuthorityRequirement
)


@pytest.fixture(scope="module")
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def mock_get_current_user():
    return User(id=1, email="admin@sbnsentinel.com", role="System Administrator", is_active=True)


def mock_get_ordinary_user():
    u = User(id=2, email="user@sbnsentinel.com", role="Organization Administrator", is_active=True)
    u.org_id = "SYSTEM_GLOBAL"
    u.clinic_id = None
    return u


@pytest.fixture
def override_deps():
    app.dependency_overrides[get_current_user] = mock_get_current_user
    yield
    app.dependency_overrides.clear()


def test_d8_historical_chain_and_reproduction(client: TestClient, db_session: Session, override_deps):
    uid = uuid.uuid4().hex[:6]
    jny_id = f"JNY-D8-{uid}"
    ctx_id = f"CTX-D8-{uid}"
    pol_id = f"POL-D8-{uid}"
    rule_id = f"RULE-D8-{uid}"
    map_id = f"MAP-D8-{uid}"
    eval_id = f"EVAL-D8-{uid}"
    rec_id = f"REC-D8-{uid}"
    dec_id = f"DEC-D8-{uid}"
    act_id = f"ACT-D8-{uid}"
    att_id = f"ATT-D8-{uid}"
    out_id = f"OUT-D8-{uid}"
    orphan_rec_id = f"REC-ORPHAN-{uid}"

    # Clear previous mock policies just in case
    governance_registry._policies = [p for p in governance_registry._policies if not p.policy_id.startswith("POL-D8")]
    governance_registry._rules = [r for r in governance_registry._rules if not r.rule_id.startswith("RULE-D8")]
    governance_registry._recommendation_mappings = [m for m in governance_registry._recommendation_mappings if not m.mapping_id.startswith("MAP-D8")]

    # Register V1
    policy_v1 = PolicyVersion(policy_id=pol_id, version="V1", content="V1 Policy", lifecycle_state=LifecycleState.ACTIVE)
    rule_v1 = RuleVersion(rule_id=rule_id, version="V1", logic_description="", lifecycle_state=LifecycleState.ACTIVE, inputs=[], allowed_outputs=[], governing_policy_id=pol_id, governing_policy_version="V1")
    mapping_v1 = RecommendationMapping(mapping_id=map_id, version="V1", applicable_rule_id=rule_id, eligible_result="CONDITION_MET", recommendation_template="Action V1", authority_requirement=AuthorityRequirement.INFORMATIONAL, priority="High", lifecycle_state=LifecycleState.ACTIVE)

    governance_registry.register_policy(policy_v1)
    governance_registry.register_rule(rule_v1)
    governance_registry.register_recommendation_mapping(mapping_v1)

    # Register V2 (Materially different)
    policy_v2 = PolicyVersion(policy_id=pol_id, version="V2", content="V2 Policy", lifecycle_state=LifecycleState.ACTIVE)
    rule_v2 = RuleVersion(rule_id=rule_id, version="V2", logic_description="", lifecycle_state=LifecycleState.ACTIVE, inputs=[], allowed_outputs=[], governing_policy_id=pol_id, governing_policy_version="V2")
    mapping_v2 = RecommendationMapping(mapping_id=map_id, version="V2", applicable_rule_id=rule_id, eligible_result="CONDITION_MET", recommendation_template="Action V2", authority_requirement=AuthorityRequirement.APPROVAL_REQUIRED, priority="Critical", lifecycle_state=LifecycleState.ACTIVE)

    governance_registry.register_policy(policy_v2)
    governance_registry.register_rule(rule_v2)
    governance_registry.register_recommendation_mapping(mapping_v2)

    # Insert Historical V1 State
    try:
        from app.models.signal import SignalModel
        db_session.add(SignalModel(id=f"SIG-D8-{uid}", type="test_signal", org_id="SYSTEM_GLOBAL", metadata_data={}))
        db_session.add(EvidenceModel(evidence_id=f"EVID-{uid}", canonical_entity="patient", fact_key="status", fact_value_str="admitted", source_connector="emr", retrieval_timestamp=datetime.utcnow(), version=3))
        db_session.add(ContextEvidenceModel(context_id=ctx_id, id=f"EVID-{uid}", evidence_type="mock", evidence_value="test"))
        db_session.add(RuleEvaluationModel(evaluation_id=eval_id, decision_context_id=ctx_id, rule_id=rule_id, rule_version="V1", policy_id=pol_id, policy_version="V1", result="CONDITION_MET", evaluation_timestamp=datetime.utcnow().isoformat(), journey_id=jny_id, input_values_json='{}'))
        db_session.add(RecommendationModel(recommendation_id=rec_id, decision_context_id=ctx_id, journey_id=jny_id, rule_evaluation_id=eval_id, mapping_id=map_id, mapping_version="V1", priority="High", content="Action V1", status="active", generated_at=datetime.utcnow().isoformat(), intended_target_reference=f"SIG-D8-{uid}"))
        db_session.add(HumanDecisionModel(decision_id=dec_id, recommendation_id=rec_id, journey_id=jny_id, actor_id="ACTOR", decision_type="APPROVED", status="RECORDED", decision_timestamp="2026-09-23T12:00:00Z"))
        db_session.add(OperationalActionModel(action_id=act_id, authorization_reference=dec_id, journey_id=jny_id, action_type="NOTIFY", target_reference="TGT", parameters_json='{}', status="COMPLETED"))
        db_session.add(ExecutionAttemptModel(attempt_id=att_id, action_id=act_id, journey_id=jny_id, attempt_number=1, result="SUCCESS"))
        db_session.add(OperationalOutcomeModel(outcome_id=out_id, action_id=act_id, journey_id=jny_id, confirmation_state="CONFIRMED", resolution_state="RESOLVED"))

        # Broken relationship fixture: recommendation pointing to non-existent rule evaluation
        db_session.add(RecommendationModel(recommendation_id=orphan_rec_id, decision_context_id=ctx_id, journey_id=jny_id, rule_evaluation_id=f"MISSING-EVAL-{uid}", mapping_id=map_id, mapping_version="V1", priority="Low", content="Unlinked Rec", status="active", generated_at=datetime.utcnow().isoformat()))
        db_session.commit()

        # Test 1: Historical Chain Verification & Vocabulary Contract
        resp_chain = client.get(f"/api/v1/history/recommendations/{rec_id}")
        assert resp_chain.status_code == 200
        chain = resp_chain.json()

        # technical_state contract
        assert chain["technical_state"] == "valid"

        # exact Evidence version preserved
        assert chain["bindings"]["decision_context_id"] == ctx_id
        assert chain["bindings"]["evidence_refs"][0]["evidence_id"] == f"EVID-{uid}"
        assert chain["bindings"]["evidence_refs"][0]["version"] == "3"

        # Policy & Rule evaluation
        assert chain["bindings"]["rule_evaluations"][0]["rule_id"] == rule_id
        assert chain["bindings"]["recommendations"][0]["recommendation_id"] == rec_id

        # Full Human Decision fields: actor + timestamp round-trip
        assert chain["bindings"]["decisions"][0]["decision_id"] == dec_id
        assert chain["bindings"]["decisions"][0]["actor_id"] == "ACTOR"
        assert chain["bindings"]["decisions"][0]["decision_type"] == "APPROVED"
        assert chain["bindings"]["decisions"][0]["status"] == "RECORDED"
        assert chain["bindings"]["decisions"][0]["timestamp"] == "2026-09-23T12:00:00Z"

        # Action → Attempts → Outcome historical DTO
        assert len(chain["bindings"]["actions"]) == 1
        act = chain["bindings"]["actions"][0]
        assert act["action_id"] == act_id
        assert act["action_type"] == "NOTIFY"
        assert act["status"] == "COMPLETED"
        assert len(act["attempts"]) == 1
        assert act["attempts"][0]["attempt_id"] == att_id
        assert int(act["attempts"][0]["attempt_number"]) == 1
        assert act["attempts"][0]["result"] == "SUCCESS"
        assert act["outcome"] is not None
        assert act["outcome"]["outcome_id"] == out_id
        assert act["outcome"]["confirmation_state"] == "CONFIRMED"
        assert act["outcome"]["resolution_state"] == "RESOLVED"

        # Test Broken Parent/Child Relationship: Incomplete chain is NOT guessed
        resp_orphan = client.get(f"/api/v1/history/recommendations/{orphan_rec_id}")
        assert resp_orphan.status_code == 200
        orphan_chain = resp_orphan.json()
        assert orphan_chain["technical_state"] == "orphaned"
        assert orphan_chain["bindings"]["rule_evaluations"] == []
        assert orphan_chain["bindings"]["policy"] is None
        assert orphan_chain["bindings"]["decisions"] == []
        assert orphan_chain["bindings"]["actions"] == []

        # Take a snapshot of counts for read-only assurance
        recs_count = db_session.query(RecommendationModel).count()
        decs_count = db_session.query(HumanDecisionModel).count()
        acts_count = db_session.query(OperationalActionModel).count()

        # Test 2: MATCH Reproduction (Ensures V1 isolation despite V2 existing)
        from app.services.rules_engine import rules_engine
        rules_engine.register_handler(rule_id, lambda d: "CONDITION_MET")

        resp_repro = client.get(f"/api/v1/history/recommendations/{rec_id}/reproduction")
        assert resp_repro.status_code == 200
        repro = resp_repro.json()
        assert repro["status"] == "MATCH"
        assert repro["reproduced"]["action"] == "Action V1"

        # Test 3: MISMATCH Reproduction
        # We mutate the historical input in DB to cause a mismatch (different output Action)
        rec_record = db_session.query(RecommendationModel).filter_by(recommendation_id=rec_id).first()
        rec_record.content = "Action V-Modified"
        db_session.commit()

        resp_mismatch = client.get(f"/api/v1/history/recommendations/{rec_id}/reproduction")
        assert resp_mismatch.status_code == 200
        mismatch = resp_mismatch.json()
        assert mismatch["status"] == "MISMATCH"
        assert mismatch["differences"] is not None

        # Restore
        rec_record.content = "Action V1"
        db_session.commit()

        # Test 4: NOT_REPRODUCIBLE (Missing Policy)
        eval_record = db_session.query(RuleEvaluationModel).filter_by(evaluation_id=eval_id).first()
        eval_record.policy_version = "V-MISSING"
        db_session.commit()
        resp_miss_pol = client.get(f"/api/v1/history/recommendations/{rec_id}/reproduction")
        assert resp_miss_pol.json()["status"] == "NOT_REPRODUCIBLE"
        assert resp_miss_pol.json()["diagnostic"]["code"] == "MISSING_POLICY"
        eval_record.policy_version = "V1"
        db_session.commit()

        # Test 5: NOT_REPRODUCIBLE (Missing Rule)
        eval_record.rule_version = "V-MISSING"
        db_session.commit()
        resp_miss_rule = client.get(f"/api/v1/history/recommendations/{rec_id}/reproduction")
        assert resp_miss_rule.json()["status"] == "NOT_REPRODUCIBLE"
        assert resp_miss_rule.json()["diagnostic"]["code"] == "MISSING_RULE"
        eval_record.rule_version = "V1"
        db_session.commit()

        # Test 6: NOT_REPRODUCIBLE (Missing Mapping - presentation/data)
        rec_record.mapping_version = "V-MISSING"
        db_session.commit()
        resp_miss_map = client.get(f"/api/v1/history/recommendations/{rec_id}/reproduction")
        miss_map_json = resp_miss_map.json()
        assert miss_map_json["status"] == "NOT_REPRODUCIBLE"
        assert miss_map_json["diagnostic"]["code"] == "MISSING_MAPPING"
        assert miss_map_json["diagnostic"]["missing_dependency"]["type"] == "RecommendationMapping"
        assert miss_map_json["diagnostic"]["missing_dependency"]["id"] == map_id
        assert miss_map_json["diagnostic"]["missing_dependency"]["version"] == "V-MISSING"
        rec_record.mapping_version = "V1"
        db_session.commit()

        # Read-only Assurance
        # Ensure no new records were created in DB during these GET requests
        assert db_session.query(RecommendationModel).count() == recs_count
        assert db_session.query(HumanDecisionModel).count() == decs_count
        assert db_session.query(OperationalActionModel).count() == acts_count

        # Test 7: Diagnostic Authorization (Negative Test)
        # Ordinary user should not see diagnostic payload, but status should remain NOT_REPRODUCIBLE
        app.dependency_overrides[get_current_user] = mock_get_ordinary_user

        # We test with the missing mapping state from Test 6
        rec_record.mapping_version = "V-MISSING"
        db_session.commit()
        resp_ordinary = client.get(f"/api/v1/history/recommendations/{rec_id}/reproduction")
        assert resp_ordinary.status_code == 200
        ordinary_json = resp_ordinary.json()
        assert ordinary_json["status"] == "NOT_REPRODUCIBLE"
        assert ordinary_json["diagnostic"] is None

        # Restore dependencies
        app.dependency_overrides[get_current_user] = mock_get_current_user
        rec_record.mapping_version = "V1"
        db_session.commit()

    finally:
        db_session.query(OperationalOutcomeModel).filter_by(outcome_id=out_id).delete()
        db_session.query(ExecutionAttemptModel).filter_by(attempt_id=att_id).delete()
        db_session.query(OperationalActionModel).filter_by(action_id=act_id).delete()
        db_session.query(HumanDecisionModel).filter_by(decision_id=dec_id).delete()
        db_session.query(RecommendationModel).filter_by(recommendation_id=rec_id).delete()
        db_session.query(RecommendationModel).filter_by(recommendation_id=orphan_rec_id).delete()
        from app.models.signal import SignalModel
        db_session.query(SignalModel).filter_by(id=f"SIG-D8-{uid}").delete()
        db_session.query(RuleEvaluationModel).filter_by(evaluation_id=eval_id).delete()
        db_session.query(ContextEvidenceModel).filter_by(context_id=ctx_id).delete()
        db_session.query(EvidenceModel).filter_by(evidence_id=f"EVID-{uid}").delete()
        db_session.query(GovernedPolicyVersionModel).filter(GovernedPolicyVersionModel.policy_id == pol_id).delete(synchronize_session=False)
        db_session.query(GovernedRuleVersionModel).filter(GovernedRuleVersionModel.rule_id == rule_id).delete(synchronize_session=False)
        db_session.query(GovernedRecommendationMappingModel).filter(GovernedRecommendationMappingModel.mapping_id == map_id).delete(synchronize_session=False)
        db_session.commit()
