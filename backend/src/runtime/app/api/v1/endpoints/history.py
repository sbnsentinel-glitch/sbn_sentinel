from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, Any

from app.db.database import get_db

from app.api.deps import get_current_user, get_scoped_user, get_signal_for_actor
from app.models.user import User, UserRole
from app.models.governance_storage import (
    RecommendationModel,
    RuleEvaluationModel,
    HumanDecisionModel,
    OperationalActionModel,
    ExecutionAttemptModel,
    OperationalOutcomeModel
)
from app.models.evidence import EvidenceModel
from app.models.decision_context_models import ContextEvidenceModel
from app.services.reconstruction_engine import reconstruction_engine

router = APIRouter()


@router.get("/recommendations/{recommendation_id}")
def get_historical_recommendation(
    recommendation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_scoped_user)
) -> Dict[str, Any]:
    """
    Returns exact historical bindings for a specific recommendation.
    """
    rec = db.query(RecommendationModel).filter(RecommendationModel.recommendation_id == recommendation_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")

    # Missing or unresolvable ownership must deny access for ordinary users
    if current_user.role != UserRole.SYSTEM_ADMINISTRATOR.value:
        if not rec.intended_target_reference:
            raise HTTPException(status_code=403, detail="Missing intended target reference denies access")
        get_signal_for_actor(db, current_user, rec.intended_target_reference)
    return _build_historical_lifecycle(rec, db)


@router.get("/journeys/{journey_id}")
def get_historical_journey(
    journey_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_scoped_user)
) -> Dict[str, Any]:
    """
    Returns ordered collection of historical recommendations for a journey.
    """
    recs = db.query(RecommendationModel).filter(RecommendationModel.journey_id == journey_id).order_by(RecommendationModel.generated_at.asc()).all()
    if not recs:
        raise HTTPException(status_code=404, detail="No historical records found for journey")

    if current_user.role != UserRole.SYSTEM_ADMINISTRATOR.value:
        if not recs[0].intended_target_reference:
            raise HTTPException(status_code=403, detail="Missing intended target reference denies access")
        get_signal_for_actor(db, current_user, recs[0].intended_target_reference)

    # If ambiguous (multiple recommendations), the spec says:
    # "Multiple historical recommendations + journey-only request => ambiguous, not arbitrary selection."
    if len(recs) > 1:
        return {
            "anchor": {
                "object_type": "journey",
                "object_id": journey_id,
                "journey_id": journey_id,
                "mode": "historical"
            },
            "bindings": _empty_bindings(),
            "technical_state": "ambiguous"
        }

    return _build_historical_lifecycle(recs[0], db)


@router.get("/recommendations/{recommendation_id}/reproduction")
def get_reproduction(
    recommendation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Executes the deterministic reconstruction engine and returns the D8 ReproductionResult.
    Accessible to any authenticated user; diagnostic payload restricted to System Administrators.
    """
    rec = db.query(RecommendationModel).filter(RecommendationModel.recommendation_id == recommendation_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    # Missing or unresolvable ownership must deny access for ordinary users
    if current_user.role != UserRole.SYSTEM_ADMINISTRATOR.value:
        if not rec.intended_target_reference:
            raise HTTPException(status_code=403, detail="Missing intended target reference denies access")
        get_signal_for_actor(db, current_user, rec.intended_target_reference)

    # Call reconstruction engine
    result = reconstruction_engine.reproduce_decision(recommendation_id)

    is_authorized_diagnostic = current_user.role == UserRole.SYSTEM_ADMINISTRATOR.value

    # Return as dict
    return {
        "status": result.status,
        "recommendation_id": result.recommendation_id,
        "original": result.original,
        "reproduced": result.reproduced,
        "differences": result.differences,
        "diagnostic": {
            "stage": result.diagnostic_stage,
            "code": result.diagnostic_code,
            "missing_dependency": result.missing_dependency
        } if (result.diagnostic_code and is_authorized_diagnostic) else None
    }


def _empty_bindings():
    return {
        "evidence_refs": [],
        "decision_context_id": None,
        "policy": None,
        "rule_evaluations": [],
        "recommendations": [],
        "decisions": [],
        "actions": []
    }


def _build_historical_lifecycle(rec: RecommendationModel, db: Session) -> Dict[str, Any]:
    decision_context_id = rec.decision_context_id
    evidence_refs = []
    errors = []

    if not decision_context_id:
        errors.append("missing_context")
    else:
        evidence_records = db.query(ContextEvidenceModel).filter(ContextEvidenceModel.context_id == decision_context_id).all()
        for ev in evidence_records:
            evidence = db.query(EvidenceModel).filter(EvidenceModel.evidence_id == ev.id).all()
            if len(evidence) > 1:
                errors.append("ambiguous_evidence")
            elif len(evidence) == 1:
                e = evidence[0]
                evidence_refs.append({
                    "evidence_id": e.evidence_id,
                    "version": str(e.version) if e.version is not None else None
                })
            else:
                errors.append("missing_evidence")

    evals = []
    policy = None
    rule_eval = None
    if rec.rule_evaluation_id:
        rule_eval_rows = db.query(RuleEvaluationModel).filter(RuleEvaluationModel.evaluation_id == rec.rule_evaluation_id).all()
        if len(rule_eval_rows) > 1:
            errors.append("ambiguous_rule_eval")
        elif len(rule_eval_rows) == 1:
            rule_eval = rule_eval_rows[0]
            evals.append({
                "evaluation_id": rule_eval.evaluation_id,
                "rule_id": rule_eval.rule_id,
                "rule_version": rule_eval.rule_version,
                "policy_id": rule_eval.policy_id,
                "policy_version": rule_eval.policy_version,
                "evaluated_at": rule_eval.evaluation_timestamp.isoformat() + "Z" if hasattr(rule_eval.evaluation_timestamp, "isoformat") else (str(rule_eval.evaluation_timestamp) + ("Z" if not str(rule_eval.evaluation_timestamp).endswith("Z") else "")) if rule_eval.evaluation_timestamp else None
            })
            policy = {
                "policy_id": rule_eval.policy_id,
                "policy_version": rule_eval.policy_version
            }
        else:
            errors.append("missing_rule_eval")
    else:
        errors.append("missing_rule_eval")

    recs_out = [{
        "recommendation_id": rec.recommendation_id,
        "mapping_id": rec.mapping_id,
        "mapping_version": rec.mapping_version,
        "status": rec.status,
        "generated_at": rec.generated_at.isoformat() + "Z" if hasattr(rec.generated_at, 'isoformat') else (rec.generated_at + "Z" if rec.generated_at and not rec.generated_at.endswith("Z") else rec.generated_at)
    }]

    decisions_out = []
    decisions = db.query(HumanDecisionModel).filter(HumanDecisionModel.recommendation_id == rec.recommendation_id).all()
    for d in decisions:
        decisions_out.append({
            "decision_id": d.decision_id,
            "actor_id": d.actor_id,
            "decision_type": d.decision_type,
            "status": d.status,
            "authority_basis": d.authority_basis if d.authority_basis else "UNAVAILABLE_LEGACY",
            "reason": d.reason,
            "override_indicator": d.override_indicator,
            "timestamp": d.decision_timestamp if d.decision_timestamp else None
        })

    actions_out = []
    for d in decisions:
        actions = db.query(OperationalActionModel).filter(OperationalActionModel.authorization_reference == d.decision_id).all()
        for a in actions:
            attempts_out = []
            attempts = db.query(ExecutionAttemptModel).filter(ExecutionAttemptModel.action_id == a.action_id).all()
            for att in attempts:
                attempts_out.append({
                    "attempt_id": att.attempt_id,
                    "attempt_number": att.attempt_number,
                    "result": att.result
                })

            outcome_out = None
            outcomes = db.query(OperationalOutcomeModel).filter(OperationalOutcomeModel.action_id == a.action_id).all()
            if len(outcomes) > 1:
                errors.append("ambiguous_outcome")
            elif len(outcomes) == 1:
                outcome = outcomes[0]
                outcome_out = {
                    "outcome_id": outcome.outcome_id,
                    "confirmation_state": outcome.confirmation_state,
                    "resolution_state": outcome.resolution_state
                }

            actions_out.append({
                "action_id": a.action_id,
                "action_type": a.action_type,
                "status": a.status,
                "current_result": a.current_result,
                "attempts": attempts_out,
                "outcome": outcome_out
            })

    if errors:
        if "ambiguous_evidence" in errors or "ambiguous_rule_eval" in errors or "ambiguous_outcome" in errors:
            technical_state = "ambiguous"
        else:
            technical_state = "orphaned"
    else:
        technical_state = "valid"

    return {
        "anchor": {
            "object_type": "recommendation",
            "object_id": rec.recommendation_id,
            "journey_id": rec.journey_id,
            "mode": "historical"
        },
        "bindings": {
            "evidence_refs": evidence_refs,
            "decision_context_id": decision_context_id,
            "policy": policy,
            "rule_evaluations": evals,
            "recommendations": recs_out,
            "decisions": decisions_out,
            "actions": actions_out
        },
        "technical_state": technical_state
    }
