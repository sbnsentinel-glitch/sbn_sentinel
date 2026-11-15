import pytest
import uuid
from app.db.database import SessionLocal
from app.models.governance_storage import (
    RecommendationModel,
    HumanDecisionModel,
    OperationalActionModel,
    OperationalOutcomeModel,
    RuleEvaluationModel
)
from app.api.v1.endpoints.history import _build_historical_lifecycle


@pytest.mark.integration
def test_cp21_cross_journey_continuity_adversarial_detection():
    """
    CP-21 / Ticket 17:
    History chain continuity validation across journey bindings.
    Any cross-journey child row must cause the chain to become orphaned/invalid, never valid.
    """
    db = SessionLocal()
    try:
        journey_a = f"JOURNEY-A-{uuid.uuid4().hex[:6]}"
        journey_b = f"JOURNEY-B-{uuid.uuid4().hex[:6]}"

        rec_id = f"REC-{uuid.uuid4().hex[:6]}"
        eval_id = f"EVAL-{uuid.uuid4().hex[:6]}"
        ctx_id = f"CTX-{uuid.uuid4().hex[:6]}"
        dec_id = f"HD-{uuid.uuid4().hex[:6]}"
        act_id = f"ACT-{uuid.uuid4().hex[:6]}"

        from datetime import datetime
        # 1. Valid chain on Journey A
        rule_eval = RuleEvaluationModel(
            evaluation_id=eval_id,
            decision_context_id=ctx_id,
            policy_id="POL-001",
            policy_version="V1",
            rule_id="RULE-SCH-001",
            rule_version="V1",
            result="CONDITION_MET",
            evaluation_timestamp=datetime.utcnow().isoformat(),
            input_values_json='{"primary_context": "Operational", "secondary_context": "Provider Schedule Gap"}',
            journey_id=journey_a
        )
        db.add(rule_eval)

        rec = RecommendationModel(
            recommendation_id=rec_id,
            decision_context_id=ctx_id,
            rule_evaluation_id=eval_id,
            journey_id=journey_a,
            mapping_id="REC-MAP-001",
            mapping_version="V1",
            content="Valid Rec",
            status="ACTIVE",
            priority="Moderate"
        )
        db.add(rec)

        # Adversarial swap: Decision is injected with journey_b!
        decision_cross = HumanDecisionModel(
            decision_id=dec_id,
            recommendation_id=rec_id,
            journey_id=journey_b,  # CROSS JOURNEY!
            actor_id="actor_bad",
            decision_type="APPROVED",
            status="RECORDED"
        )
        db.add(decision_cross)

        action = OperationalActionModel(
            action_id=act_id,
            authorization_reference=dec_id,
            journey_id=journey_a,
            action_type="PATIENT_COMMUNICATION",
            status="CREATED"
        )
        db.add(action)
        db.commit()

        # Lifecycle check
        lifecycle = _build_historical_lifecycle(rec, db)
        assert lifecycle["technical_state"] != "valid", (
            f"Expected non-valid state for cross-journey decision, got {lifecycle['technical_state']}"
        )
        assert "cross_journey_decision" in lifecycle.get("errors", [])

    finally:
        db.close()
