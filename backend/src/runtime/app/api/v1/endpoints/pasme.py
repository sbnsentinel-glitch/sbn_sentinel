import os
import time

import psutil
from app.api.deps import RoleChecker  # noqa
from app.core.config import settings
from app.db.database import get_db
from app.models.rule import RuleModel
from app.models.user import User, UserRole
from app.schemas.audit import AuditLogCreate
from app.services.data_audit_engine import data_audit_engine
from fastapi import (APIRouter, Depends, HTTPException, WebSocket,
                     WebSocketDisconnect)
from jose import JWTError, jwt
from sqlalchemy.orm import Session

router = APIRouter()

# Global maintenance mode flag (in-memory for V1)
MAINTENANCE_MODE = False

# WebSocket Connection Manager for PASME Real-Time Chat


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
async def websocket_chat(websocket: WebSocket, room_id: str, token: str, db: Session = Depends(get_db)):
    """
    PASME Real-Time WebSocket for cross-browser Team Messaging.
    Secured as per F-02 requirements.
    """
    origin = websocket.headers.get("origin")
    if not origin or origin not in settings.ALLOWED_WS_ORIGINS:
        await websocket.close(code=1008)
        return

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            await websocket.close(code=1008)
            return

        user = db.query(User).filter(User.id == int(user_id)).first()
        if not user or not user.is_active:
            await websocket.close(code=1008)
            return

        from datetime import datetime
        iat = payload.get("iat")
        if iat and user.token_invalid_before:
            if datetime.fromtimestamp(iat) < user.token_invalid_before:
                await websocket.close(code=1008)
                return

        # Room authorization
        if user.role != UserRole.SYSTEM_ADMINISTRATOR.value:
            if room_id != f"org_{user.org_id}":
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

            if len(str(data)) > 2048:
                await websocket.send_json({"error": "Message too large"})
                continue

            now = time.time()
            if now - last_msg_time < 1.0:
                await websocket.send_json({"error": "Rate limit exceeded"})
                continue
            last_msg_time = now

            db.refresh(user)
            if not user.is_active or (iat and user.token_invalid_before and datetime.fromtimestamp(iat) < user.token_invalid_before):
                await websocket.close(code=1008)
                return

            data["sender_id"] = user_id
            data["role"] = user.role
            data["room_id"] = room_id

            await manager.broadcast(room_id, data)
    except WebSocketDisconnect:
        manager.disconnect(websocket, room_id)


@router.get("/health")
def get_platform_health(current_user: User = Depends(
        RoleChecker([UserRole.SYSTEM_ADMINISTRATOR.value]))):
    """
    Returns PASME Platform Health monitoring metrics.
    CP-30: Keep liveness, readiness and capability separate.
    """
    process = psutil.Process(os.getpid())
    memory_info = process.memory_info()

    from app.services.rules_engine import rules_engine
    readiness = rules_engine.validate_governance_readiness()
    rules_engine_status = "READY" if readiness.get("status") == "READY" else "NOT_READY"

    # CP-30: Storage / DMAE readiness check
    storage_status = "READY"
    try:
        from app.db.database import SessionLocal
        import sqlalchemy
        test_db = SessionLocal()
        test_db.execute(sqlalchemy.text("SELECT 1"))
        test_db.close()
    except Exception:
        storage_status = "NOT_READY"

    # CP-30: Connector readiness / capability check
    connector_status = "READY"
    from app.core.config import settings
    if getattr(settings, "ENVIRONMENT", "").upper() == "PRODUCTION" and not getattr(settings, "EHR_CLIENT_ID", ""):
        connector_status = "UNAVAILABLE"

    return {
        "status": "healthy" if not MAINTENANCE_MODE else "maintenance",
        "maintenance_mode": MAINTENANCE_MODE,
        "metrics": {
            "cpu_percent": psutil.cpu_percent(),
            "memory_usage_mb": round(memory_info.rss / 1024 / 1024, 2),
            "uptime_seconds": round(time.time() - process.create_time(), 2)
        },
        "modules": {
            "connector_engine": connector_status,
            "rules_engine": rules_engine_status,
            "context_engine": "READY",
            "dmae": storage_status,
            "siame": "READY"
        }
    }


@router.get("/rules")
def get_all_rules(db: Session = Depends(get_db), current_user: User = Depends(
        RoleChecker([UserRole.SYSTEM_ADMINISTRATOR.value]))):
    """
    Retrieve all business rules for PASME administration.
    CP-23: Authoritative projection from GovernanceRegistry.
    """
    from app.services.governance_registry import governance_registry
    gov_rules = list(governance_registry._rules.values())
    if not gov_rules:
        rules = db.query(RuleModel).all()
        return [
            {
                "id": r.id,
                "rule_id": r.rule_id,
                "version": "V1",
                "name": r.name,
                "category": r.category,
                "severity": r.severity,
                "is_active": r.is_active,
                "description": r.description
            }
            for r in rules
        ]

    return [
        {
            "id": r.rule_id,
            "rule_id": r.rule_id,
            "version": r.version,
            "name": r.rule_id,
            "category": "Operational",
            "severity": "Normal",
            "is_active": (r.lifecycle_state.value == "ACTIVE"),
            "description": r.logic_description
        }
        for r in gov_rules
    ]


@router.patch("/rules/{rule_id}/toggle")
def toggle_rule(rule_id: str, db: Session = Depends(get_db), current_user: User = Depends(
        RoleChecker([UserRole.SYSTEM_ADMINISTRATOR.value]))):
    """
    Toggle a rule's active state. Audited by DMAE.
    CP-23: Controls authoritative GovernanceRegistry RuleVersion.
    """
    from app.services.governance_registry import governance_registry, LifecycleState
    gov_rule_id = rule_id if rule_id.startswith("RULE-") else f"RULE-{rule_id}"
    gov_rule = governance_registry.get_rule_by_version(gov_rule_id, "V1")
    if not gov_rule:
        for r in governance_registry._rules.values():
            if r.rule_id == rule_id or r.rule_id == f"RULE-{rule_id}":
                gov_rule = r
                break

    if not gov_rule:
        rule = db.query(RuleModel).filter(RuleModel.rule_id == rule_id).first()
        if not rule:
            raise HTTPException(status_code=404, detail="Rule not found")
        rule.is_active = not rule.is_active
        db.commit()
        return {"rule_id": rule.rule_id, "is_active": rule.is_active}

    new_state = LifecycleState.RETIRED if gov_rule.lifecycle_state == LifecycleState.ACTIVE else LifecycleState.ACTIVE
    object.__setattr__(gov_rule, "lifecycle_state", new_state)

    # Sync to DB RuleModel if present
    rule = db.query(RuleModel).filter(RuleModel.rule_id == rule_id).first()
    if rule:
        rule.is_active = (new_state == LifecycleState.ACTIVE)
        db.commit()

    audit_data = AuditLogCreate(
        user_email=current_user.email,
        action=f"{'Enabled' if new_state == LifecycleState.ACTIVE else 'Disabled'} Governed Rule",
        resource=f"Rule: {gov_rule.rule_id}"
    )
    data_audit_engine.log_audit_event(audit_data)

    return {"rule_id": gov_rule.rule_id, "version": gov_rule.version, "is_active": (new_state == LifecycleState.ACTIVE)}


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
