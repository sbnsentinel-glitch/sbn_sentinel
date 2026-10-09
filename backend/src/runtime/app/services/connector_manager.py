from pydantic import BaseModel
from typing import Literal, Optional, Dict
import logging
from typing import Any
from datetime import datetime

from app.db.database import SessionLocal
from app.models.connector import ConnectorModel
# Removed static import of PracticeFusionConnector
from app.services.state_transition_engine import sste

logger = logging.getLogger(__name__)


class ConnectorManager:
    """
    SES-005 Connector Engineering Framework
    Manages connector lifecycle, health monitoring, and sync execution.
    """

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        # Register available connector classes
        # We no longer hard-code the Practice Fusion registry here.
        # It is managed by app.integrations.core.registry
        self._connector_registry: Dict[str, Any] = {}

    async def sync_connector(self, connector_id: str) -> Dict[str, Any]:
        """
        Executes a synchronization cycle for a specific connector.
        Enforces SES-005 lifecycle state transitions.
        """
        db = SessionLocal()
        try:
            db_connector = db.query(ConnectorModel).filter(
                ConnectorModel.id == connector_id).first()
            if not db_connector:
                return {"status": "Failed", "error": "Connector not found"}

            # Enforce dynamic loading via IntegrationRegistry
            from app.integrations.core.registry import registry

            config = db_connector.config or {}
            config["id"] = db_connector.id

            # Use SecretProvider or secure vault in real implementation
            # For now, rely entirely on the secure config JSON for credentials
            # Do not overload access_token as private_key!

            adapter = registry.create(
                vendor_id=db_connector.name,
                config=config
            )

            if not adapter:
                # D7: Do not simulate success for unsupported connectors.
                sste.execute_transition(db_connector, "Connector", "Warning")
                db_connector.failure_code = "UNSUPPORTED"
                db.commit()
                return {"status": "Failed", "error": f"Connector type {db_connector.name} is not fully supported in V1", "code": "UNAVAILABLE"}

            # State Transition: Synchronizing
            sste.execute_transition(db_connector, "Connector", "Synchronizing")
            db.commit()

            result = await adapter.sync()

            # Process result and update lifecycle state
            if result.get("status") == "Success":
                sste.execute_transition(db_connector, "Connector", "Healthy")
                db_connector.last_sync = datetime.utcnow()
                db_connector.latency_ms = int(result.get("duration_ms", 50))
                db_connector.failure_code = None
            else:
                sste.execute_transition(db_connector, "Connector", "Warning")  # Transient failure state
                db_connector.failure_code = result.get("failure_code", "UNKNOWN")

            db.commit()
            return result

        except Exception as e:
            self.logger.error(f"Sync failed for connector {connector_id}: {e}")
            if 'db_connector' in locals() and db_connector:
                sste.execute_transition(db_connector, "Connector", "Warning")
                db_connector.failure_code = getattr(e, "failure_code", "UNKNOWN")
                db.commit()
            return {"status": "Failed", "error": str(e)}
        finally:
            db.close()

    def is_ready(self, connector_name: str = "PRACTICE_FUSION") -> bool:
        """SES-005 / SESR-011: Verify operational readiness for a specific connector.
        Fail-closed: returns False if the connector record is absent, unhealthy,
        or missing credentials.
        """
        state = connector_runtime_state.get(connector_name)
        return state.capability_state == "AUTHORIZED_READY" and not state.is_stale


class ConnectorRuntimeStateDTO(BaseModel):
    capability_state: Literal["CONFIGURED", "AUTH_VERIFIED", "AUTHORIZED_READY", "DEGRADED", "STALE", "UNCONFIGURED"]
    is_stale: bool
    last_verified_at: Optional[datetime] = None


class ConnectorRuntimeStateManager:
    """
    Authoritative owner of connector operational readiness lifecycle:
    UNCONFIGURED -> CONFIGURED -> AUTH_VERIFIED -> AUTHORIZED_READY -> DEGRADED / STALE.
    Persists state to database and deterministically reconstructs on restart
    to guarantee no false readiness occurs.
    """
    def __init__(self):
        self._states: Dict[str, ConnectorRuntimeStateDTO] = {}

    def get(self, name: str) -> ConnectorRuntimeStateDTO:
        search_term = name.replace("_", " ")
        # 1. Check memory cache first
        cached = self._states.get(name) or self._states.get(search_term)
        if cached:
            # Verify TTL staleness
            if cached.last_verified_at:
                age_seconds = (datetime.utcnow() - cached.last_verified_at).total_seconds()
                if age_seconds > 3600:
                    cached = ConnectorRuntimeStateDTO(
                        capability_state="STALE",
                        is_stale=True,
                        last_verified_at=cached.last_verified_at,
                    )
                    self._states[name] = cached
                    self._states[search_term] = cached
            return cached

        # 2. Reconstruct deterministically from database on restart / cache-miss
        try:
            db = SessionLocal()
            try:
                conn = (
                    db.query(ConnectorModel)
                    .filter(
                        (ConnectorModel.name.ilike(f"%{name}%"))
                        | (ConnectorModel.name.ilike(f"%{search_term}%"))
                        | (ConnectorModel.id == name)
                    )
                    .first()
                )
                if not conn:
                    return ConnectorRuntimeStateDTO(capability_state="UNCONFIGURED", is_stale=True)

                persisted = (conn.config or {}).get("readiness_state")
                if persisted:
                    last_ver = None
                    if persisted.get("last_verified_at"):
                        try:
                            last_ver = datetime.fromisoformat(persisted["last_verified_at"])
                        except Exception:
                            pass
                    is_stale = persisted.get("is_stale", True)
                    cap_state = persisted.get("capability_state", "CONFIGURED")

                    if last_ver and (datetime.utcnow() - last_ver).total_seconds() > 3600:
                        cap_state = "STALE"
                        is_stale = True

                    if getattr(conn, "failure_code", None) or conn.status in ("Warning", "Disconnected", "Error"):
                        cap_state = "DEGRADED"
                        is_stale = True

                    dto = ConnectorRuntimeStateDTO(
                        capability_state=cap_state,
                        is_stale=is_stale,
                        last_verified_at=last_ver,
                    )
                    self._states[name] = dto
                    self._states[search_term] = dto
                    return dto

                # If connector exists but never authenticated/verified
                dto = ConnectorRuntimeStateDTO(capability_state="CONFIGURED", is_stale=True)
                self._states[name] = dto
                self._states[search_term] = dto
                return dto
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Failed to reconstruct connector runtime state for {name}: {e}")
            return ConnectorRuntimeStateDTO(capability_state="UNCONFIGURED", is_stale=True)

    def set(self, name: str, state: ConnectorRuntimeStateDTO):
        search_term = name.replace("_", " ")
        self._states[name] = state
        self._states[search_term] = state
        try:
            db = SessionLocal()
            try:
                conn = (
                    db.query(ConnectorModel)
                    .filter(
                        (ConnectorModel.name.ilike(f"%{name}%"))
                        | (ConnectorModel.name.ilike(f"%{search_term}%"))
                        | (ConnectorModel.id == name)
                    )
                    .first()
                )
                if conn:
                    cfg = dict(conn.config or {})
                    cfg["readiness_state"] = {
                        "capability_state": state.capability_state,
                        "is_stale": state.is_stale,
                        "last_verified_at": state.last_verified_at.isoformat() if state.last_verified_at else None,
                    }
                    conn.config = cfg
                    db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Could not persist connector runtime state for {name}: {e}")

    async def verify_readiness(self, connector_name: str, adapter) -> ConnectorRuntimeStateDTO:
        """
        Executes actual auth and capability verification before transitioning to AUTHORIZED_READY.
        """
        try:
            if not adapter:
                state = ConnectorRuntimeStateDTO(capability_state="CONFIGURED", is_stale=True)
                self.set(connector_name, state)
                return state

            # Step 1: Real Auth Verification
            token_lease = await adapter.auth.authenticate() if hasattr(adapter, "auth") and adapter.auth else None
            if not token_lease or not getattr(token_lease, "access_token", None):
                state = ConnectorRuntimeStateDTO(capability_state="DEGRADED", is_stale=True)
                self.set(connector_name, state)
                return state

            # Step 2: Real Capability Verification
            caps = await adapter.get_capability_statement()
            if not caps or not caps.supports_search("Patient"):
                state = ConnectorRuntimeStateDTO(capability_state="AUTH_VERIFIED", is_stale=True)
                self.set(connector_name, state)
                return state

            # Fully Verified
            state = ConnectorRuntimeStateDTO(
                capability_state="AUTHORIZED_READY",
                is_stale=False,
                last_verified_at=datetime.utcnow(),
            )
            self.set(connector_name, state)
            return state
        except Exception as e:
            logger.error(f"Readiness verification failed for {connector_name}: {e}")
            state = ConnectorRuntimeStateDTO(capability_state="DEGRADED", is_stale=True)
            self.set(connector_name, state)
            return state


connector_runtime_state = ConnectorRuntimeStateManager()
connector_manager = ConnectorManager()
