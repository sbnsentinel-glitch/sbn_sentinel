import logging
from datetime import datetime
from app.db.database import SessionLocal
from app.models.signal import SignalModel
from app.models.audit import AuditLogModel
from app.models.telemetry import TelemetryLogModel
from app.schemas.signal import SignalEvent
from app.schemas.audit import AuditLogCreate

logger = logging.getLogger(__name__)


class DataAuditEngine:
    """
    Data Management & Audit Engine (DMAE).
    Acts as Sentinel's internal system of record.
    Manages operational data lifecycle, validation, and immutable audit logs.
    """

    def save_intelligence_record(self, event: SignalEvent, db_session=None) -> bool:
        """
        Validates and securely stores an operational intelligence record (Signal)
        and generates an automatic system audit log.
        """
        db = db_session or SessionLocal()
        should_close = (db_session is None)
        try:
            db_signal = SignalModel(
                id=event.id,
                source=event.source,
                type=event.type,
                message=event.message,
                timestamp=datetime.fromisoformat(
                    event.timestamp.replace(
                        'Z',
                        '+00:00')) if isinstance(
                    event.timestamp,
                    str) else event.timestamp,
                metadata_data=event.metadata,
                risk_level=event.risk_level,
                problem=event.problem,
                reason=event.reason,
                business_impact=event.business_impact,
                recommended_action=event.recommended_action,
                expected_outcome=event.expected_outcome,
                primary_context=event.primary_context,
                secondary_context=event.secondary_context,
                context_confidence=event.context_confidence,
                context_reason=event.context_reason,
                revenue_risk_category=event.revenue_risk_category,
                estimated_financial_exposure=event.estimated_financial_exposure,
                revenue_confidence=event.revenue_confidence,
                operational_dependency=event.operational_dependency,
                org_id=getattr(event, 'org_id', None) or (event.metadata.get('org_id') if event.metadata else None),
                clinic_id=getattr(event, 'clinic_id', None) or (event.metadata.get('clinic_id') if event.metadata else None),
                explainability_log=getattr(
                    event,
                    'explainability_log',
                    None),
                priority_score=getattr(
                    event,
                    'priority_score',
                    None))
            db.add(db_signal)
            if should_close:
                db.commit()

            # Generate Immutable System Audit Log
            self._log_internal(
                db,
                user_system="system@sentinel.local",
                action=f"Generated Intelligence Record: {event.type}",
                module="DataManagementEngine",
                correlation_id=event.id
            )
            return True
        except Exception as e:
            logger.error(f"[DMAE] Failed to save intelligence record: {e}")
            if should_close:
                db.rollback()
            return False
        finally:
            if should_close:
                db.close()

    def log_audit_event(self, audit_data: AuditLogCreate) -> bool:
        """
        Public method to save an immutable audit log (e.g., from an API call for user actions).
        """
        db = SessionLocal()
        try:
            self._log_internal(
                db,
                user_system=audit_data.user_email,
                action=audit_data.action,
                module=audit_data.resource,
                correlation_id=None
            )
            return True
        except Exception as e:
            logger.error(f"[DMAE] Failed to save audit log: {e}")
            db.rollback()
            return False
        finally:
            db.close()

    def _log_internal(
            self,
            db,
            user_system: str,
            action: str,
            module: str,
            correlation_id: str = None):
        """Internal helper to insert into AuditLogModel."""
        db_audit = AuditLogModel(
            user_system=user_system,
            action=action,
            module=module,
            correlation_id=correlation_id
        )
        db.add(db_audit)
        db.commit()

    def log_failure_event(
        self,
        user_system: str,
        module: str,
        correlation_id: str,
        error_category: str,
        severity: str,
        retry_attempts: int,
        recovery_outcome: str,
        resolution_status: str,
    ) -> bool:
        """
        SES-007: Records a complete failure audit entry.
        """
        db = SessionLocal()
        try:
            db_audit = TelemetryLogModel(
                user_system=user_system,
                module=module,
                correlation_id=correlation_id,
                error_category=error_category,
                severity=severity,
                retry_attempts=retry_attempts,
                recovery_outcome=recovery_outcome,
                resolution_status=resolution_status
            )
            db.add(db_audit)
            db.commit()
            return True
        except Exception as e:
            logger.error(f"[DMAE] Failed to save failure audit log: {e}")
            db.rollback()
            return False
        finally:
            db.close()


data_audit_engine = DataAuditEngine()
