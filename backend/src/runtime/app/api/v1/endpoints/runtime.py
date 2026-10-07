from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Dict, Any
from datetime import datetime

from app.db.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.connector import ConnectorModel

router = APIRouter()


@router.get("/runtime", summary="D7 Authoritative Runtime Status")
def get_runtime_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    D7 Authoritative Runtime Status
    Communicates technical truth without converting it into operational truth.
    Connector failure, timeout, invalid response, partial response,
    degraded runtime and system unavailability remain distinct.
    """
    connectors = db.query(ConnectorModel).all()

    capabilities = []
    connector_dtos = []

    # Analyze Practice Fusion (EHR Capability)
    from app.services.connector_manager import connector_runtime_state
    pf_state = connector_runtime_state.get("PRACTICE_FUSION")

    if pf_state.capability_state == "UNCONFIGURED":
        # T01: PF absent -> UNAVAILABLE
        capabilities.append({
            "capability_id": "ehr_read",
            "label": "EHR Data Retrieval",
            "state": "UNAVAILABLE",
            "affected_scope": "EHR Data Retrieval / Practice Fusion",
            "can_continue": False,
            "retry_supported": False,
            "last_confirmed_at": None
        })
    elif pf_state.capability_state in ["CONFIGURED", "AUTH_VERIFIED", "DEGRADED", "STALE"]:
        # T02, T03: PF unhealthy or missing token -> DEGRADED/UNAVAILABLE
        state = "UNAVAILABLE" if pf_state.capability_state in ["CONFIGURED", "STALE"] else "DEGRADED"
        capabilities.append({
            "capability_id": "ehr_read",
            "label": "EHR Data Retrieval",
            "state": state,
            "affected_scope": "Practice Fusion Sync",
            "can_continue": False,
            "retry_supported": pf_state.capability_state in ["AUTH_VERIFIED", "DEGRADED"],
            "last_confirmed_at": pf_state.last_verified_at.isoformat() + "Z" if pf_state.last_verified_at else None,
            "diagnostic_ref": "CONN-PF"
        })
    else:
        capabilities.append({
            "capability_id": "ehr_read",
            "label": "EHR Data Retrieval",
            "state": "READY",
            "can_continue": True,
            "retry_supported": False,
            "last_confirmed_at": pf_state.last_verified_at.isoformat() + "Z" if pf_state.last_verified_at else None
        })

    for c in connectors:
        # D7: Do not infer TIMEOUT from generic status. Persist/return the actual failure_code.
        connector_dtos.append({
            "connector_id": c.id,
            "name": c.name,
            "state": c.status.lower() if c.status else "unknown",
            "supported_in_v1": "Practice Fusion" in c.name,
            "latency_ms": c.latency_ms,
            "last_sync": c.last_sync.isoformat() + "Z" if c.last_sync else None,
            "failure_code": getattr(c, "failure_code", None)
        })

    # Overall state
    overall_state = "READY"
    if any(c["state"] in ("UNAVAILABLE", "DEGRADED") for c in capabilities):
        overall_state = "DEGRADED"  # Or BLOCKED if it's full system block

    return {
        "overall": {
            "scope": "system",
            "state": overall_state,
            "message": "System operating normally" if overall_state == "READY" else "Degraded operation detected",
            "checked_at": datetime.utcnow().isoformat() + "Z"
        },
        "capabilities": capabilities,
        "connectors": connector_dtos,
        "technical_state": "ready"
    }
