import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../src/runtime')))

from app.services.reconstruction_engine import reconstruction_engine  # noqa: E402
from app.db.database import SessionLocal  # noqa: E402
from app.models.governance_storage import RecommendationModel, RuleEvaluationModel  # noqa: E402


def main():
    if len(sys.argv) < 4:
        print("Usage: reconstruction_v2_read.py <jny_id> <rec_id> <eval_id>")
        sys.exit(1)

    _ = sys.argv[1]
    rec_id = sys.argv[2]
    eval_id = sys.argv[3]

    db = SessionLocal()
    eval_record = db.query(RuleEvaluationModel).filter(RuleEvaluationModel.evaluation_id == eval_id).first()
    if eval_record:
        from app.services.rules_engine import rules_engine
        rules_engine.register_handler(eval_record.rule_id, lambda d: "CONDITION_MET")

    # Invoke Reconstruction Engine in fresh process: must strictly resolve and match V1
    result = reconstruction_engine.reproduce_decision(rec_id)

    # Clean up right after check
    db = SessionLocal()
    db.query(RecommendationModel).filter(RecommendationModel.recommendation_id == rec_id).delete()
    db.query(RuleEvaluationModel).filter(RuleEvaluationModel.evaluation_id == eval_id).delete()
    db.commit()
    db.close()

    if result.status != "MATCH":
        print(f"Reconstruction failed: {result.differences}")
        sys.exit(1)

    if result.reproduced.get("action") != "Test Action V1":
        print(f"Reconstruction yielded wrong action: {result.reproduced.get('action')}")
        sys.exit(1)

    print("SUCCESS")


if __name__ == "__main__":
    main()
