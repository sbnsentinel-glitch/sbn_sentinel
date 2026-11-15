"""
D4 — Decision Basis Read-Only Projection Endpoint
GET /api/v1/decision-basis/{signal_id}

Assembles a DecisionBasisDTO from existing authoritative persisted records.
NEVER evaluates evidence, chooses policy, executes rules, generates
recommendations, or changes state. READ-ONLY.
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from typing import Any, Optional
from app.api.deps import RoleChecker
from app.db.database import get_db
from app.models.signal import SignalModel
from app.models.user import UserRole
from app.models.governance_storage import (
    RuleEvaluationModel,
    GovernedPolicyVersionModel,
    RecommendationModel,
)
from app.models.decision_context_models import (
    ContextMissingEvidenceModel,
    ContextConflictsModel,
    ContextFreshnessModel,
    ContextEvidenceModel,
    ContextProvenanceModel,
)

router = APIRouter()

# Roles permitted to read the Decision Basis
_ALLOWED_ROLES = [
    UserRole.CLINIC_MANAGER.value,
    UserRole.ORGANIZATION_ADMINISTRATOR.value,
    UserRole.SYSTEM_ADMINISTRATOR.value,
]


def _build_object_ref(signal: SignalModel) -> dict:
    return {
        "object_type": "Signal",
        "object_id": signal.id,
    }


def _build_decision_context(
    signal: SignalModel,
    context_id: Optional[str],
    evaluated_at: Optional[str],
    db: Optional[Session] = None,
) -> dict:
    """
    Derives the Decision Context identity. Mode is read from metadata.is_historical.
    Evaluated_at is strictly from authoritative RuleEvaluationModel.
    Sufficiency_status is read from authoritative DecisionContextModel.
    """
    metadata: dict = signal.metadata_data or {}
    is_historical: Optional[bool] = metadata.get("is_historical")
    mode = "historical" if is_historical is True else "current"

    sufficiency_status = None
    if context_id and db:
        from app.models.intelligence import DecisionContextModel
        ctx_row = db.query(DecisionContextModel).filter(
            DecisionContextModel.id == context_id
        ).first()
        if ctx_row and getattr(ctx_row, "sufficiency_status", None):
            sufficiency_status = ctx_row.sufficiency_status

    return {
        "context_id": context_id,
        "status": signal.primary_context,
        "sufficiency_status": sufficiency_status,
        "evaluated_at": evaluated_at,
        "mode": mode,
    }


def _build_evidence(context_id: Optional[str], db: Session) -> dict:
    """
    Assembles evidence sections from persisted AIS-002 records.
    Returns null-safe lists — never invents content, no fallback logic.
    """
    used = []
    missing = []
    conflicts = []
    freshness = []

    if context_id:
        # Used evidence
        ev_rows = db.query(ContextEvidenceModel).filter(
            ContextEvidenceModel.context_id == context_id
        ).all()
        for ev in ev_rows:
            used.append({
                "evidence_id": ev.id,
                "type": ev.evidence_type,
                "value": ev.evidence_value,
                "retrieved_at": ev.added_at.isoformat() if ev.added_at else None,
                "retrieval_status": "RETRIEVED",
            })

        # Missing evidence
        miss_rows = db.query(ContextMissingEvidenceModel).filter(
            ContextMissingEvidenceModel.context_id == context_id
        ).all()
        for m in miss_rows:
            missing.append({
                "evidence_id": m.id,
                "type": m.expected_evidence_type,
                "impact_level": m.impact_level,
                "retrieval_status": "MISSING",
            })

        # Conflicts
        conf_rows = db.query(ContextConflictsModel).filter(
            ContextConflictsModel.context_id == context_id
        ).all()
        for c in conf_rows:
            conflicts.append({
                "conflict_id": c.id,
                "evidence_a_id": c.evidence_a_id,
                "evidence_b_id": c.evidence_b_id,
                "description": c.conflict_description,
                "resolution_status": c.resolution_status,
            })

        # Freshness
        fresh_rows = db.query(ContextFreshnessModel).filter(
            ContextFreshnessModel.context_id == context_id
        ).all()
        for f in fresh_rows:
            freshness.append({
                "evidence_id": f.evidence_id,
                "age_seconds": f.age_seconds,
                "is_stale": f.is_stale,
                "freshness_status": "STALE" if f.is_stale else "CURRENT",
            })

    return {
        "used": used,
        "missing": missing,
        "conflicts": conflicts,
        "freshness": freshness,
    }


def _build_policy(policy_id: Optional[str], policy_version: Optional[str], db: Session) -> Optional[dict]:
    """
    Returns policy basis from GovernedPolicyVersionModel using authoritative IDs.
    """
    if not policy_id or not policy_version:
        return None

    row = db.query(GovernedPolicyVersionModel).filter(
        GovernedPolicyVersionModel.policy_id == policy_id,
        GovernedPolicyVersionModel.version == policy_version,
    ).first()

    if not row:
        return None

    return {
        "policy_id": row.policy_id,
        "version": row.version,
        "lifecycle_state": row.lifecycle_state,
        "effective_from": row.effective_from,
        "effective_until": row.effective_until,
    }


def _build_provenance(context_id: Optional[str], db: Session) -> Optional[dict]:
    """
    Level-3 provenance based on exact authoritative context ID.
    """
    if not context_id:
        return None

    prov_rows = db.query(ContextProvenanceModel).filter(
        ContextProvenanceModel.context_id == context_id
    ).all()

    if not prov_rows:
        return None

    provenance_items = [
        {
            "evidence_id": p.evidence_id,
            "source_system": p.source_system,
            "ingestion_timestamp": (
                p.ingestion_timestamp.isoformat() if p.ingestion_timestamp else None
            ),
        }
        for p in prov_rows
    ]

    return {
        "context_id": context_id,
        "provenance_items": provenance_items,
    }


@router.get("/{signal_id}")
def get_decision_basis(
    signal_id: str,
    rule_evaluation_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: Any = Depends(RoleChecker(_ALLOWED_ROLES)),
):
    """
    D4 — Read-only Decision Basis projection for a governed Signal.
    Resolves data via authoritative persisted RuleEvaluationModels.
    """
    signal = db.query(SignalModel).filter(SignalModel.id == signal_id).first()
    if not signal:
        raise HTTPException(status_code=404, detail=f"Signal '{signal_id}' not found.")

    metadata: dict = signal.metadata_data or {}
    journey_id = metadata.get("correlation_id") or metadata.get("pipeline_event_id") or signal.id

    # Resolve from authoritative persisted RuleEvaluationModel
    evals = db.query(RuleEvaluationModel).filter(
        RuleEvaluationModel.journey_id == journey_id
    ).all()

    if not evals:
        # No authoritative rules engine output -> not evaluated
        # D4: Still preserve persisted context evidence, missing, conflicts, and freshness
        from app.models.intelligence import DecisionContextModel
        ctx_model = db.query(DecisionContextModel).filter(
            (DecisionContextModel.event_id == signal.id) | (DecisionContextModel.id == signal.id)
        ).first()
        context_id = ctx_model.id if ctx_model else None
        evidence_dict = _build_evidence(context_id, db) if context_id else {"used": [], "missing": [], "conflicts": [], "freshness": []}
        return {
            "object_ref": _build_object_ref(signal),
            "journey_id": journey_id,
            "decision_context": _build_decision_context(signal, context_id, None, db),
            "evidence": evidence_dict,
            "policy": None,
            "rules": [],
            "provenance": None,
            "technical_state": "unavailable",
        }

    if rule_evaluation_id:
        target_eval = next((r for r in evals if r.evaluation_id == rule_evaluation_id), None)
        if not target_eval:
            return {
                "object_ref": _build_object_ref(signal),
                "journey_id": journey_id,
                "decision_context": _build_decision_context(signal, None, None, db),
                "evidence": {"used": [], "missing": [], "conflicts": [], "freshness": []},
                "policy": None,
                "rules": [],
                "provenance": None,
                "technical_state": "unavailable",
            }
        context_id = target_eval.decision_context_id
        policy_id = target_eval.policy_id
        policy_version = target_eval.policy_version
        evaluated_at = target_eval.evaluation_timestamp
        evals_to_show = [
            r for r in evals
            if r.evaluation_id == rule_evaluation_id or (
                r.decision_context_id == context_id
                and r.policy_id == policy_id
                and r.policy_version == policy_version
            )
        ]
    else:
        # Check if journey has an authoritative Recommendation resolving the primary basis
        rec = db.query(RecommendationModel).filter(RecommendationModel.journey_id == journey_id).first()
        target_eval = None
        if rec and rec.rule_evaluation_id:
            target_eval = next((r for r in evals if r.evaluation_id == rec.rule_evaluation_id), None)

        if target_eval:
            context_id = target_eval.decision_context_id
            policy_id = target_eval.policy_id
            policy_version = target_eval.policy_version
            evaluated_at = target_eval.evaluation_timestamp
        else:
            context_id = evals[0].decision_context_id
            policy_id = evals[0].policy_id
            policy_version = evals[0].policy_version
            evaluated_at = evals[0].evaluation_timestamp

        # Detect contradictory evaluations for the exact same rule
        rule_results = {}
        for r in evals:
            k = (r.rule_id, r.rule_version, r.decision_context_id)
            if k in rule_results and rule_results[k] != r.result:
                return {
                    "object_ref": _build_object_ref(signal),
                    "journey_id": journey_id,
                    "decision_context": _build_decision_context(signal, None, None, db),
                    "evidence": {"used": [], "missing": [], "conflicts": [], "freshness": []},
                    "policy": None,
                    "rules": [],
                    "provenance": None,
                    "technical_state": "unavailable",
                }
            rule_results[k] = r.result

        evals_to_show = evals

    try:
        evidence = _build_evidence(context_id, db)
        policy = _build_policy(policy_id, policy_version, db)

        if not policy:
            technical_state = "unavailable"
        else:
            technical_state = "ready"

        rules = [
            {
                "evaluation_id": r.evaluation_id,
                "rule_id": r.rule_id,
                "rule_version": r.rule_version,
                "policy_id": r.policy_id,
                "policy_version": r.policy_version,
                "result": r.result,
                "evaluation_timestamp": r.evaluation_timestamp,
            }
            for r in evals_to_show
        ]

        provenance = _build_provenance(context_id, db)
    except Exception:
        technical_state = "unavailable"
        evidence = {"used": [], "missing": [], "conflicts": [], "freshness": []}
        policy = None
        rules = []
        provenance = None

    return {
        "object_ref": _build_object_ref(signal),
        "journey_id": journey_id,
        "decision_context": _build_decision_context(signal, context_id, evaluated_at, db),
        "evidence": evidence,
        "policy": policy,
        "rules": rules,
        "provenance": provenance,
        "technical_state": technical_state,
    }
