from typing import Literal, Dict, Any, List, Optional
from dataclasses import dataclass
import logging

from app.db.database import SessionLocal
from app.models.governance_storage import RecommendationModel, RuleEvaluationModel
from app.services.governance_registry import governance_registry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReproductionResult:
    status: Literal["MATCH", "MISMATCH", "NOT_REPRODUCIBLE"]
    recommendation_id: str
    original: Dict[str, Any]
    reproduced: Optional[Dict[str, Any]]
    differences: List[Dict[str, Any]]
    diagnostic_stage: Optional[str] = None
    diagnostic_code: Optional[str] = None
    missing_dependency: Optional[Dict[str, Any]] = None


class ReconstructionEngine:
    """
    SESR-010: Decision Reproducibility & Deterministic Reconstruction Engine.
    Re-runs historical governed logic against historical inputs to prove determinism.
    """

    def __init__(self):
        self.registry = governance_registry

    def reproduce_decision(self, recommendation_id: str) -> ReproductionResult:
        """
        Attempts to reproduce a governed decision strictly from its historical binding contexts.
        Does not query active/current versions. Does not cause side effects.
        """
        db = SessionLocal()
        try:
            # 1. Fetch Historical Context Binding from new governed storage
            record = db.query(RecommendationModel).filter(
                RecommendationModel.recommendation_id == recommendation_id).first()
            if not record:
                return ReproductionResult(
                    status="NOT_REPRODUCIBLE",
                    recommendation_id=recommendation_id,
                    original={},
                    reproduced=None,
                    differences=[],
                    diagnostic_stage="historical_binding",
                    diagnostic_code="MISSING_RECOMMENDATION"
                )

            original_rec = {
                "priority": record.priority,
                "action": record.content,
                "mapping_version": record.mapping_version,
                "authority": record.authority_requirement,
                "problem": getattr(record, "problem", None),
                "business_impact": getattr(record, "business_impact", None),
                "expected_outcome": getattr(record, "expected_outcome", None)
            }

            if not record.mapping_version:
                return ReproductionResult(
                    status="NOT_REPRODUCIBLE",
                    recommendation_id=recommendation_id,
                    original=original_rec,
                    reproduced=None,
                    differences=[],
                    diagnostic_stage="historical_binding",
                    diagnostic_code="MISSING_MAPPING_VERSION"
                )

            # Fetch real historical inputs used for the evaluation (Issue #6 Fix)
            eval_record = db.query(RuleEvaluationModel).filter(
                RuleEvaluationModel.evaluation_id == record.rule_evaluation_id).first()
            if not eval_record:
                return ReproductionResult(
                    status="NOT_REPRODUCIBLE",
                    recommendation_id=recommendation_id,
                    original=original_rec,
                    reproduced=None,
                    differences=[],
                    diagnostic_stage="historical_binding",
                    diagnostic_code="MISSING_RULE_EVALUATION"
                )

            # 2. Fetch Historical Logic Versions
            historical_policy = self.registry.get_policy_by_version(
                eval_record.policy_id, eval_record.policy_version)
            if not historical_policy:
                return ReproductionResult(
                    status="NOT_REPRODUCIBLE",
                    recommendation_id=recommendation_id,
                    original=original_rec,
                    reproduced=None,
                    differences=[],
                    diagnostic_stage="historical_logic",
                    diagnostic_code="MISSING_POLICY",
                    missing_dependency={"type": "Policy", "id": eval_record.policy_id, "version": eval_record.policy_version}
                )

            historical_mapping = self.registry.get_recommendation_mapping_by_version(
                record.mapping_id, record.mapping_version)
            if not historical_mapping:
                return ReproductionResult(
                    status="NOT_REPRODUCIBLE",
                    recommendation_id=recommendation_id,
                    original=original_rec,
                    reproduced=None,
                    differences=[],
                    diagnostic_stage="historical_logic",
                    diagnostic_code="MISSING_MAPPING",
                    missing_dependency={"type": "RecommendationMapping", "id": record.mapping_id, "version": record.mapping_version}
                )

            historical_rule = self.registry.get_rule_by_version(
                eval_record.rule_id, eval_record.rule_version)
            if not historical_rule:
                return ReproductionResult(
                    status="NOT_REPRODUCIBLE",
                    recommendation_id=recommendation_id,
                    original=original_rec,
                    reproduced=None,
                    differences=[],
                    diagnostic_stage="historical_logic",
                    diagnostic_code="MISSING_RULE",
                    missing_dependency={"type": "Rule", "id": eval_record.rule_id, "version": eval_record.rule_version}
                )

            # 3. Deterministic Reconstruction

            # 3a. Reproduce Rule Logic (isolated context)

            import json

            if not eval_record.input_values_json:
                return ReproductionResult(
                    status="NOT_REPRODUCIBLE",
                    recommendation_id=recommendation_id,
                    original=original_rec,
                    reproduced=None,
                    differences=[],
                    diagnostic_stage="historical_logic",
                    diagnostic_code="MISSING_INPUTS"
                )

            try:
                inputs = json.loads(eval_record.input_values_json)
            except json.JSONDecodeError:
                return ReproductionResult(
                    status="NOT_REPRODUCIBLE",
                    recommendation_id=recommendation_id,
                    original=original_rec,
                    reproduced=None,
                    differences=[],
                    diagnostic_stage="historical_logic",
                    diagnostic_code="INVALID_INPUTS"
                )

            # Execute real rule engine logic dynamically, discarding the hardcoded stubs.
            from app.services.rules_engine import rules_engine

            try:
                # evaluate rule logic strictly with the historical inputs
                rule_result = rules_engine._execute_rule_logic(historical_rule, inputs)
            except Exception as e:
                logger.error(f"Rule reproduction failed: {e}")
                rule_result = "NOT_EVALUABLE"

            if rule_result == "NOT_EVALUABLE":
                return ReproductionResult(
                    status="NOT_REPRODUCIBLE",
                    recommendation_id=recommendation_id,
                    original=original_rec,
                    reproduced=None,
                    differences=[],
                    diagnostic_stage="historical_logic",
                    diagnostic_code="MISSING_EXECUTABLE"
                )

            # 3b. Reproduce Recommendation
            reproduced_rec = {}
            if rule_result == "CONDITION_MET" and historical_mapping:
                reproduced_rec = {
                    "priority": historical_mapping.priority,
                    "business_impact": historical_mapping.business_impact_template,
                    "action": historical_mapping.recommendation_template,
                    "expected_outcome": historical_mapping.expected_outcome_template,
                    "mapping_version": historical_mapping.version,
                    "authority": getattr(historical_mapping.authority_requirement, "value", str(historical_mapping.authority_requirement)),
                    "problem": getattr(historical_mapping, "problem_template", None)
                }
            elif rule_result == "CONDITION_NOT_MET":
                reproduced_rec = {
                    "action": "NO_RECOMMENDATION",
                    "priority": "Information",
                    "mapping_version": None,
                    "authority": None,
                    "problem": None,
                    "business_impact": None,
                    "expected_outcome": None
                }

            # 4. Compare Outputs
            # Compare canonical contract fields
            diffs = []
            compare_keys = ["priority", "action", "mapping_version"]
            # If historical original record had these fields persisted, include them in comparison
            for optional_k in ["authority", "problem", "business_impact", "expected_outcome"]:
                if original_rec.get(optional_k) is not None:
                    compare_keys.append(optional_k)

            for k in compare_keys:
                orig_val = original_rec.get(k)
                repr_val = reproduced_rec.get(k)
                if orig_val != repr_val:
                    diffs.append({
                        "field": k,
                        "original": orig_val,
                        "reproduced": repr_val
                    })

            if not diffs:
                status = "MATCH"
            else:
                status = "MISMATCH"

            return ReproductionResult(
                status=status,
                recommendation_id=recommendation_id,
                original=original_rec,
                reproduced=reproduced_rec,
                differences=diffs
            )

        finally:
            db.close()


reconstruction_engine = ReconstructionEngine()
