import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../src/runtime')))

from app.db.database import SessionLocal, Base, engine  # noqa: E402
from app.services.governance_registry import (  # noqa: E402
    governance_registry, PolicyVersion, RuleVersion, LifecycleState,
    RecommendationMapping, AuthorityRequirement
)
from app.models.governance_storage import RecommendationModel, RuleEvaluationModel  # noqa: E402
import json  # noqa: E402
import uuid  # noqa: E402
from datetime import datetime  # noqa: E402


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    uid = uuid.uuid4().hex[:6]
    policy_id = f"POL-RECON-{uid}"
    rule_id = f"RULE-RECON-{uid}"
    journey_id = f"JNY-RECON-{uid}"
    eval_id = f"EVAL-RECON-{uid}"
    rec_id = f"REC-RECON-{uid}"
    mapping_id = f"MAP-RECON-{uid}"

    # 1. Register historical V1 logic
    policy_v1 = PolicyVersion(
        policy_id=policy_id, version="V1", content="V1 Policy", lifecycle_state=LifecycleState.ACTIVE)
    rule_v1 = RuleVersion(
        rule_id=rule_id, version="V1", logic_description="", lifecycle_state=LifecycleState.ACTIVE,
        inputs=[], allowed_outputs=[], governing_policy_id=policy_id, governing_policy_version="V1")
    mapping_v1 = RecommendationMapping(
        mapping_id=mapping_id, version="V1", applicable_rule_id=rule_id,
        eligible_result="CONDITION_MET", recommendation_template="Test Action V1",
        authority_requirement=AuthorityRequirement.APPROVAL_REQUIRED, priority="High",
        lifecycle_state=LifecycleState.ACTIVE, business_impact_template="V1 Impact",
        expected_outcome_template="V1 Expected")

    governance_registry.register_policy(policy_v1)
    governance_registry.register_rule(rule_v1)
    governance_registry.register_recommendation_mapping(mapping_v1)

    # Inject historical state explicitly bound to V1
    db.add(RuleEvaluationModel(
        evaluation_id=eval_id, decision_context_id="CTX-RECON", policy_id=policy_id, policy_version="V1",
        rule_id=rule_id, rule_version="V1", result="CONDITION_MET",
        evaluation_timestamp=datetime.utcnow().isoformat(),
        input_values_json=json.dumps({"primary_context": "Operational"}), journey_id=journey_id
    ))
    db.add(RecommendationModel(
        recommendation_id=rec_id, decision_context_id="CTX-RECON", rule_evaluation_id=eval_id,
        journey_id=journey_id, mapping_id=mapping_id, mapping_version="V1", content="Test Action V1",
        status="ACTIVE", priority="High", generated_at=datetime.utcnow().isoformat()
    ))
    db.commit()

    # 2. Register materially different V2 logic
    policy_v2 = PolicyVersion(
        policy_id=policy_id, version="V2", content="V2 Policy", lifecycle_state=LifecycleState.ACTIVE)
    rule_v2 = RuleVersion(
        rule_id=rule_id, version="V2", logic_description="", lifecycle_state=LifecycleState.ACTIVE,
        inputs=[], allowed_outputs=[], governing_policy_id=policy_id, governing_policy_version="V2")
    mapping_v2 = RecommendationMapping(
        mapping_id=mapping_id, version="V2", applicable_rule_id=rule_id,
        eligible_result="CONDITION_MET", recommendation_template="Materially Different V2 Action",
        authority_requirement=AuthorityRequirement.APPROVAL_REQUIRED, priority="Critical",
        lifecycle_state=LifecycleState.ACTIVE, business_impact_template="V2 Impact",
        expected_outcome_template="V2 Expected")

    governance_registry.register_policy(policy_v2)
    governance_registry.register_rule(rule_v2)
    governance_registry.register_recommendation_mapping(mapping_v2)

    print(json.dumps({"jny_id": journey_id, "rec_id": rec_id, "eval_id": eval_id}))


if __name__ == "__main__":
    main()
