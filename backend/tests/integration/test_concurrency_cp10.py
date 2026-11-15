import pytest
import concurrent.futures
import uuid
from app.services.governance_registry import (
    governance_registry,
    RecommendationRecord,
    RecommendationStatus,
    AuthorityRequirement,
    DecisionType
)
from app.services.human_decision_engine import human_decision_engine


@pytest.mark.integration
def test_cp10_concurrent_human_decisions_single_winner_and_idempotency():
    """
    CP-10 / Ticket 16:
    Concurrent Human Decision insertion test.
    Ensures that paired conflicting requests produce exactly one persisted decision ID
    and return conflict (or idempotent success for exact retry), never phantom success.
    """
    rec_id = f"REC-CONC-{uuid.uuid4().hex[:8].upper()}"
    journey_id = f"JOURNEY-CONC-{uuid.uuid4().hex[:8].upper()}"

    # 1. Register an active recommendation in governance storage
    rec = RecommendationRecord(
        recommendation_id=rec_id,
        mapping_id="REC-MAP-001",
        mapping_version="V1",
        decision_context_id="CTX-CONC-001",
        rule_evaluation_id="EVAL-CONC-001",
        recommendation_content="Review no-show follow-up according to clinic policy",
        status=RecommendationStatus.ACTIVE,
        authority_requirement=AuthorityRequirement.APPROVAL_REQUIRED,
        priority="Moderate",
        business_impact="Schedule gap",
        expected_outcome="Reschedule slot",
        problem="Patient No-Show",
        journey_id=journey_id,
        intended_target_reference="target-001"
    )
    governance_registry.record_recommendation(rec)

    # 2. Concurrently submit 10 conflicting decisions for the same recommendation
    results = []
    def make_decision(actor_idx, dec_type):
        return human_decision_engine.record_decision(
            actor_id=f"actor_{actor_idx}",
            actor_role="Clinic Manager",
            recommendation_id=rec_id,
            decision_type_str=dec_type,
            reason=f"Concurrent test actor_{actor_idx}"
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(make_decision, i, "APPROVED" if i % 2 == 0 else "REJECTED")
            for i in range(10)
        ]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    successes = [r for r in results if r.get("status") == "SUCCESS"]
    errors = [r for r in results if r.get("status") == "ERROR"]

    # Exactly ONE decision can win and be persisted as current RECORDED
    assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}"
    assert len(errors) == 9, f"Expected 9 conflicts/errors, got {len(errors)}"
    winning_decision_id = successes[0]["decision_id"]

    # 3. Idempotent Retry: Same winning actor resubmitting identical decision succeeds with SAME ID
    # Find winning actor and decision type
    from app.db.database import SessionLocal
    from app.models.governance_storage import HumanDecisionModel
    db = SessionLocal()
    persisted = db.query(HumanDecisionModel).filter(
        HumanDecisionModel.decision_id == winning_decision_id
    ).first()
    db.close()

    assert persisted is not None
    retry_res = human_decision_engine.record_decision(
        actor_id=persisted.actor_id,
        actor_role="Clinic Manager",
        recommendation_id=rec_id,
        decision_type_str=persisted.decision_type,
        reason="Idempotent retry"
    )
    assert retry_res.get("status") == "SUCCESS"
    assert retry_res.get("decision_id") == winning_decision_id
