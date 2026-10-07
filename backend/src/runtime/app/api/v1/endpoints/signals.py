from app.models.user import UserRole
from app.api.deps import RoleChecker
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.orm import Session
import asyncio

from app.services.simulation_engine import simulation_engine
from app.db.database import get_db
from app.models.signal import SignalModel

router = APIRouter()


@router.get("")
@router.get("/")
def get_historical_signals(db: Session = Depends(get_db),
                           current_user=Depends(RoleChecker([UserRole.CLINIC_MANAGER.value,
                                                             UserRole.ORGANIZATION_ADMINISTRATOR.value,
                                                             UserRole.SYSTEM_ADMINISTRATOR.value]))):
    signals = db.query(SignalModel).order_by(SignalModel.timestamp.desc()).limit(100).all()
    result = []
    for s in signals:
        result.append({
            "id": s.id,
            "source": s.source,
            "type": s.type,
            "message": s.message,
            "timestamp": s.timestamp.isoformat() if s.timestamp else None,
            "metadata": s.metadata_data,
            "risk_level": s.risk_level,
            "problem": s.problem,
            "reason": s.reason,
            "business_impact": s.business_impact,
            "recommended_action": s.recommended_action,
            "expected_outcome": s.expected_outcome,
            "primary_context": s.primary_context,
            "secondary_context": s.secondary_context,
            "context_confidence": s.context_confidence,
            "context_reason": s.context_reason,
            "revenue_risk_category": s.revenue_risk_category,
            "estimated_financial_exposure": s.estimated_financial_exposure,
            "revenue_confidence": s.revenue_confidence,
            "operational_dependency": s.operational_dependency,
            "explainability_log": getattr(s, "explainability_log", None),
            "priority_score": getattr(s, "priority_score", None)
        })
    return result


@router.on_event("startup")
async def startup_event():
    simulation_engine.start()


@router.on_event("shutdown")
async def shutdown_event():
    simulation_engine.stop()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    origin = websocket.headers.get("origin")
    from app.core.config import settings
    if not origin or origin not in settings.ALLOWED_WS_ORIGINS:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    q = asyncio.Queue()
    simulation_engine.add_client(q)
    try:
        while True:
            # Wait for new message from the queue
            message = await q.get()
            await websocket.send_text(message)
    except WebSocketDisconnect:
        simulation_engine.remove_client(q)
