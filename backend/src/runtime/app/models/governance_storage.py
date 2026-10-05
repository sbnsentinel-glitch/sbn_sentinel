from sqlalchemy import Column, String, Text, UniqueConstraint, Integer, Boolean
from app.db.database import Base


class GovernanceStorageModel(Base):
    """Fallback storage for GovernanceRegistry to migrate away from pickle."""
    __tablename__ = "governance_storage"
    __table_args__ = {'extend_existing': True}
    id = Column(String, primary_key=True)
    state_json = Column(Text, nullable=False)


class RuleEvaluationModel(Base):
    __tablename__ = "governed_rule_evaluations"
    __table_args__ = {'extend_existing': True}
    evaluation_id = Column(String, primary_key=True)
    decision_context_id = Column(String, nullable=False)
    policy_id = Column(String, nullable=False)
    policy_version = Column(String, nullable=False)
    rule_id = Column(String, nullable=False)
    rule_version = Column(String, nullable=False)
    result = Column(String, nullable=False)
    evaluation_timestamp = Column(String, nullable=False)
    input_values_json = Column(Text)
    journey_id = Column(String, nullable=False, index=True)


class RecommendationModel(Base):
    __tablename__ = "governed_recommendations"
    __table_args__ = {'extend_existing': True}
    recommendation_id = Column(String, primary_key=True)
    decision_context_id = Column(String, nullable=False)
    rule_evaluation_id = Column(String, nullable=False)
    journey_id = Column(String, nullable=False, index=True)
    mapping_id = Column(String, nullable=False)
    mapping_version = Column(String, nullable=False)
    content = Column(Text)
    status = Column(String)
    priority = Column(String)
    generated_at = Column(String)
    intended_target_reference = Column(String, nullable=True)
    authority_requirement = Column(String, nullable=True)


class HumanDecisionModel(Base):
    __tablename__ = "governed_decisions"
    __table_args__ = {'extend_existing': True}
    decision_id = Column(String, primary_key=True)
    recommendation_id = Column(String, nullable=False, index=True)
    journey_id = Column(String, nullable=False, index=True)
    actor_id = Column(String)
    decision_type = Column(String)
    status = Column(String)
    decision_timestamp = Column(String)
    authority_basis = Column(String, nullable=True)
    reason = Column(Text, nullable=True)
    override_indicator = Column(Boolean, default=False)


class OperationalActionModel(Base):
    __tablename__ = "governed_actions"
    __table_args__ = {'extend_existing': True}
    action_id = Column(String, primary_key=True)
    authorization_reference = Column(String, nullable=False)  # foreign key to decision
    journey_id = Column(String, nullable=False, index=True)
    action_type = Column(String)
    target_reference = Column(String)
    status = Column(String)
    current_result = Column(String)
    parameters_json = Column(Text)
    created_at = Column(String)
    execute_by = Column(String, nullable=True)  # D6.3: durable expiry timestamp
    intent_hash = Column(String, unique=True, nullable=True)

    __table_args__ = (
        UniqueConstraint('authorization_reference', 'action_type', 'target_reference', 'intent_hash', name='uq_action_intent'),
        {'extend_existing': True}
    )


class ExecutionAttemptModel(Base):
    __tablename__ = "governed_execution_attempts"
    __table_args__ = {'extend_existing': True}
    attempt_id = Column(String, primary_key=True)
    action_id = Column(String, nullable=False)
    journey_id = Column(String, nullable=False, index=True)
    result = Column(String)
    attempt_timestamp = Column(String)
    # D6.3: durable fields — nullable so legacy rows render Unknown/Unavailable
    attempt_number = Column(Integer, nullable=True)  # stored as integer to match migration
    connector = Column(String, nullable=True)
    request_reference = Column(String, nullable=True)
    response_reference = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)


class OperationalOutcomeModel(Base):
    __tablename__ = "governed_outcomes"
    __table_args__ = {'extend_existing': True}
    outcome_id = Column(String, primary_key=True)
    action_id = Column(String, nullable=False)
    journey_id = Column(String, nullable=False, index=True)
    confirmation_state = Column(String)
    resolution_state = Column(String)
    expected_outcome_json = Column(Text, nullable=True)
    observed_outcome_json = Column(Text, nullable=True)
    created_at = Column(String)
    # D6.3: durable outcome lifecycle fields — nullable for legacy row safety
    source_reference = Column(String, nullable=True)
    closure_reason = Column(Text, nullable=True)
    confirmed_at = Column(String, nullable=True)
    closed_at = Column(String, nullable=True)
    reopened_at = Column(String, nullable=True)


class GovernedPolicyVersionModel(Base):
    __tablename__ = "governed_policy_versions"
    __table_args__ = {'extend_existing': True}
    policy_id = Column(String, primary_key=True)
    version = Column(String, primary_key=True)
    lifecycle_state = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    effective_from = Column(String, nullable=True)
    effective_until = Column(String, nullable=True)
    approval_state = Column(String, nullable=True)
    approved_by = Column(String, nullable=True)
    approval_timestamp = Column(String, nullable=True)
    previous_version = Column(String, nullable=True)
    created_at = Column(String, nullable=True)
    scope = Column(String, default="Global")


class GovernedRuleVersionModel(Base):
    __tablename__ = "governed_rule_versions"
    __table_args__ = {'extend_existing': True}
    rule_id = Column(String, primary_key=True)
    version = Column(String, primary_key=True)
    governing_policy_id = Column(String, nullable=False)
    governing_policy_version = Column(String, nullable=False)
    lifecycle_state = Column(String, nullable=False)
    logic_description = Column(Text, nullable=True)
    inputs_json = Column(Text, nullable=True)
    allowed_outputs_json = Column(Text, nullable=True)
    effective_from = Column(String, nullable=True)
    effective_until = Column(String, nullable=True)
    approval_state = Column(String, nullable=True)
    approved_by = Column(String, nullable=True)
    approval_timestamp = Column(String, nullable=True)
    previous_version = Column(String, nullable=True)
    created_at = Column(String, nullable=True)


class GovernedRecommendationMappingModel(Base):
    __tablename__ = "governed_recommendation_mappings"
    __table_args__ = {'extend_existing': True}
    mapping_id = Column(String, primary_key=True)
    version = Column(String, primary_key=True)
    applicable_rule_id = Column(String, nullable=False)
    eligible_result = Column(String, nullable=False)
    recommendation_template = Column(Text, nullable=False)
    authority_requirement = Column(String, nullable=False)
    priority = Column(String, nullable=False)
    lifecycle_state = Column(String, nullable=False)
    business_impact_template = Column(Text, nullable=True)
    expected_outcome_template = Column(Text, nullable=True)
    problem_template = Column(Text, nullable=True)
    effective_from = Column(String, nullable=True)
    created_at = Column(String, nullable=True)
    allowed_action_types_json = Column(Text, nullable=True)
