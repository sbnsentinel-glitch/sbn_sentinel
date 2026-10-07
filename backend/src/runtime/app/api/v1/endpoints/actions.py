from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Dict, Any
from app.services.operational_execution_engine import operational_execution_engine
from app.api.deps import RoleChecker
from app.models.user import UserRole

router = APIRouter()


class CreateActionRequest(BaseModel):
    decision_id: str
    action_type: str
    target_reference: str
    parameters: Dict[str, Any]


class ExecuteActionRequest(BaseModel):
    action_id: str


@router.post("/")
async def create_operational_action(
    request: CreateActionRequest,
    current_user: Any = Depends(RoleChecker(
        [UserRole.SYSTEM_ADMINISTRATOR.value, UserRole.CLINIC_MANAGER.value, UserRole.FRONT_DESK.value]
    ))
):
    """
    SESR-006: Create a Governed Operational Action based on a Human Decision.
    D6.5: Idempotency — if a non-cancelled action already exists with the same
    (decision_id, action_type, target_reference), return the existing record
    instead of creating a duplicate.
    """
    # D6 Correction: Independent Governance Validation
    from app.db.database import SessionLocal
    from app.models.governance_storage import OperationalActionModel, HumanDecisionModel, RecommendationModel, GovernedRecommendationMappingModel
    import json
    import hashlib
    from sqlalchemy.exc import IntegrityError

    db = SessionLocal()
    try:
        # Validate decision and capability
        decision_row = db.query(HumanDecisionModel).filter(HumanDecisionModel.decision_id == request.decision_id).first()
        if not decision_row:
            raise HTTPException(status_code=400, detail="Decision not found.")

        rec_row = db.query(RecommendationModel).filter(RecommendationModel.recommendation_id == decision_row.recommendation_id).first()
        if not rec_row:
            raise HTTPException(status_code=400, detail="Recommendation not found.")

        mapping_row = db.query(GovernedRecommendationMappingModel).filter(
            GovernedRecommendationMappingModel.mapping_id == rec_row.mapping_id,
            GovernedRecommendationMappingModel.version == rec_row.mapping_version
        ).first()

        allowed_types = []
        if mapping_row and mapping_row.allowed_action_types_json:
            allowed_types = json.loads(mapping_row.allowed_action_types_json)

        if request.action_type not in allowed_types:
            raise HTTPException(status_code=403, detail="Action type not permitted by governance mapping.")

        if rec_row.intended_target_reference and request.target_reference != rec_row.intended_target_reference:
            raise HTTPException(status_code=403, detail="Target reference does not match intended target.")

        # D6 Correction: Canonical material intent & concurrency protection
        canonical_req = json.dumps(request.parameters, sort_keys=True) if request.parameters else "{}"
        intent_string = f"{request.decision_id}|{request.action_type}|{request.target_reference}|{canonical_req}"
        intent_hash = hashlib.sha256(intent_string.encode('utf-8')).hexdigest()

        # Check for existing non-cancelled actions
        existing = (
            db.query(OperationalActionModel)
            .filter(
                OperationalActionModel.authorization_reference == request.decision_id
            )
            .filter(OperationalActionModel.status.notin_(["CANCELLED", "EXPIRED"]))
            .with_for_update()
            .first()
        )

        if existing:
            if existing.intent_hash != intent_hash:
                raise HTTPException(status_code=409, detail="CONFLICT: Action exists with different material intent parameters.")

            try:
                params = json.loads(existing.parameters_json) if getattr(existing, "parameters_json", None) else {}
            except Exception:
                params = {}

            return {
                "status": "IDEMPOTENT",
                "action_id": existing.action_id,
                "action_type": existing.action_type,
                "target_reference": existing.target_reference,
                "current_status": existing.status,
                "current_result": existing.current_result,
                "journey_id": existing.journey_id,
                "parameters": params,
                "message": "Action already exists for this decision. Returning existing record.",
            }
    finally:
        db.close()

    try:
        result = operational_execution_engine.create_action(
            decision_id=request.decision_id,
            action_type_str=request.action_type,
            target_reference=request.target_reference,
            parameters=request.parameters,
            initiator_scope={
                "user_id": current_user.id,
                "org_id": getattr(current_user, "org_id", None),
                "clinic_id": getattr(current_user, "clinic_id", None)
            } if hasattr(current_user, "id") else None,
            intent_hash=intent_hash  # D6: pass intent_hash to engine
        )
    except IntegrityError:
        # D6 Correction: Idempotency gate - return existing if constraint failed
        db = SessionLocal()
        try:
            existing = (
                db.query(OperationalActionModel)
                .filter(OperationalActionModel.intent_hash == intent_hash)
                .first()
            )
            if existing:
                try:
                    params = json.loads(existing.parameters_json) if getattr(existing, "parameters_json", None) else {}
                except Exception:
                    params = {}

                return {
                    "status": "IDEMPOTENT",
                    "action_id": existing.action_id,
                    "action_type": existing.action_type,
                    "target_reference": existing.target_reference,
                    "current_status": existing.status,
                    "current_result": existing.current_result,
                    "journey_id": existing.journey_id,
                    "parameters": params,
                    "message": "Action already exists for this material intent. Returning existing record.",
                }
            else:
                # Fallback if somehow not found
                raise HTTPException(status_code=409, detail="CONFLICT: Action exists with different material intent parameters.")
        finally:
            db.close()

    if result["status"] == "ERROR":
        # Handle IntegrityError caught by engine, or just conflict
        if result.get("code") == "CONFLICT":
            raise HTTPException(status_code=409, detail=result["message"])
        # Handle idempotent success that engine might return
        if result.get("status") == "IDEMPOTENT":
            return result
        raise HTTPException(status_code=400, detail=result["message"])

    return result


@router.post("/execute")
async def execute_operational_action(
    request: ExecuteActionRequest,
    current_user: Any = Depends(
        RoleChecker([UserRole.SYSTEM_ADMINISTRATOR.value, UserRole.CLINIC_MANAGER.value])
    )
):
    """
    SESR-006: Validate execution eligibility and attempt action.
    """
    result = operational_execution_engine.execute_action(action_id=request.action_id)

    if result["status"] == "ERROR":
        raise HTTPException(status_code=400, detail=result.get("message", "Execution failed"))
    if result["status"] in ["BLOCKED", "REJECTED"]:
        raise HTTPException(status_code=409, detail=result.get("message", "Execution blocked or rejected"))

    return result
