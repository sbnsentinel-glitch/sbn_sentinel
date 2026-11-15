import pytest
import uuid
from app.services.reconstruction_engine import reconstruction_engine
from app.services.governance_registry import (
    governance_registry,
    RecommendationRecord,
    RecommendationStatus,
    AuthorityRequirement
)
from app.db.database import SessionLocal
from app.models.governance_storage import RuleEvaluationModel


@pytest.mark.integration
def test_cp12_reconstruction_canonical_contract_and_missing_inputs():
    """
    CP-12 / Ticket 04:
    Reconstruction compares canonical fields (including authority, impact, outcome)
    and returns NOT_REPRODUCIBLE if render inputs are missing, or MISMATCH if authority mutated.
    """
    rec_id = f"REC-RECON-{uuid.uuid4().hex[:6]}"
    eval_id = f"EVAL-RECON-{uuid.uuid4().hex[:6]}"
    ctx_id = f"CTX-RECON-{uuid.uuid4().hex[:6]}"
    journey_id = f"JOURNEY-RECON-{uuid.uuid4().hex[:6]}"

    from datetime import datetime
    # Save rule evaluation in DB
    db = SessionLocal()
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
        journey_id=journey_id
    )
    db.add(rule_eval)
    db.commit()
    db.close()

    # Case 1: Exact match
    rec = RecommendationRecord(
        recommendation_id=rec_id,
        mapping_id="REC-MAP-001",
        mapping_version="V1",
        decision_context_id=ctx_id,
        rule_evaluation_id=eval_id,
        recommendation_content="Review no-show follow-up according to clinic policy and evaluate reschedule options.",
        status=RecommendationStatus.ACTIVE,
        authority_requirement=AuthorityRequirement.APPROVAL_REQUIRED,
        priority="Moderate",
        business_impact="Unfilled appointment slot in provider schedule.",
        expected_outcome="Patient contacted for reschedule and schedule slot reclaimed.",
        problem="Patient No-Show",
        journey_id=journey_id,
        intended_target_reference="target-1"
    )
    governance_registry.record_recommendation(rec)

    res = reconstruction_engine.reproduce_decision(rec_id)
    assert res.status == "MATCH", f"Expected MATCH, got {res.status} (diffs={res.differences})"

    # Case 2: Mutate authority on historical record -> must MISMATCH!
    rec_mutated_id = f"REC-MUT-{uuid.uuid4().hex[:6]}"
    rec_mutated = RecommendationRecord(
        recommendation_id=rec_mutated_id,
        mapping_id="REC-MAP-001",
        mapping_version="V1",
        decision_context_id=ctx_id,
        rule_evaluation_id=eval_id,
        recommendation_content="Review no-show follow-up according to clinic policy and evaluate reschedule options.",
        status=RecommendationStatus.ACTIVE,
        authority_requirement=AuthorityRequirement.INFORMATIONAL,  # Mutated from APPROVAL_REQUIRED!
        priority="Moderate",
        business_impact="Unfilled appointment slot in provider schedule.",
        expected_outcome="Patient contacted for reschedule and schedule slot reclaimed.",
        problem="Patient No-Show",
        journey_id=journey_id,
        intended_target_reference="target-1"
    )
    governance_registry.record_recommendation(rec_mutated)

    res_mut = reconstruction_engine.reproduce_decision(rec_mutated_id)
    assert res_mut.status == "MISMATCH", f"Expected MISMATCH on authority change, got {res_mut.status}"
    diff_fields = [d["field"] for d in res_mut.differences]
    assert "authority" in diff_fields
