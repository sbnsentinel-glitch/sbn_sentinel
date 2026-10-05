from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from typing import List
import time
import psutil
import os

from app.db.database import get_db
from app.api.deps import RoleChecker
from app.api.deps import RoleChecker  # noqa
from app.models.user import User, UserRole
from app.models.rule import RuleModel
from app.services.data_audit_engine import data_audit_engine
from app.schemas.audit import AuditLogCreate

router = APIRouter()

# Global maintenance mode flag (in-memory for V1)
MAINTENANCE_MODE = False

# WebSocket Connection Manager for PASME Real-Time Chat


from jose import jwt, JWTError
from app.core.config import settings

class ConnectionManager:
    def __init__(self):
        self.active_connections = {}

    async def connect(self, websocket: WebSocket, room_id: str):
        await websocket.accept()
        if room_id not in self.active_connections:
            self.active_connections[room_id] = []
        self.active_connections[room_id].append(websocket)

    def disconnect(self, websocket: WebSocket, room_id: str):
        if room_id in self.active_connections and websocket in self.active_connections[room_id]:
            self.active_connections[room_id].remove(websocket)

    async def broadcast(self, room_id: str, message: dict):
        if room_id in self.active_connections:
            for connection in self.active_connections[room_id]:
                try:
                    await connection.send_json(message)
                except Exception:
                    pass

manager = ConnectionManager()


@router.websocket("/chat/ws/{room_id}")
async def websocket_chat(websocket: WebSocket, room_id: str, token: str):
    """
    PASME Real-Time WebSocket for cross-browser Team Messaging.
    Secured as per F-02 requirements.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id = payload.get("sub")
        role = payload.get("role")
        if not user_id:
            await websocket.close(code=1008)
            return
    except JWTError:
        await websocket.close(code=1008)
        return

    await manager.connect(websocket, room_id)
    last_msg_time = 0
    try:
        while True:
            data = await websocket.receive_json()
            
            # Size limit check (approximate)
            if len(str(data)) > 2048:
                await websocket.send_json({"error": "Message too large"})
                continue
                
            # Rate limit check (1 message per second)
            now = time.time()
            if now - last_msg_time < 1.0:
                await websocket.send_json({"error": "Rate limit exceeded"})
                continue
            last_msg_time = now

            # Bind sender identity to the token, not client input
            data["sender_id"] = user_id
            data["role"] = role
            data["room_id"] = room_id

            await manager.broadcast(room_id, data)
    except WebSocketDisconnect:
        manager.disconnect(websocket, room_id)


@router.get("/health")
def get_platform_health(current_user: User = Depends(
        RoleChecker([UserRole.SYSTEM_ADMINISTRATOR.value]))):
    """
    Returns PASME Platform Health monitoring metrics.
    """
    process = psutil.Process(os.getpid())
    memory_info = process.memory_info()

    return {
        "status": "healthy" if not MAINTENANCE_MODE else "maintenance",
        "maintenance_mode": MAINTENANCE_MODE,
        "metrics": {
            "cpu_percent": psutil.cpu_percent(),
            "memory_usage_mb": round(memory_info.rss / 1024 / 1024, 2),
            "uptime_seconds": round(time.time() - process.create_time(), 2)
        },
        "modules": {
            "connector_engine": "online",
            "rules_engine": "online",
            "context_engine": "online",
            "dmae": "online",
            "siame": "online"
        }
    }


@router.get("/rules")
def get_all_rules(db: Session = Depends(get_db), current_user: User = Depends(
        RoleChecker([UserRole.SYSTEM_ADMINISTRATOR.value]))):
    """
    Retrieve all business rules for PASME administration.
    """
    rules = db.query(RuleModel).all()
    if not rules:
        # Seed rules if they don't exist yet
        seed_rules(db)
        rules = db.query(RuleModel).all()

    return [
        {
            "id": r.id,
            "rule_id": r.rule_id,
            "name": r.name,
            "category": r.category,
            "severity": r.severity,
            "is_active": r.is_active,
            "description": r.description
        }
        for r in rules
    ]


@router.patch("/rules/{rule_id}/toggle")
def toggle_rule(rule_id: str, db: Session = Depends(get_db), current_user: User = Depends(
        RoleChecker([UserRole.SYSTEM_ADMINISTRATOR.value]))):
    """
    Toggle a rule's active state. Audited by DMAE.
    """
    rule = db.query(RuleModel).filter(RuleModel.rule_id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    previous_state = rule.is_active  # noqa
    rule.is_active = not rule.is_active

    # MS-012/MS-010 Audit Requirement: Administrative actions are permanently traceable
    audit_data = AuditLogCreate(
        user_email=current_user.email,
        action=f"{'Enabled' if rule.is_active else 'Disabled'} Rule",
        resource=f"Rule: {rule.rule_id}"
    )
    data_audit_engine.log_audit_event(audit_data)

    db.commit()
    return {"rule_id": rule.rule_id, "is_active": rule.is_active}


@router.post("/maintenance/toggle")
def toggle_maintenance_mode(db: Session = Depends(get_db), current_user: User = Depends(
        RoleChecker([UserRole.SYSTEM_ADMINISTRATOR.value]))):
    """
    Toggle global maintenance mode.
    """
    global MAINTENANCE_MODE
    MAINTENANCE_MODE = not MAINTENANCE_MODE

    # Audit log
    audit_data = AuditLogCreate(
        user_email=current_user.email,
        action=f"{'Enabled' if MAINTENANCE_MODE else 'Disabled'} Maintenance Mode",
        resource="Platform System"
    )
    data_audit_engine.log_audit_event(audit_data)

    return {"maintenance_mode": MAINTENANCE_MODE}


def seed_rules(db: Session):
    """Seed initial rules matching the V1 requirements if empty."""
    initial_rules = [
        RuleModel(
            rule_id="SCH-001",
            name="Patient No-Show Detected",
            category="Scheduling",
            severity="High",
            description="Patient did not arrive for scheduled appointment.",
            is_active=True),
        RuleModel(
            rule_id="SCH-002",
            name="Wait Time Threshold Exceeded",
            category="Scheduling",
            severity="Critical",
            description="Patient wait time exceeded threshold (45 mins).",
            is_active=True),
        RuleModel(
            rule_id="SCH-003",
            name="New Appointment Scheduled",
            category="Scheduling",
            severity="Information",
            description="New appointment booked in EHR.",
            is_active=True),
        RuleModel(
            rule_id="OPS-001",
            name="Front Desk Unreachable (Missed Call)",
            category="Operational Capacity",
            severity="Moderate",
            description="Patient couldn't reach the front desk.",
            is_active=True),
        RuleModel(
            rule_id="CLIN-001",
            name="Clinical Documentation Pending Review",
            category="Clinical Workflow",
            severity="Moderate",
            description="Lab results pending review.",
            is_active=True)]
    db.bulk_save_objects(initial_rules)
    db.commit()
