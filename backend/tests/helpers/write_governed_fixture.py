import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../src/runtime')))

from app.db.database import Base, engine  # noqa: E402
from app.services.governance_registry import (  # noqa: E402
    governance_registry, RecommendationRecord, RecommendationStatus,
    AuthorityRequirement, HumanDecisionRecord, DecisionType, DecisionStatus,
    RuleEvaluationRecord
)
from datetime import datetime  # noqa: E402
import json  # noqa: E402


def main():
    Base.metadata.create_all(bind=engine)

    import uuid
    uid = uuid.uuid4().hex[:6]
    jny_id = f"JNY-SUBPROC-{uid}"
    eval_id = f"EVAL-SUBPROC-{uid}"
    rec_id = f"REC-SUBPROC-{uid}"
    dec_id = f"DEC-SUBPROC-{uid}"

    governance_registry.record_evaluation(RuleEvaluationRecord(
        evaluation_id=eval_id,
        decision_context_id="CTX-SUBPROC",
        policy_id="POL-SUBPROC",
        policy_version="V1",
        rule_id="RULE-SUBPROC",
        rule_version="V1",
        result="CONDITION_MET",
        evaluation_timestamp=datetime.utcnow(),
        input_values={"test": "subproc"},
        journey_id=jny_id
    ))

    governance_registry.record_recommendation(RecommendationRecord(
        recommendation_id=rec_id,
        mapping_id="MAP-SUBPROC",
        mapping_version="V1",
        decision_context_id="CTX-SUBPROC",
        rule_evaluation_id=eval_id,
        recommendation_content="Subproc Rec",
        status=RecommendationStatus.ACTIVE,
        authority_requirement=AuthorityRequirement.APPROVAL_REQUIRED,
        priority="High",
        journey_id=jny_id
    ))

    governance_registry.record_human_decision(HumanDecisionRecord(
        decision_id=dec_id,
        recommendation_id=rec_id,
        actor_id="ACTOR-SUBPROC",
        decision_type=DecisionType.APPROVED,
        authority_basis="Valid",
        status=DecisionStatus.RECORDED,
        journey_id=jny_id
    ))

    print(json.dumps({"jny_id": jny_id, "rec_id": rec_id, "dec_id": dec_id}))


if __name__ == "__main__":
    main()
