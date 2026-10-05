from dataclasses import dataclass, field
from datetime import datetime, timedelta
from dateutil.parser import parse as dateutil_parse
from enum import Enum
from typing import Optional, List, Dict, Any
from uuid import UUID  # noqa
from app.db.database import SessionLocal
from app.models.governance_storage import (
    RecommendationModel, HumanDecisionModel,
    OperationalActionModel, ExecutionAttemptModel, OperationalOutcomeModel,
    RuleEvaluationModel
)
import logging

logger = logging.getLogger(__name__)

# ======================================================
# SESR-003: Core Governance Objects
# ======================================================


class LifecycleState(Enum):
    DRAFT = "DRAFT"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    RETIRED = "RETIRED"


@dataclass(frozen=True)
class PolicyVersion:
    """PRO-002: Policy Version Record"""
    policy_id: str
    version: str
    content: str
    lifecycle_state: LifecycleState
    effective_from: Optional[datetime] = None
    effective_until: Optional[datetime] = None
    approval_state: str = "PENDING"
    approved_by: Optional[str] = None
    approval_timestamp: Optional[datetime] = None
    previous_version: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    scope: str = "Global"

    def is_applicable(self, eval_time: datetime) -> bool:
        if self.lifecycle_state != LifecycleState.ACTIVE:
            return False
        if self.effective_from and eval_time < self.effective_from:
            return False
        if self.effective_until and eval_time > self.effective_until:
            return False
        return True


@dataclass(frozen=True)
class RuleInputDefinition:
    """PRO-006: Rule Input Definition"""
    input_name: str
    input_type: str
    required: bool
    expected_source: str


@dataclass(frozen=True)
class RuleVersion:
    """PRO-004: Rule Version Record"""
    rule_id: str
    version: str
    logic_description: str
    lifecycle_state: LifecycleState
    inputs: List[RuleInputDefinition]
    allowed_outputs: List[str]
    governing_policy_id: str
    governing_policy_version: str
    effective_from: Optional[datetime] = None
    effective_until: Optional[datetime] = None
    approval_state: str = "PENDING"
    approved_by: Optional[str] = None
    approval_timestamp: Optional[datetime] = None
    previous_version: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)

    def is_applicable(self, eval_time: datetime) -> bool:
        if self.lifecycle_state != LifecycleState.ACTIVE:
            return False
        if self.effective_from and eval_time < self.effective_from:
            return False
        if self.effective_until and eval_time > self.effective_until:
            return False
        return True


@dataclass(frozen=True)
class RuleEvaluationRecord:
    """PRO-014: Rule Evaluation Record"""
    evaluation_id: str
    decision_context_id: str
    policy_id: str
    policy_version: str
    rule_id: str
    rule_version: str
    result: str
    evaluation_timestamp: datetime = field(default_factory=datetime.utcnow)
    input_values: Dict[str, Any] = field(default_factory=dict)
    # SESR-008: Journey identity — the correlation_id of the originating operational event
    journey_id: Optional[str] = None


# ======================================================
# SESR-004: Recommendation Governance Objects
# ======================================================

class RecommendationStatus(Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    SUPERSEDED = "SUPERSEDED"


class AuthorityRequirement(Enum):
    INFORMATIONAL = "INFORMATIONAL"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"


@dataclass(frozen=True)
class RecommendationMapping:
    """RCO-002: Recommendation Mapping Record"""
    mapping_id: str
    version: str
    applicable_rule_id: str
    eligible_result: str
    recommendation_template: str
    authority_requirement: AuthorityRequirement
    priority: str
    lifecycle_state: LifecycleState
    business_impact_template: str = ""
    expected_outcome_template: str = ""
    problem_template: str = ""
    effective_from: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    allowed_action_types: List[str] = field(default_factory=list)

    def is_applicable(self, eval_time: datetime) -> bool:
        if self.lifecycle_state != LifecycleState.ACTIVE:
            return False
        if self.effective_from and eval_time < self.effective_from:
            return False
        return True


@dataclass(frozen=True)
class RecommendationRecord:
    """RCO-001: Recommendation Record"""
    recommendation_id: str
    mapping_id: str
    mapping_version: str
    decision_context_id: str
    rule_evaluation_id: str
    recommendation_content: str
    status: RecommendationStatus
    authority_requirement: AuthorityRequirement
    priority: str
    business_impact: str = ""
    expected_outcome: str = ""
    problem: str = ""
    generated_at: datetime = field(default_factory=datetime.utcnow)
    # SESR-008: Journey identity
    journey_id: Optional[str] = None
    intended_target_reference: Optional[str] = None


# ======================================================
# SESR-005: Human Decision Governance Objects
# ======================================================

class DecisionType(Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    OVERRIDDEN = "OVERRIDDEN"
    RETURNED_FOR_REVIEW = "RETURNED_FOR_REVIEW"


class DecisionStatus(Enum):
    RECORDED = "RECORDED"
    SUPERSEDED = "SUPERSEDED"
    INVALIDATED = "INVALIDATED"


@dataclass(frozen=True)
class AuthorityConfiguration:
    """ADG-006: Deterministic Authority Configuration"""
    role: str
    allowed_decisions: List[DecisionType]
    can_override: bool = False
    requires_reason_for: List[DecisionType] = field(
        default_factory=lambda: [
            DecisionType.REJECTED,
            DecisionType.OVERRIDDEN])


@dataclass(frozen=True)
class HumanDecisionRecord:
    """HDO-001: Human Decision Record"""
    decision_id: str
    recommendation_id: str
    actor_id: str
    decision_type: DecisionType
    authority_basis: str
    status: DecisionStatus
    reason: Optional[str] = None
    override_indicator: bool = False
    decision_timestamp: datetime = field(default_factory=datetime.utcnow)
    # SESR-008: Journey identity
    journey_id: Optional[str] = None


# ======================================================
# SESR-006: Operational Action Governance Objects
# ======================================================

class ActionType(Enum):
    RESCHEDULE_APPOINTMENT = "RESCHEDULE_APPOINTMENT"
    SEND_NOTIFICATION = "SEND_NOTIFICATION"
    UPDATE_OPERATIONAL_STATUS = "UPDATE_OPERATIONAL_STATUS"
    CREATE_FOLLOWUP_TASK = "CREATE_FOLLOWUP_TASK"


class ActionStatus(Enum):
    CREATED = "CREATED"
    READY = "READY"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    BLOCKED = "BLOCKED"


class ExecutionResult(Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"


@dataclass(frozen=True)
class OperationalActionRecord:
    """AEX-001: Operational Action Record"""
    action_id: str
    action_type: ActionType
    target_reference: str
    authorization_reference: str
    parameters: Dict[str, Any]
    status: ActionStatus = ActionStatus.READY
    current_result: ExecutionResult = ExecutionResult.NOT_ATTEMPTED
    execute_by: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    # SESR-008: Journey identity
    journey_id: Optional[str] = None
    intent_hash: Optional[str] = None


@dataclass(frozen=True)
class ExecutionAttemptRecord:
    """AEX-014: Execution Attempt Record"""
    attempt_id: str
    action_id: str
    attempt_number: int
    connector: str
    result: ExecutionResult
    request_reference: Optional[str] = None
    response_reference: Optional[str] = None
    error_message: Optional[str] = None
    attempt_timestamp: datetime = field(default_factory=datetime.utcnow)
    # SESR-008: Journey identity
    journey_id: Optional[str] = None

# ======================================================
# SESR-007: Operational Outcome Governance Objects
# ======================================================


class OutcomeConfirmationState(Enum):
    CONFIRMED = "CONFIRMED"
    MISMATCH = "MISMATCH"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"


class OutcomeResolutionState(Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    FOLLOW_UP_REQUIRED = "FOLLOW_UP_REQUIRED"


@dataclass(frozen=True)
class OperationalOutcomeRecord:
    """OOC-001: Operational Outcome Record"""
    outcome_id: str
    action_id: str
    expected_outcome: Any
    observed_outcome: Any
    confirmation_state: OutcomeConfirmationState = OutcomeConfirmationState.UNKNOWN
    resolution_state: OutcomeResolutionState = OutcomeResolutionState.OPEN
    closure_reason: Optional[str] = None
    source_reference: Optional[str] = None
    confirmed_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    reopened_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    # SESR-008: Journey identity
    journey_id: Optional[str] = None

# ======================================================
# SESR-008: Continuity Violation Error
# ======================================================


class ContinuityViolationError(Exception):
    """
    SESR-008 CFR-001 / CVC-031: Raised when a governed record cannot be associated
    with its required upstream parent, or when journey_id values do not match.
    This error must never be silenced — it signals a broken operational chain.
    """


# ======================================================
# IN-MEMORY REGISTRIES
# ======================================================

class GovernanceRegistry:
    """
    In-memory registry to demonstrate SESR-003, SESR-004, and SESR-005 compliance.
    Acts as the Single Source of Truth for engine processing.
    """

    def __init__(self):
        self._policies: List[PolicyVersion] = []
        self._rules: List[RuleVersion] = []
        self._evaluations: List[RuleEvaluationRecord] = []
        self._recommendation_mappings: List[RecommendationMapping] = []
        self._recommendations: List[RecommendationRecord] = []
        self._human_decisions: List[HumanDecisionRecord] = []
        self._authority_configs: Dict[str, AuthorityConfiguration] = {}
        self._operational_actions: List[OperationalActionRecord] = []
        self._execution_attempts: List[ExecutionAttemptRecord] = []
        self._operational_outcomes: List[OperationalOutcomeRecord] = []

    def _load(self) -> Optional[dict]:
        return None

    def _save(self):
        pass

    def register_policy(self, policy: PolicyVersion):
        from app.db.database import SessionLocal
        from app.models.governance_storage import GovernedPolicyVersionModel
        db = SessionLocal()
        try:
            row = db.query(GovernedPolicyVersionModel).filter_by(
                policy_id=policy.policy_id, version=policy.version
            ).first()
            if not row:
                eff_from = policy.effective_from.isoformat() if policy.effective_from else None
                eff_until = policy.effective_until.isoformat() if policy.effective_until else None
                app_ts = policy.approval_timestamp.isoformat() if policy.approval_timestamp else None
                cr_at = policy.created_at.isoformat() if policy.created_at else None
                ls = policy.lifecycle_state.value if hasattr(policy.lifecycle_state, "value") else str(policy.lifecycle_state)
                db.add(GovernedPolicyVersionModel(
                    policy_id=policy.policy_id,
                    version=policy.version,
                    lifecycle_state=ls,
                    content=policy.content,
                    effective_from=eff_from,
                    effective_until=eff_until,
                    approval_state=policy.approval_state,
                    approved_by=policy.approved_by,
                    approval_timestamp=app_ts,
                    previous_version=policy.previous_version,
                    created_at=cr_at,
                    scope=policy.scope
                ))
                db.commit()
            if not any(p.policy_id == policy.policy_id and p.version == policy.version for p in self._policies):
                self._policies.append(policy)
            self._save()
        except Exception as e:
            db.rollback()
            logger.error(f"Error persisting policy version: {e}")
            raise
        finally:
            db.close()

    def register_rule(self, rule: RuleVersion):
        import json
        from app.db.database import SessionLocal
        from app.models.governance_storage import GovernedRuleVersionModel
        db = SessionLocal()
        try:
            row = db.query(GovernedRuleVersionModel).filter_by(
                rule_id=rule.rule_id, version=rule.version
            ).first()
            if not row:
                inputs_data = [
                    {
                        "input_name": i.input_name,
                        "input_type": i.input_type,
                        "required": i.required,
                        "expected_source": i.expected_source
                    } for i in (rule.inputs or [])
                ]
                eff_from = rule.effective_from.isoformat() if rule.effective_from else None
                eff_until = rule.effective_until.isoformat() if rule.effective_until else None
                app_ts = rule.approval_timestamp.isoformat() if rule.approval_timestamp else None
                cr_at = rule.created_at.isoformat() if rule.created_at else None
                ls = rule.lifecycle_state.value if hasattr(rule.lifecycle_state, "value") else str(rule.lifecycle_state)
                db.add(GovernedRuleVersionModel(
                    rule_id=rule.rule_id,
                    version=rule.version,
                    governing_policy_id=rule.governing_policy_id,
                    governing_policy_version=rule.governing_policy_version,
                    lifecycle_state=ls,
                    logic_description=rule.logic_description,
                    inputs_json=json.dumps(inputs_data),
                    allowed_outputs_json=json.dumps(rule.allowed_outputs or []),
                    effective_from=eff_from,
                    effective_until=eff_until,
                    approval_state=rule.approval_state,
                    approved_by=rule.approved_by,
                    approval_timestamp=app_ts,
                    previous_version=rule.previous_version,
                    created_at=cr_at
                ))
                db.commit()
            if not any(r.rule_id == rule.rule_id and r.version == rule.version for r in self._rules):
                self._rules.append(rule)
            self._save()
        except Exception as e:
            db.rollback()
            logger.error(f"Error persisting rule version: {e}")
            raise
        finally:
            db.close()

    def record_evaluation(self, record: RuleEvaluationRecord):
        db = SessionLocal()
        try:
            import json
            db.add(RuleEvaluationModel(
                evaluation_id=record.evaluation_id,
                decision_context_id=record.decision_context_id,
                policy_id=record.policy_id,
                policy_version=record.policy_version,
                rule_id=record.rule_id,
                rule_version=record.rule_version,
                result=record.result,
                evaluation_timestamp=record.evaluation_timestamp.isoformat(),
                input_values_json=json.dumps(record.input_values),
                journey_id=record.journey_id
            ))
            db.commit()
            self._evaluations.append(record)
        except Exception as e:
            db.rollback()
            logger.error(f"DB Error saving rule evaluation: {e}")
            raise
        finally:
            db.close()

        logger.debug(
            f"[GovernanceRegistry] Recorded evaluation {record.evaluation_id} -> {record.result}")

    def get_rule_evaluation(self, evaluation_id: str) -> Optional[RuleEvaluationRecord]:
        from app.db.database import SessionLocal
        from app.models.governance_storage import RuleEvaluationModel
        import json
        from dateutil.parser import parse
        db = SessionLocal()
        try:
            row = db.query(RuleEvaluationModel).filter(
                RuleEvaluationModel.evaluation_id == evaluation_id).first()
            if row:
                ts = parse(row.evaluation_timestamp) if isinstance(
                    row.evaluation_timestamp, str) else row.evaluation_timestamp
                inputs = json.loads(row.input_values_json) if row.input_values_json else {}
                return RuleEvaluationRecord(
                    evaluation_id=row.evaluation_id,
                    decision_context_id=row.decision_context_id,
                    policy_id=row.policy_id,
                    policy_version=row.policy_version,
                    rule_id=row.rule_id,
                    rule_version=row.rule_version,
                    result=row.result,
                    evaluation_timestamp=ts,
                    input_values=inputs,
                    journey_id=row.journey_id
                )
            for e in self._evaluations:
                if e.evaluation_id == evaluation_id:
                    return e
            return None
        finally:
            db.close()

    def register_recommendation_mapping(self, mapping: RecommendationMapping):
        import json
        from app.db.database import SessionLocal
        from app.models.governance_storage import GovernedRecommendationMappingModel
        db = SessionLocal()
        try:
            row = db.query(GovernedRecommendationMappingModel).filter_by(
                mapping_id=mapping.mapping_id, version=mapping.version
            ).first()
            if not row:
                eff_from = mapping.effective_from.isoformat() if mapping.effective_from else None
                cr_at = mapping.created_at.isoformat() if mapping.created_at else None
                ar = mapping.authority_requirement.value if hasattr(mapping.authority_requirement, "value") else str(mapping.authority_requirement)
                ls = mapping.lifecycle_state.value if hasattr(mapping.lifecycle_state, "value") else str(mapping.lifecycle_state)
                db.add(GovernedRecommendationMappingModel(
                    mapping_id=mapping.mapping_id,
                    version=mapping.version,
                    applicable_rule_id=mapping.applicable_rule_id,
                    eligible_result=mapping.eligible_result,
                    recommendation_template=mapping.recommendation_template,
                    authority_requirement=ar,
                    priority=mapping.priority,
                    lifecycle_state=ls,
                    business_impact_template=mapping.business_impact_template,
                    expected_outcome_template=mapping.expected_outcome_template,
                    problem_template=mapping.problem_template,
                    effective_from=eff_from,
                    created_at=cr_at,
                    allowed_action_types_json=json.dumps(mapping.allowed_action_types)
                ))
                db.commit()
            if not any(m.mapping_id == mapping.mapping_id and m.version == mapping.version for m in self._recommendation_mappings):
                self._recommendation_mappings.append(mapping)
            self._save()
        except Exception as e:
            db.rollback()
            logger.error(f"Error persisting recommendation mapping: {e}")
            raise
        finally:
            db.close()

    def record_recommendation(self, record: RecommendationRecord):
        db = SessionLocal()
        try:
            db.add(RecommendationModel(
                recommendation_id=record.recommendation_id,
                decision_context_id=record.decision_context_id,
                rule_evaluation_id=record.rule_evaluation_id,
                journey_id=record.journey_id,  # Issue #3: Removed "UNKNOWN" fallback
                mapping_id=record.mapping_id,
                mapping_version=record.mapping_version,
                content=record.recommendation_content,
                status=record.status.value,
                priority=record.priority,
                generated_at=record.generated_at.isoformat(),
                intended_target_reference=record.intended_target_reference,
                authority_requirement=record.authority_requirement.value
            ))
            db.commit()
            self._recommendations.append(record)
        except Exception as e:
            db.rollback()
            logger.error(f"DB Error saving recommendation: {e}")
            raise  # Issue #3: Abort, do not swallow error
        finally:
            db.close()

        logger.debug(f"[GovernanceRegistry] Recorded recommendation {record.recommendation_id}")

    def get_recommendation(self, recommendation_id: str) -> Optional[RecommendationRecord]:
        from app.db.database import SessionLocal
        from app.models.governance_storage import RecommendationModel
        from dateutil.parser import parse
        db = SessionLocal()
        try:
            db_record = db.query(RecommendationModel).filter(RecommendationModel.recommendation_id == recommendation_id).first()
            if db_record:
                return RecommendationRecord(
                    recommendation_id=db_record.recommendation_id,
                    mapping_id=db_record.mapping_id,
                    mapping_version=db_record.mapping_version,
                    decision_context_id=db_record.decision_context_id,
                    rule_evaluation_id=db_record.rule_evaluation_id,
                    recommendation_content=db_record.content,
                    status=RecommendationStatus(db_record.status),
                    authority_requirement=AuthorityRequirement(db_record.authority_requirement) if getattr(db_record, "authority_requirement", None) else AuthorityRequirement.INFORMATIONAL,
                    priority=db_record.priority,
                    generated_at=parse(db_record.generated_at),
                    journey_id=db_record.journey_id,
                    intended_target_reference=db_record.intended_target_reference
                )
            for r in self._recommendations:
                if r.recommendation_id == recommendation_id:
                    return r
            return None
        finally:
            db.close()

    def register_authority_config(self, config: AuthorityConfiguration):
        self._authority_configs[config.role] = config
        self._save()

    def get_authority_config(self, role: str) -> Optional[AuthorityConfiguration]:
        return self._authority_configs.get(role)

    def record_human_decision(self, decision: HumanDecisionRecord):
        db = SessionLocal()
        try:
            existing = db.query(HumanDecisionModel).filter(
                HumanDecisionModel.recommendation_id == decision.recommendation_id,
                HumanDecisionModel.status == "RECORDED"
            ).with_for_update().first()

            if existing:
                if existing.actor_id != decision.actor_id or existing.decision_type != decision.decision_type.value:
                    raise Exception("A current decision already exists for this recommendation.")
                # Idempotent - do nothing
            else:
                db.add(HumanDecisionModel(
                    decision_id=decision.decision_id,
                    recommendation_id=decision.recommendation_id,
                    journey_id=decision.journey_id,
                    actor_id=decision.actor_id,
                    decision_type=decision.decision_type.value,
                    status=decision.status.value,
                    decision_timestamp=decision.decision_timestamp.isoformat(),
                    authority_basis=decision.authority_basis,
                    reason=decision.reason,
                    override_indicator=decision.override_indicator
                ))
                db.commit()
            self._human_decisions.append(decision)
        except Exception as e:
            db.rollback()
            logger.error(f"DB Error saving decision: {e}")
            raise  # Issue #3: Abort
        finally:
            db.close()

        logger.debug(
            f"[GovernanceRegistry] Recorded human decision {decision.decision_id} by {decision.actor_id}")

    def get_human_decision(self, decision_id: str) -> Optional[HumanDecisionRecord]:
        from app.db.database import SessionLocal
        from app.models.governance_storage import HumanDecisionModel
        db = SessionLocal()
        try:
            db_record = db.query(HumanDecisionModel).filter(HumanDecisionModel.decision_id == decision_id).first()
            if db_record:
                return HumanDecisionRecord(
                    decision_id=db_record.decision_id,
                    recommendation_id=db_record.recommendation_id,
                    actor_id=db_record.actor_id,
                    decision_type=DecisionType(db_record.decision_type),
                    authority_basis=db_record.authority_basis or "Unknown",
                    reason=db_record.reason,
                    override_indicator=db_record.override_indicator,
                    status=DecisionStatus(db_record.status),
                    journey_id=db_record.journey_id
                )
            # Fallback to in-memory for testing if needed
            for d in self._human_decisions:
                if d.decision_id == decision_id:
                    return d
            return None
        finally:
            db.close()

    def record_operational_action(self, action: OperationalActionRecord):
        db = SessionLocal()
        try:
            import json
            db.add(OperationalActionModel(
                action_id=action.action_id,
                authorization_reference=action.authorization_reference,
                journey_id=action.journey_id,  # Issue #3: Removed "UNKNOWN" fallback
                action_type=action.action_type.value,
                target_reference=action.target_reference,
                status=action.status.value,
                current_result=action.current_result.value,
                parameters_json=json.dumps(action.parameters),
                created_at=action.created_at.isoformat(),
                execute_by=action.execute_by.isoformat() if action.execute_by else None,
                intent_hash=action.intent_hash
            ))
            db.commit()
            self._operational_actions.append(action)
        except Exception as e:
            db.rollback()
            logger.error(f"DB Error saving action: {e}")
            raise  # Issue #3: Abort
        finally:
            db.close()

        logger.debug(f"[GovernanceRegistry] Recorded operational action {action.action_id}")

    def get_operational_action(self, action_id: str) -> Optional[OperationalActionRecord]:
        from app.db.database import SessionLocal
        from app.models.governance_storage import OperationalActionModel
        db = SessionLocal()
        try:
            db_record = db.query(OperationalActionModel).filter(OperationalActionModel.action_id == action_id).first()
            if db_record:
                import json

                return OperationalActionRecord(
                    action_id=db_record.action_id,
                    action_type=ActionType(db_record.action_type),
                    target_reference=db_record.target_reference,
                    authorization_reference=db_record.authorization_reference,
                    parameters=json.loads(db_record.parameters_json) if getattr(db_record, "parameters_json", None) else {},
                    status=ActionStatus(db_record.status),
                    current_result=ExecutionResult(db_record.current_result) if getattr(db_record, "current_result", None) else ExecutionResult.NOT_ATTEMPTED,
                    created_at=dateutil_parse(db_record.created_at) if getattr(db_record, "created_at", None) else None,
                    execute_by=dateutil_parse(db_record.execute_by) if getattr(db_record, "execute_by", None) else None,
                    journey_id=db_record.journey_id,
                    intent_hash=getattr(db_record, "intent_hash", None)
                )
            for a in self._operational_actions:
                if a.action_id == action_id:
                    return a
            return None
        finally:
            db.close()

    def update_operational_action(
            self,
            action_id: str,
            new_status: ActionStatus,
            new_result: ExecutionResult,
            receipt: Optional[Dict[str, Any]] = None):

        # Update DB Model
        from app.db.database import SessionLocal
        from app.models.governance_storage import OperationalActionModel
        db = SessionLocal()
        try:
            db_record = db.query(OperationalActionModel).filter(OperationalActionModel.action_id == action_id).with_for_update().first()
            if db_record:
                if new_status in (ActionStatus.COMPLETED, ActionStatus.FAILED):
                    if not receipt:
                        raise Exception("Missing execution receipt for terminal state transition.")
                    if not receipt.get("connector"):
                        raise Exception("Missing connector identity in receipt.")
                    if db_record.intent_hash and receipt.get("intent_hash") != db_record.intent_hash:
                        raise Exception("Execution receipt intent hash mismatch.")

                db_record.status = new_status.value
                db_record.current_result = new_result.value
                db.commit()

            # Also update in-memory cache for older tests
            for i, a in enumerate(self._operational_actions):
                if a.action_id == action_id:
                    import dataclasses
                    updated = dataclasses.replace(a, status=new_status, current_result=new_result)
                    self._operational_actions[i] = updated
                    return updated

            if db_record:
                return self.get_operational_action(action_id)
            return None
        finally:
            db.close()

    def claim_operational_action(self, action_id: str) -> bool:
        from app.db.database import SessionLocal
        from app.models.governance_storage import OperationalActionModel
        db = SessionLocal()
        try:
            db_record = db.query(OperationalActionModel).filter(OperationalActionModel.action_id == action_id).with_for_update().first()
            if db_record and db_record.status in ("CREATED", "READY"):
                db_record.status = "EXECUTING"
                db.commit()
                # Update memory
                for i, a in enumerate(self._operational_actions):
                    if a.action_id == action_id:
                        import dataclasses
                        updated = dataclasses.replace(a, status=ActionStatus.EXECUTING)
                        self._operational_actions[i] = updated
                return True
            return False
        finally:
            db.close()

    def record_execution_attempt(self, attempt: ExecutionAttemptRecord):
        db = SessionLocal()
        try:
            db.add(ExecutionAttemptModel(
                attempt_id=attempt.attempt_id,
                action_id=attempt.action_id,
                journey_id=attempt.journey_id,  # Issue #3: Removed "UNKNOWN" fallback
                result=attempt.result.value,
                attempt_timestamp=attempt.attempt_timestamp.isoformat(),
                # D6.3: persist durable fields so attempt_number/connector survive restart
                attempt_number=attempt.attempt_number,
                connector=attempt.connector,
                request_reference=attempt.request_reference,
                response_reference=attempt.response_reference,
                error_message=attempt.error_message,
            ))
            db.commit()
            self._execution_attempts.append(attempt)
        except Exception as e:
            db.rollback()
            logger.error(f"DB Error saving attempt: {e}")
            raise  # Issue #3: Abort
        finally:
            db.close()

        logger.debug(
            f"[GovernanceRegistry] Recorded execution attempt {attempt.attempt_id} for action {attempt.action_id}")

    def get_execution_attempts(self, action_id: str) -> List[ExecutionAttemptRecord]:
        from app.db.database import SessionLocal
        from app.models.governance_storage import ExecutionAttemptModel
        db = SessionLocal()
        try:
            db_records = db.query(ExecutionAttemptModel).filter(
                ExecutionAttemptModel.action_id == action_id
            ).order_by(ExecutionAttemptModel.attempt_timestamp).all()
            if db_records:
                # D6.3: Restore exact durable fields; legacy rows with NULL attempt_number
                # fall back to positional order; never fabricate connector labels
                result_list = []
                for idx, r in enumerate(db_records):
                    raw_num = getattr(r, "attempt_number", None)
                    try:
                        num = int(raw_num) if raw_num is not None else (idx + 1)
                    except (ValueError, TypeError):
                        num = idx + 1
                    connector_val = getattr(r, "connector", None) or "UNKNOWN"
                    result_list.append(ExecutionAttemptRecord(
                        attempt_id=r.attempt_id,
                        action_id=r.action_id,
                        attempt_number=num,
                        connector=connector_val,
                        result=ExecutionResult(r.result),
                        request_reference=getattr(r, "request_reference", None),
                        response_reference=getattr(r, "response_reference", None),
                        error_message=getattr(r, "error_message", None),
                        journey_id=r.journey_id
                    ))
                return result_list
            return [a for a in self._execution_attempts if a.action_id == action_id]
        finally:
            db.close()

    def record_operational_outcome(self, outcome: OperationalOutcomeRecord):
        db = SessionLocal()
        try:
            import json
            expected_json = json.dumps(
                outcome.expected_outcome) if outcome.expected_outcome is not None else None
            observed_json = json.dumps(
                outcome.observed_outcome) if outcome.observed_outcome is not None else None
            db.add(OperationalOutcomeModel(
                outcome_id=outcome.outcome_id,
                action_id=outcome.action_id,
                journey_id=outcome.journey_id,  # Issue #3: Removed "UNKNOWN" fallback
                confirmation_state=outcome.confirmation_state.value,
                resolution_state=outcome.resolution_state.value,
                expected_outcome_json=expected_json,
                observed_outcome_json=observed_json,
                created_at=outcome.created_at.isoformat(),
                # D6 Correction: Persist new outcome fields
                source_reference=outcome.source_reference,
                closure_reason=outcome.closure_reason,
                confirmed_at=outcome.confirmed_at.isoformat() if outcome.confirmed_at else None,
                closed_at=outcome.closed_at.isoformat() if outcome.closed_at else None,
                reopened_at=outcome.reopened_at.isoformat() if outcome.reopened_at else None
            ))
            db.commit()
            self._operational_outcomes.append(outcome)
        except Exception as e:
            db.rollback()
            logger.error(f"DB Error saving outcome: {e}")
            raise  # Issue #3: Abort
        finally:
            db.close()

        logger.debug(f"[GovernanceRegistry] Recorded operational outcome {outcome.outcome_id}")

    def get_operational_outcome(self, outcome_id: str) -> Optional[OperationalOutcomeRecord]:
        from app.db.database import SessionLocal
        from app.models.governance_storage import OperationalOutcomeModel
        import json
        db = SessionLocal()
        try:
            db_record = db.query(OperationalOutcomeModel).filter(
                OperationalOutcomeModel.outcome_id == outcome_id).first()
            if db_record:
                expected = json.loads(
                    db_record.expected_outcome_json) if getattr(
                    db_record, "expected_outcome_json", None) else None
                observed = json.loads(
                    db_record.observed_outcome_json) if getattr(
                    db_record, "observed_outcome_json", None) else None
                return OperationalOutcomeRecord(
                    outcome_id=db_record.outcome_id,
                    action_id=db_record.action_id,
                    expected_outcome=expected,
                    observed_outcome=observed,
                    confirmation_state=OutcomeConfirmationState(db_record.confirmation_state),
                    resolution_state=OutcomeResolutionState(db_record.resolution_state),
                    journey_id=db_record.journey_id,
                    # D6 Correction: Restore new outcome fields
                    source_reference=db_record.source_reference,
                    closure_reason=db_record.closure_reason,
                    confirmed_at=datetime.fromisoformat(db_record.confirmed_at) if db_record.confirmed_at else None,
                    closed_at=datetime.fromisoformat(db_record.closed_at) if db_record.closed_at else None,
                    reopened_at=datetime.fromisoformat(db_record.reopened_at) if db_record.reopened_at else None
                )
            for o in self._operational_outcomes:
                if o.outcome_id == outcome_id:
                    return o
            return None
        finally:
            db.close()

    def get_operational_outcome_by_action(
            self, action_id: str) -> Optional[OperationalOutcomeRecord]:
        from app.db.database import SessionLocal
        from app.models.governance_storage import OperationalOutcomeModel
        import json
        db = SessionLocal()
        try:
            db_record = db.query(OperationalOutcomeModel).filter(
                OperationalOutcomeModel.action_id == action_id).first()
            if db_record:
                expected = json.loads(
                    db_record.expected_outcome_json) if getattr(
                    db_record, "expected_outcome_json", None) else None
                observed = json.loads(
                    db_record.observed_outcome_json) if getattr(
                    db_record, "observed_outcome_json", None) else None
                return OperationalOutcomeRecord(
                    outcome_id=db_record.outcome_id,
                    action_id=db_record.action_id,
                    expected_outcome=expected,
                    observed_outcome=observed,
                    confirmation_state=OutcomeConfirmationState(db_record.confirmation_state),
                    resolution_state=OutcomeResolutionState(db_record.resolution_state),
                    journey_id=db_record.journey_id,
                    # D6 Correction: Restore new outcome fields
                    source_reference=db_record.source_reference,
                    closure_reason=db_record.closure_reason,
                    confirmed_at=datetime.fromisoformat(db_record.confirmed_at) if db_record.confirmed_at else None,
                    closed_at=datetime.fromisoformat(db_record.closed_at) if db_record.closed_at else None,
                    reopened_at=datetime.fromisoformat(db_record.reopened_at) if db_record.reopened_at else None
                )
            for o in self._operational_outcomes:
                if o.action_id == action_id:
                    return o
            return None
        finally:
            db.close()

    # ======================================================
    # SESR-008: Continuity Validation
    # ======================================================

    def validate_upstream_continuity(
        self,
        child_journey_id: Optional[str],
        parent_id: str,
        parent_type: str,
    ) -> None:
        """
        SESR-008 CFR-001: Validates that:
          1. A parent record with `parent_id` actually exists in the registry.
          2. The child's `journey_id` matches the parent's `journey_id`.

        Args:
            child_journey_id: The journey_id being assigned to the child record.
            parent_id: The ID of the required upstream parent record.
            parent_type: One of 'evaluation', 'recommendation', 'decision', 'action', 'outcome'.

        Raises:
            ContinuityViolationError: If parent is not found or journey_ids do not match.
        """
        parent_record = None
        parent_journey_id = None

        if parent_type == "evaluation":
            parent_record = self.get_rule_evaluation(parent_id)
            if parent_record:
                parent_journey_id = parent_record.journey_id
        elif parent_type == "recommendation":
            parent_record = self.get_recommendation(parent_id)
            if parent_record:
                parent_journey_id = parent_record.journey_id
        elif parent_type == "decision":
            parent_record = self.get_human_decision(parent_id)
            if parent_record:
                parent_journey_id = parent_record.journey_id
        elif parent_type == "action":
            parent_record = self.get_operational_action(parent_id)
            if parent_record:
                parent_journey_id = parent_record.journey_id
        elif parent_type == "outcome":
            parent_record = self.get_operational_outcome(parent_id)
            if parent_record:
                parent_journey_id = parent_record.journey_id
        else:
            raise ContinuityViolationError(
                f"[SESR-008] Unknown parent_type '{parent_type}'. "
                f"Continuity validation requires a known record type."
            )

        if parent_record is None:
            raise ContinuityViolationError(
                f"[SESR-008] CVC-031: Parent record '{parent_id}' of type '{parent_type}' "
                f"does not exist in the registry. Cannot establish continuity."
            )

        # Both must carry a journey_id, and they must match
        if child_journey_id is None or parent_journey_id is None:
            raise ContinuityViolationError(
                f"[SESR-008] CVC-031: journey_id is missing. "
                f"child_journey_id={child_journey_id}, parent_journey_id={parent_journey_id}. "
                f"Every governed record must carry an explicit journey_id."
            )

        if child_journey_id != parent_journey_id:
            raise ContinuityViolationError(
                f"[SESR-008] CVC-031: Journey identity mismatch. "
                f"child_journey_id='{child_journey_id}' does not match "
                f"parent_journey_id='{parent_journey_id}' (parent_id='{parent_id}')."
            )

        logger.debug(
            f"[SESR-008] Continuity validated: parent={parent_id} (type={parent_type}), "
            f"journey_id={child_journey_id}"
        )

    def update_operational_outcome(self, outcome_id: str, **kwargs):
        from app.core.exceptions import PersistenceError
        if kwargs.pop("simulate_persistence_error", False):
            raise PersistenceError("Database connection failed during Operational Outcome commit.")

        from app.db.database import SessionLocal
        from app.models.governance_storage import OperationalOutcomeModel
        db = SessionLocal()
        try:
            db_record = db.query(OperationalOutcomeModel).filter(OperationalOutcomeModel.outcome_id == outcome_id).with_for_update().first()
            if db_record:
                if "confirmation_state" in kwargs:
                    db_record.confirmation_state = kwargs["confirmation_state"].value
                if "resolution_state" in kwargs:
                    db_record.resolution_state = kwargs["resolution_state"].value
                db.commit()

            for i, o in enumerate(self._operational_outcomes):
                if o.outcome_id == outcome_id:
                    import dataclasses
                    updated = dataclasses.replace(o, **kwargs)
                    self._operational_outcomes[i] = updated
                    return updated

            if db_record:
                return self.get_operational_outcome(outcome_id)
            return None
        finally:
            db.close()

    def _ensure_policies_loaded(self):
        if not self._policies:
            from app.db.database import SessionLocal
            from app.models.governance_storage import GovernedPolicyVersionModel
            db = SessionLocal()
            try:
                rows = db.query(GovernedPolicyVersionModel).all()
                for row in rows:
                    p = self.get_policy_by_version(row.policy_id, row.version)
                    if p and not any(x.policy_id == p.policy_id and x.version == p.version for x in self._policies):
                        self._policies.append(p)
            finally:
                db.close()

    def _ensure_rules_loaded(self):
        if not self._rules:
            from app.db.database import SessionLocal
            from app.models.governance_storage import GovernedRuleVersionModel
            db = SessionLocal()
            try:
                rows = db.query(GovernedRuleVersionModel).all()
                for row in rows:
                    r = self.get_rule_by_version(row.rule_id, row.version)
                    if r and not any(x.rule_id == r.rule_id and x.version == r.version for x in self._rules):
                        self._rules.append(r)
            finally:
                db.close()

    def _ensure_mappings_loaded(self):
        if not self._recommendation_mappings:
            from app.db.database import SessionLocal
            from app.models.governance_storage import GovernedRecommendationMappingModel
            db = SessionLocal()
            try:
                rows = db.query(GovernedRecommendationMappingModel).all()
                for row in rows:
                    m = self.get_recommendation_mapping_by_version(row.mapping_id, row.version)
                    if m and not any(x.mapping_id == m.mapping_id and x.version == m.version for x in self._recommendation_mappings):
                        self._recommendation_mappings.append(m)
            finally:
                db.close()

    def get_applicable_recommendation_mapping(
            self,
            rule_id: str,
            result: str,
            eval_time: datetime) -> Optional[RecommendationMapping]:
        """RGV-005: Deterministic Mapping Resolution"""
        self._ensure_mappings_loaded()
        applicable = []
        for m in self._recommendation_mappings:
            if m.applicable_rule_id == rule_id and m.eligible_result == result and m.is_applicable(
                    eval_time):
                applicable.append(m)
        if not applicable:
            return None
        if len(applicable) > 1:
            raise ValueError(
                f"GOVERNANCE_AMBIGUITY: Multiple active recommendation mappings found for rule {rule_id}")
        return applicable[0]

    def get_applicable_policies(self, eval_time: datetime) -> List[PolicyVersion]:
        """PRR-003: Resolve Applicable Policy Version"""
        self._ensure_policies_loaded()
        applicable = []
        # Find latest active policy per policy_id that is effective
        for p_id in set(p.policy_id for p in self._policies):
            versions = [p for p in self._policies if p.policy_id ==
                        p_id and p.is_applicable(eval_time)]
            if len(versions) > 1:
                raise ValueError(f"GOVERNANCE_AMBIGUITY: Multiple active policies found for {p_id}")
            if versions:
                applicable.append(versions[0])
        return applicable

    def get_applicable_rules_for_policy(
            self,
            policy_id: str,
            policy_version: str,
            eval_time: datetime) -> List[RuleVersion]:
        """PRR-004: Resolve Applicable Rule Version"""
        self._ensure_rules_loaded()
        applicable = []
        mapped_rules = [r for r in self._rules if r.governing_policy_id ==
                        policy_id and r.governing_policy_version == policy_version]
        for r_id in set(r.rule_id for r in mapped_rules):
            versions = [r for r in mapped_rules if r.rule_id == r_id and r.is_applicable(eval_time)]
            if len(versions) > 1:
                raise ValueError(f"GOVERNANCE_AMBIGUITY: Multiple active rules found for {r_id}")
            if versions:
                applicable.append(versions[0])
        return applicable

    def get_policy_by_version(self, policy_id: str, version: str) -> Optional[PolicyVersion]:
        from app.db.database import SessionLocal
        from app.models.governance_storage import GovernedPolicyVersionModel
        from dateutil.parser import parse
        db = SessionLocal()
        try:
            row = db.query(GovernedPolicyVersionModel).filter_by(
                policy_id=policy_id, version=version
            ).one_or_none()
            if row:
                eff_from = parse(row.effective_from) if row.effective_from else None
                eff_until = parse(row.effective_until) if row.effective_until else None
                app_ts = parse(row.approval_timestamp) if row.approval_timestamp else None
                cr_at = parse(row.created_at) if row.created_at else datetime.utcnow()
                ls = LifecycleState(row.lifecycle_state) if row.lifecycle_state in [e.value for e in LifecycleState] else LifecycleState.ACTIVE
                return PolicyVersion(
                    policy_id=row.policy_id,
                    version=row.version,
                    content=row.content,
                    lifecycle_state=ls,
                    effective_from=eff_from,
                    effective_until=eff_until,
                    approval_state=row.approval_state or "PENDING",
                    approved_by=row.approved_by,
                    approval_timestamp=app_ts,
                    previous_version=row.previous_version,
                    created_at=cr_at,
                    scope=row.scope or "Global"
                )
            for p in self._policies:
                if p.policy_id == policy_id and p.version == version:
                    return p
            return None
        finally:
            db.close()

    def get_rule_by_version(self, rule_id: str, version: str) -> Optional[RuleVersion]:
        import json
        from app.db.database import SessionLocal
        from app.models.governance_storage import GovernedRuleVersionModel
        from dateutil.parser import parse
        db = SessionLocal()
        try:
            row = db.query(GovernedRuleVersionModel).filter_by(
                rule_id=rule_id, version=version
            ).one_or_none()
            if row:
                eff_from = parse(row.effective_from) if row.effective_from else None
                eff_until = parse(row.effective_until) if row.effective_until else None
                app_ts = parse(row.approval_timestamp) if row.approval_timestamp else None
                cr_at = parse(row.created_at) if row.created_at else datetime.utcnow()
                raw_inputs = json.loads(row.inputs_json) if row.inputs_json else []
                inputs = [
                    RuleInputDefinition(
                        input_name=i.get("input_name", ""),
                        input_type=i.get("input_type", "str"),
                        required=i.get("required", True),
                        expected_source=i.get("expected_source", "DecisionContext")
                    ) for i in raw_inputs
                ]
                outputs = json.loads(row.allowed_outputs_json) if row.allowed_outputs_json else []
                ls = LifecycleState(row.lifecycle_state) if row.lifecycle_state in [e.value for e in LifecycleState] else LifecycleState.ACTIVE
                return RuleVersion(
                    rule_id=row.rule_id,
                    version=row.version,
                    logic_description=row.logic_description or "",
                    lifecycle_state=ls,
                    inputs=inputs,
                    allowed_outputs=outputs,
                    governing_policy_id=row.governing_policy_id,
                    governing_policy_version=row.governing_policy_version,
                    effective_from=eff_from,
                    effective_until=eff_until,
                    approval_state=row.approval_state or "PENDING",
                    approved_by=row.approved_by,
                    approval_timestamp=app_ts,
                    previous_version=row.previous_version,
                    created_at=cr_at
                )
            for r in self._rules:
                if r.rule_id == rule_id and r.version == version:
                    return r
            return None
        finally:
            db.close()

    def get_recommendation_mapping_by_version(
            self,
            mapping_id: str,
            version: str) -> Optional[RecommendationMapping]:
        from app.db.database import SessionLocal
        from app.models.governance_storage import GovernedRecommendationMappingModel
        from dateutil.parser import parse
        import json
        db = SessionLocal()
        try:
            row = db.query(GovernedRecommendationMappingModel).filter_by(
                mapping_id=mapping_id, version=version
            ).one_or_none()
            if row:
                eff_from = parse(row.effective_from) if row.effective_from else None
                cr_at = parse(row.created_at) if row.created_at else datetime.utcnow()
                ar = AuthorityRequirement(row.authority_requirement) if row.authority_requirement in [e.value for e in AuthorityRequirement] else AuthorityRequirement.INFORMATIONAL
                ls = LifecycleState(row.lifecycle_state) if row.lifecycle_state in [e.value for e in LifecycleState] else LifecycleState.ACTIVE
                return RecommendationMapping(
                    mapping_id=row.mapping_id,
                    version=row.version,
                    applicable_rule_id=row.applicable_rule_id,
                    eligible_result=row.eligible_result,
                    recommendation_template=row.recommendation_template,
                    authority_requirement=ar,
                    priority=row.priority,
                    lifecycle_state=ls,
                    business_impact_template=row.business_impact_template or "",
                    expected_outcome_template=row.expected_outcome_template or "",
                    problem_template=row.problem_template or "",
                    effective_from=eff_from,
                    created_at=cr_at,
                    allowed_action_types=json.loads(row.allowed_action_types_json) if getattr(row, "allowed_action_types_json", None) else []
                )
            for m in self._recommendation_mappings:
                if m.mapping_id == mapping_id and m.version == version:
                    return m
            return None
        finally:
            db.close()


governance_registry = GovernanceRegistry()

# ======================================================
# SEED INITIAL GOVERNED DATA
# ======================================================


def initialize_registry_seeds():
    # 1. Evidence Availability Policy
    if not governance_registry.get_policy_by_version("POL-001", "V1"):
        governance_registry.register_policy(PolicyVersion(
            policy_id="POL-001",
            version="V1",
            content="Recommendations require operational evidence.",
            lifecycle_state=LifecycleState.ACTIVE,
            effective_from=datetime.utcnow() - timedelta(days=30),
            approval_state="APPROVED",
            approved_by="System Administrator"
        ))

    # 2. Event Type Authority Policy
    if not governance_registry.get_policy_by_version("POL-002", "V1"):
        governance_registry.register_policy(PolicyVersion(
            policy_id="POL-002",
            version="V1",
            content="Only EHR, Phone, Email, Manual events are authorized.",
            lifecycle_state=LifecycleState.ACTIVE,
            effective_from=datetime.utcnow() - timedelta(days=30),
            approval_state="APPROVED",
            approved_by="System Administrator"
        ))

    # 3. Production Source Authority Policy (Item 18)
    if not governance_registry.get_policy_by_version("POL-003", "V1"):
        governance_registry.register_policy(PolicyVersion(
            policy_id="POL-003",
            version="V1",
            content="Production execution is restricted to Practice Fusion sources.",
            lifecycle_state=LifecycleState.ACTIVE,
            effective_from=datetime.utcnow() - timedelta(days=30),
            approval_state="APPROVED",
            approved_by="System Administrator"
        ))

    # 3. Clinic No-Show Rule
    if not governance_registry.get_rule_by_version("RULE-SCH-001", "V1"):
        governance_registry.register_rule(RuleVersion(
            rule_id="RULE-SCH-001",
            version="V1",
            logic_description="Flag patient no-show gaps in schedule.",
            lifecycle_state=LifecycleState.ACTIVE,
            governing_policy_id="POL-001",
            governing_policy_version="V1",
            inputs=[
                RuleInputDefinition("primary_context", "str", True, "DecisionContext"),
                RuleInputDefinition("secondary_context", "str", True, "DecisionContext"),
                RuleInputDefinition("event_type", "str", True, "DecisionContext")
            ],
            allowed_outputs=["CONDITION_MET", "CONDITION_NOT_MET", "NOT_EVALUABLE"],
            effective_from=datetime.utcnow() - timedelta(days=30),
            approval_state="APPROVED",
            approved_by="System Administrator"
        ))

    # 4. Clinic Wait Time Rule
    if not governance_registry.get_rule_by_version("RULE-SCH-002", "V1"):
        governance_registry.register_rule(RuleVersion(
            rule_id="RULE-SCH-002",
            version="V1",
            logic_description="Flag wait times exceeding threshold.",
            lifecycle_state=LifecycleState.ACTIVE,
            governing_policy_id="POL-001",
            governing_policy_version="V1",
            inputs=[
                RuleInputDefinition("primary_context", "str", True, "DecisionContext"),
                RuleInputDefinition("secondary_context", "str", True, "DecisionContext")
            ],
            allowed_outputs=["CONDITION_MET", "CONDITION_NOT_MET", "NOT_EVALUABLE"],
            effective_from=datetime.utcnow() - timedelta(days=30),
            approval_state="APPROVED",
            approved_by="System Administrator"
        ))

    # 5. System Blocked Rule (PF-Only)
    if not governance_registry.get_rule_by_version("RULE-SYS-BLOCKED", "V1"):
        governance_registry.register_rule(RuleVersion(
            rule_id="RULE-SYS-BLOCKED",
            version="V1",
            logic_description="Blocks action if source is not Practice Fusion in Production.",
            lifecycle_state=LifecycleState.ACTIVE,
            governing_policy_id="POL-003",
            governing_policy_version="V1",
            inputs=[
                RuleInputDefinition("source_connector", "str", True, "DecisionContext")
            ],
            allowed_outputs=["NOT_EVALUABLE", "BLOCKED"],
            effective_from=datetime.utcnow() - timedelta(days=30),
            approval_state="APPROVED",
            approved_by="System Administrator"
        ))

    # 6. Recommendation Mappings (SESR-004)
    if not governance_registry.get_recommendation_mapping_by_version("REC-MAP-001", "V1"):
        governance_registry.register_recommendation_mapping(
            RecommendationMapping(
                mapping_id="REC-MAP-001",
                version="V1",
                applicable_rule_id="RULE-SCH-001",
                eligible_result="CONDITION_MET",
                recommendation_template="Consider sending an SMS reschedule link and dispatching a $25 fee claim.",
                authority_requirement=AuthorityRequirement.APPROVAL_REQUIRED,
                priority="Moderate",
                business_impact_template="-$150.00 estimated revenue loss.",
                expected_outcome_template="Recovery of $25 fee and rescheduled visit.",
                problem_template="Patient No-Show",
                lifecycle_state=LifecycleState.ACTIVE,
                effective_from=datetime.utcnow() -
                timedelta(
                    days=30),
                allowed_action_types=["RESCHEDULE_APPOINTMENT", "SEND_NOTIFICATION"]
            ))

    if not governance_registry.get_recommendation_mapping_by_version("REC-MAP-002", "V1"):
        governance_registry.register_recommendation_mapping(
            RecommendationMapping(
                mapping_id="REC-MAP-002",
                version="V1",
                applicable_rule_id="RULE-SCH-002",
                eligible_result="CONDITION_MET",
                recommendation_template="Suggest re-routing to next available Room and notifying the Clinic Administrator.",
                authority_requirement=AuthorityRequirement.REVIEW_REQUIRED,
                priority="High",
                business_impact_template="High risk of patient satisfaction drop and negative reviews.",
                expected_outcome_template="Wait time mitigated, patient informed.",
                problem_template="Extended Patient Wait Time",
                lifecycle_state=LifecycleState.ACTIVE,
                effective_from=datetime.utcnow() -
                timedelta(
                    days=30),
                allowed_action_types=["UPDATE_OPERATIONAL_STATUS", "SEND_NOTIFICATION"]
            ))

    if not governance_registry.get_recommendation_mapping_by_version("REC-MAP-003", "V1"):
        governance_registry.register_recommendation_mapping(RecommendationMapping(
            mapping_id="REC-MAP-003",
            version="V1",
            applicable_rule_id="RULE-SYS-BLOCKED",
            eligible_result="NOT_EVALUABLE",
            recommendation_template="Review policy rules.",
            authority_requirement=AuthorityRequirement.INFORMATIONAL,
            priority="Information",
            business_impact_template="Ensured compliance and security.",
            expected_outcome_template="Maintained system integrity.",
            problem_template="Action Blocked by Governance",
            lifecycle_state=LifecycleState.ACTIVE,
            effective_from=datetime.utcnow() - timedelta(days=30),
            allowed_action_types=["CREATE_FOLLOWUP_TASK"]
        ))

    if not governance_registry.get_recommendation_mapping_by_version("REC-MAP-004", "V1"):
        governance_registry.register_recommendation_mapping(RecommendationMapping(
            mapping_id="REC-MAP-004",
            version="V1",
            applicable_rule_id="RULE-SCH-001",
            eligible_result="NOT_EVALUABLE",
            recommendation_template="Request Human Review for incomplete schedule data.",
            authority_requirement=AuthorityRequirement.REVIEW_REQUIRED,
            priority="Information",
            business_impact_template="None",
            expected_outcome_template="Data integrity maintained.",
            problem_template="Unverifiable Schedule Context",
            lifecycle_state=LifecycleState.ACTIVE,
            effective_from=datetime.utcnow() - timedelta(days=30),
            allowed_action_types=["CREATE_FOLLOWUP_TASK"]
        ))

    # 6. Authority Configurations (SESR-005)
    governance_registry.register_authority_config(
        AuthorityConfiguration(
            role="Clinic Manager",
            allowed_decisions=[
                DecisionType.APPROVED,
                DecisionType.REJECTED,
                DecisionType.OVERRIDDEN,
                DecisionType.RETURNED_FOR_REVIEW],
            can_override=True,
            requires_reason_for=[
                DecisionType.REJECTED,
                DecisionType.OVERRIDDEN]))

    governance_registry.register_authority_config(AuthorityConfiguration(
        role="Front Desk",
        allowed_decisions=[DecisionType.APPROVED, DecisionType.REJECTED],
        can_override=False,
        requires_reason_for=[DecisionType.REJECTED]
    ))
