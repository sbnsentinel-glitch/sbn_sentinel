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
    def __init__(self):
        self._states: Dict[str, ConnectorRuntimeStateDTO] = {}

    def get(self, name: str) -> ConnectorRuntimeStateDTO:
        return self._states.get(name, ConnectorRuntimeStateDTO(capability_state="UNCONFIGURED", is_stale=True))

    def set(self, name: str, state: ConnectorRuntimeStateDTO):
        self._states[name] = state


connector_runtime_state = ConnectorRuntimeStateManager()
connector_manager = ConnectorManager()
