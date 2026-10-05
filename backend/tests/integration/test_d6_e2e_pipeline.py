import pytest
from fastapi.testclient import TestClient
import uuid
from unittest.mock import patch
from app.main import app
from app.db.database import SessionLocal, Base
from app.models.governance_storage import (
    HumanDecisionModel,
    OperationalActionModel,
    ExecutionAttemptModel,
    OperationalOutcomeModel,
    RecommendationModel,
    RuleEvaluationModel
)
from app.models.organization import OrganizationClinicModel
from app.models.encounter import EncounterModel
from app.models.intelligence import DecisionContextModel, OperationalIntelligenceModel, RevenueIntelligenceModel
from app.models.event import OperationalEventModel
from app.models.governance_storage import GovernedRecommendationMappingModel
from app.services.governance_registry import governance_registry, DecisionType, AuthorityConfiguration
from app.services.processing_orchestrator import ProcessingOrchestrator
from app.schemas.service_communication import ServiceResponse, ServiceStatus
from app.api.deps import get_current_user

client = TestClient(app)


@pytest.fixture(scope="function")
def setup_db():
    Base.metadata.drop_all(bind=SessionLocal().get_bind())
    Base.metadata.create_all(bind=SessionLocal().get_bind())
    governance_registry._policies.clear()
    governance_registry._rules.clear()
    governance_registry._evaluations.clear()
    governance_registry._recommendation_mappings.clear()
    governance_registry._recommendations.clear()
    governance_registry._human_decisions.clear()
    governance_registry._operational_actions.clear()
    governance_registry._execution_attempts.clear()
    governance_registry._operational_outcomes.clear()
    yield
    db = SessionLocal()
    db.query(OperationalOutcomeModel).delete()
    db.query(ExecutionAttemptModel).delete()
    db.query(OperationalActionModel).delete()
    db.query(HumanDecisionModel).delete()
    db.query(RecommendationModel).delete()
    db.query(RuleEvaluationModel).delete()
    db.query(OrganizationClinicModel).delete()
    db.query(EncounterModel).delete()
    db.query(OperationalIntelligenceModel).delete()
    db.query(RevenueIntelligenceModel).delete()
    db.query(DecisionContextModel).delete()
    db.query(OperationalEventModel).delete()
    db.commit()
    db.close()


@pytest.fixture(scope="function")
def mock_admin():
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


@pytest.fixture(scope="function", autouse=True)
def mock_upstream_engines():
    with patch("app.services.processing_orchestrator.evidence_engine.invoke") as mock_ev, \
            patch("app.services.processing_orchestrator.decision_context_engine.invoke") as mock_ctx, \
            patch("app.services.processing_orchestrator.policy_engine.invoke") as mock_pol, \
            patch("app.services.processing_orchestrator.rules_engine.invoke") as mock_rule, \
            patch("app.services.processing_orchestrator.revenue_intelligence_engine.invoke") as mock_rev:

        mock_ev.return_value = ServiceResponse(status=ServiceStatus.SUCCESS, result_payload={"eos_003_package": {}}, correlation_id="mock", processing_time_ms=10)
        mock_ctx.return_value = ServiceResponse(status=ServiceStatus.SUCCESS, result_payload={"primary_context": "NoShow"}, correlation_id="mock", processing_time_ms=10)

        # Mock policy_result object correctly
        class MockPolicyResult:
            is_permitted = True

        mock_pol.return_value = ServiceResponse(status=ServiceStatus.SUCCESS, result_payload={"policy_result": MockPolicyResult(), "policy_version": "V1"}, correlation_id="mock", processing_time_ms=10)
        mock_rule.return_value = ServiceResponse(status=ServiceStatus.SUCCESS, result_payload={"findings": [{"rule_id": "RULE-SCH-001", "result": "CONDITION_MET", "evaluation_id": f"EVAL-{uuid.uuid4().hex[:6]}"}]}, correlation_id="mock", processing_time_ms=10)
        mock_rev.return_value = ServiceResponse(status=ServiceStatus.SUCCESS, result_payload={"estimated_exposure": "$0"}, correlation_id="mock", processing_time_ms=10)
        yield


def test_d6_real_pipeline_target_propagation_positive(setup_db, mock_admin):
    db = SessionLocal()
    journey_id = f"J-{uuid.uuid4().hex[:6]}"
    clinic_id = "ORG-CLINIC-E2E"

    db.add(OrganizationClinicModel(
        id=clinic_id,
        organization_id="org_123",
        name="E2E Clinic",
        is_active=True
    ))
    db.add(GovernedRecommendationMappingModel(
        mapping_id="REC-MAP-001",
        version="V1",
        applicable_rule_id="RULE-SCH-001",
        eligible_result="CONDITION_MET",
        recommendation_template="Test",
        authority_requirement="SYSTEM_ADMIN",
        priority="NORMAL",
        lifecycle_state="ACTIVE",
        allowed_action_types_json='["RESCHEDULE_APPOINTMENT", "SEND_NOTIFICATION"]'
    ))
    db.commit()

    orchestrator = ProcessingOrchestrator()

    event = orchestrator.create_event(
        event_type="EHR",
        source="Practice Fusion",
        raw_payload={
            "detail": "patient no-show",
            "target_reference": clinic_id,
            "target_type": "CLINIC"
        },
        priority="Normal",
        correlation_id=journey_id,
    )

    # Run pipeline in background executor logic
    orchestrator.process_event_background(event.id)

    event = db.query(OperationalEventModel).filter_by(id=event.id).first()
    assert event.state == "Completed"

    # Resolve Recommendation
    recommendation = db.query(RecommendationModel).filter_by(journey_id=journey_id).first()
    assert recommendation is not None
    assert recommendation.intended_target_reference == clinic_id

    # Post Decision
    decision_payload = {
        "recommendation_id": recommendation.recommendation_id,
        "decision_type": "APPROVED",
        "reason": "Proceed with fee and reschedule"
    }
    decision_resp = client.post("/api/v1/decisions/", json=decision_payload)
    assert decision_resp.status_code == 200
    decision_id = decision_resp.json().get("decision_id")

    # D6 Lifecycle Verification
    lifecycle_resp = client.get(f"/api/v1/actions/lifecycle/{decision_id}")
    assert lifecycle_resp.status_code == 200
    lifecycle = lifecycle_resp.json()

    assert lifecycle["can_create"] is True
    permitted_targets = lifecycle["creation"]["permitted_targets"]
    assert any(t["target_id"] == clinic_id for t in permitted_targets)


def test_d6_real_pipeline_target_propagation_negative(setup_db, mock_admin):
    db = SessionLocal()
    journey_id = f"J-{uuid.uuid4().hex[:6]}"

    orchestrator = ProcessingOrchestrator()

    db.add(GovernedRecommendationMappingModel(
        mapping_id="REC-MAP-001",
        version="V1",
        applicable_rule_id="RULE-SCH-001",
        eligible_result="CONDITION_MET",
        recommendation_template="Test",
        authority_requirement="SYSTEM_ADMIN",
        priority="NORMAL",
        lifecycle_state="ACTIVE",
        allowed_action_types_json='["RESCHEDULE_APPOINTMENT", "SEND_NOTIFICATION"]'
    ))
    db.commit()

    # Negative Variant: no explicit target
    event = orchestrator.create_event(
        event_type="EHR",
        source="Practice Fusion",
        raw_payload={
            "detail": "patient no-show",
        },
        priority="Normal",
        correlation_id=journey_id,
    )

    orchestrator.process_event_background(event.id)

    event = db.query(OperationalEventModel).filter_by(id=event.id).first()
    assert event.state == "Completed"

    # Resolve Recommendation
    recommendation = db.query(RecommendationModel).filter_by(journey_id=journey_id).first()
    assert recommendation is not None
    assert recommendation.intended_target_reference is None

    # Post Decision
    decision_payload = {
        "recommendation_id": recommendation.recommendation_id,
        "decision_type": "APPROVED",
        "reason": "Proceed"
    }
    decision_resp = client.post("/api/v1/decisions/", json=decision_payload)
    assert decision_resp.status_code == 200
    decision_id = decision_resp.json().get("decision_id")

    # D6 Lifecycle Verification
    lifecycle_resp = client.get(f"/api/v1/actions/lifecycle/{decision_id}")
    assert lifecycle_resp.status_code == 200
    lifecycle = lifecycle_resp.json()

    # Since there is no explicit target -> intended_target_reference is null -> can_create is false
    assert lifecycle["can_create"] is False
    assert len(lifecycle["creation"]["permitted_targets"]) == 0
