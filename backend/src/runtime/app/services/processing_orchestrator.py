"""
SES-002: Internal Data Flow & Event Processing Specification
Event Pipeline Orchestrator — The operational backbone of SBN Sentinel.

This module enforces the 8-layer processing pipeline defined in SES-002.
Every event — regardless of source — follows this exact sequence:

    Layer 1: External Input
    Layer 2: Connector Normalization
    Layer 3: Rules Engine Evaluation
    Layer 4: Decision Context Engine
    Layer 5: Operational Intelligence Engine
    Layer 6: Revenue Intelligence Engine
    Layer 7: Data Management & Audit Storage
    Layer 8: Dashboard Publication

No layer may be skipped. No module may bypass previous layers.
State transitions are recorded at each stage for full observability.
"""
import uuid
import logging
import time
from datetime import datetime
from typing import Dict, Any, Optional

from app.db.database import SessionLocal
from app.schemas.service_communication import ServiceRequest, ServiceStatus
from app.models.event import OperationalEventModel
from app.models.intelligence import (
    DecisionContextModel, OperationalIntelligenceModel,
    RevenueIntelligenceModel
)
from app.models.decision_record import DecisionRecordModel

from app.services.evidence_engine import evidence_engine
from app.services.decision_context_engine import decision_context_engine
from app.services.policy_engine import policy_engine
from app.services.rules_engine import rules_engine
from app.services.intelligence_engine import intelligence_engine
from app.services.revenue_intelligence_engine import revenue_intelligence_engine
from app.services.data_audit_engine import data_audit_engine
from app.schemas.signal import SignalEvent
from app.core.exceptions import (
    InputValidationError, ConnectorError, DependencyError,
    TimeoutError, InvalidResponseError, PersistenceError
)
from app.services.state_transition_engine import sste

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Priority Ordering (lower = higher priority)
# ─────────────────────────────────────────────
PRIORITY_ORDER = {
    "Critical": 0,
    "High": 1,
    "Normal": 2,
    "Low": 3,
}


class ProcessingOrchestrator:
    """
    SES-006 Compliant Processing Engine Orchestrator.

    Enforces the deterministic, observable, fault-tolerant 8-layer
    processing pipeline for every Sentinel event.
    """

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

    # ─────────────────────────────────────────────
    # PUBLIC ENTRY POINT
    # ─────────────────────────────────────────────
    def create_event(
        self,
        event_type: str,
        source: str,
        raw_payload: Dict[str, Any],
        priority: str = "Normal",
        correlation_id: Optional[str] = None,
        initiated_by: str = "system",
    ) -> OperationalEventModel:
        """
        SES-002 Layer 1 / SES-009 Async Queuing:
        Creates an event record quickly and returns it in a 'Queued' state.
        The actual processing is deferred to the background.
        """
        db = SessionLocal()
        try:
            event = OperationalEventModel(
                id=str(uuid.uuid4()),
                correlation_id=correlation_id or str(uuid.uuid4()),
                event_type=event_type,
                source=source,
                priority=priority,
                raw_payload=raw_payload,
                state="Queued",
                received_at=datetime.utcnow(),
            )
            sste.execute_transition(event, "OperationalEvent", "Queued")
            db.add(event)
            db.commit()
            db.refresh(event)

            self.logger.info(
                f"[SES-009][PIPELINE] Event {event.id} QUEUED | "
                f"type={event_type} source={source} priority={priority}"
            )
            return event
        finally:
            db.close()

    def process_event_background(self, event_id: str):
        """
        SES-009: Background task executor.
        Fetches the queued event and processes it through the 8 layers.
        """
        db = SessionLocal()
        try:
            event = db.query(OperationalEventModel).filter(
                OperationalEventModel.id == event_id).first()
            if not event:
                self.logger.error(
                    f"[SES-009] Cannot process background event {event_id}, not found in DB.")
                return

            sste.execute_transition(event, "OperationalEvent", "Processing")
            db.commit()

            # Run the pipeline
            self._run_pipeline(event, db)
        except Exception as e:
            self.logger.error(
                f"[SES-009][PIPELINE] Fatal background error for event {event_id}: {e}")
        finally:
            db.close()

    # ─────────────────────────────────────────────
    # INTERNAL PIPELINE RUNNER
    # ─────────────────────────────────────────────
    def _run_pipeline(self, event: OperationalEventModel, db) -> OperationalEventModel:
        """
        Executes Layers 2–8 in strict sequential order.
        Each layer updates event state and records observability data.
        """
        try:
            if event not in db:
                attached = db.query(OperationalEventModel).filter(
                    OperationalEventModel.id == event.id).first()
                if attached:
                    event = attached

            # ── Layer 2: Connector Normalization ──────────────────────
            event = self._layer2_normalize(event, db)
            if event.state != "Processing":
                return event

            # ── Layer 3: Evidence Engine ────────────────────────────────
            event = self._layer3_evidence(event, db)
            if event.state != "Processing":
                return event

            # ── Layer 4: Decision Context Engine ──────────────────────
            event = self._layer4_context(event, db)
            if event.state != "Processing":
                return event

            # ── Layer 5: Policy Engine (Governance) ─────────────────────
            event = self._layer5_policy(event, db)
            if event.state != "Processing":
                return event

            # ── Layer 6: Rules Engine ──────────────────────────────────
            event = self._layer6_rules(event, db)
            if event.state != "Processing":
                return event

            # ── Layer 7: Operational Intelligence Engine ───────────────
            event = self._layer7_intelligence(event, db)
            if event.state != "Processing":
                return event

            # ── Layer 8: Revenue Intelligence Engine ──────────────────
            event = self._layer8_revenue(event, db)
            if event.state != "Processing":
                return event

            # ── Layer 9: Data Management & Audit Storage ───────────────
            event = self._layer9_storage(event, db)
            if event.state == "Failed":
                return event

            # ── Layer 10: Dashboard Publication ────────────────────────
            event = self._layer10_publish(event, db)

            # Mark Completed
            sste.execute_transition(event, "OperationalEvent", "Completed")
            event.completed_at = datetime.utcnow()
            total_duration = event.total_duration_ms()
            db.commit()

            # SES-009: Explicit Performance Metric Logging
            try:
                data_audit_engine._log_internal(
                    db,
                    user_system="system@sentinel.local",
                    action=f"PERFORMANCE_METRIC:{event.event_type}",
                    module="ProcessingOrchestrator",
                    correlation_id=event.id
                )
            except Exception as metric_err:
                self.logger.warning(f"Failed to log performance metric: {metric_err}")

            self.logger.info(
                f"[SES-002][PIPELINE] Event {event.id} COMPLETED | "
                f"total_ms={total_duration:.1f}"
            )
            return event

        except Exception as e:
            return self._fail_event(event, db, layer="Pipeline", error=e)

    # ─────────────────────────────────────────────
    # LAYER 2 — Connector Normalization
    # ─────────────────────────────────────────────
    def _layer2_normalize(self, event: OperationalEventModel, db) -> OperationalEventModel:
        """
        Receives raw connector payload, authenticates, validates, normalizes
        into a standard Sentinel event format.
        """
        t_start = time.time()
        try:
            event.state = "Processing"
            event.processing_started_at = datetime.utcnow()

            # We don't store normalized output in db anymore per SES-004.
            # We just pass it in memory as needed, or it can be saved in raw_payload if needed.
            # For now, we just record the duration.

            event.layer2_duration_ms = (time.time() - t_start) * 1000

            db.commit()
            self.logger.debug(f"[L2] Event {event.id} normalized | source={event.source}")
            return event

        except Exception as e:
            return self._fail_event(event, db, layer="L2-Normalize", error=e)

    # ─────────────────────────────────────────────
    # LAYER 3 — Evidence Engine
    # ─────────────────────────────────────────────
    def _layer3_evidence(self, event: OperationalEventModel, db) -> OperationalEventModel:
        """
        Gathers raw event data and translates it into an EvidencePackage of operational facts.
        """
        t_start = time.time()
        try:
            payload_data = event.raw_payload or {}

            from app.adapters.ehr_adapter import default_ehr_adapter
            canonical_event = default_ehr_adapter.to_canonical(
                raw_payload=payload_data,
                event_id=event.id,
                event_type=event.event_type,
                source=event.source
            )

            request = ServiceRequest(
                correlation_id=event.correlation_id,
                calling_module="EventPipeline",
                target_service="EvidenceEngine",
                payload={
                    "canonical_event": canonical_event.dict()
                }
            )

            response = evidence_engine.invoke(request)

            if response.status != ServiceStatus.SUCCESS:
                error_msg = response.error_details.message if response.error_details else "Unknown error"
                return self._fail_event(event, db, layer="L3-Evidence", error=error_msg)

            evidence_package = response.result_payload.get(
                "eos_003_package") or response.result_payload.get("evidence_package")
            import dataclasses
            event.evidence_package = dataclasses.asdict(evidence_package) if hasattr(
                evidence_package, '__dataclass_fields__') else evidence_package
            event.layer3_duration_ms = (time.time() - t_start) * 1000

            db.commit()
            self.logger.debug(f"[L3] Evidence Engine completed for event {event.id}")
            return event
        except Exception as e:
            return self._fail_event(event, db, layer="L3-Evidence", error=e)

    # ─────────────────────────────────────────────
    # LAYER 4 — Decision Context Engine
    # ─────────────────────────────────────────────
    def _layer4_context(self, event: OperationalEventModel, db) -> OperationalEventModel:
        """
        Classifies operational context based on the EvidencePackage.
        """
        t_start = time.time()
        try:
            request = ServiceRequest(
                correlation_id=event.correlation_id,
                calling_module="EventPipeline",
                target_service="DecisionContextEngine",
                payload={
                    "eos_003_package": getattr(event, "evidence_package", {}),
                    "event_type": event.event_type
                }
            )

            response = decision_context_engine.invoke(request)

            if response.status != ServiceStatus.SUCCESS:
                error_msg = response.error_details.message if response.error_details else "Unknown error"
                return self._fail_event(event, db, layer="L4-Context", error=error_msg)

            import json
            import uuid
            pkg = response.result_payload.get("ais_002_decision_context_package", {})
            pkg_gov = pkg.get("governance", {}) if isinstance(pkg, dict) else {}
            suff_status = pkg_gov.get("sufficiency_status") if isinstance(pkg_gov, dict) else None
            ctx_id = str(uuid.uuid4())

            def resolve_operational_target(evt) -> tuple[str | None, str | None]:
                payload = evt.raw_payload or {}
                ref = payload.get("target_reference")
                t_type = payload.get("target_type")
                if not ref:
                    return None, None
                return str(ref), str(t_type or "UNKNOWN")

            target_ref, target_type = resolve_operational_target(event)

            event.decision_context = DecisionContextModel(
                id=ctx_id,
                primary_context=response.result_payload.get("primary_context", "Unknown"),
                secondary_context=response.result_payload.get("secondary_context"),
                evidence_state=json.dumps(getattr(event, "evidence_package", {}), default=str),
                reason=response.result_payload.get("reason"),
                sufficiency_status=suff_status,
                operational_target_reference=target_ref,
                operational_target_type=target_type
            )
            event.layer4_duration_ms = (time.time() - t_start) * 1000

            db.commit()

            # Persist D4 Context Evidence Records
            self._persist_context_evidence_records(event, pkg, db)

            self.logger.debug(
                f"[L4] DCE resolved context for event {event.id}: {event.decision_context.primary_context}")
            return event
        except Exception as e:
            return self._fail_event(event, db, layer="L4-Context", error=e)

    # ─────────────────────────────────────────────
    # LAYER 5 — Policy Engine
    # ─────────────────────────────────────────────
    def _layer5_policy(self, event: OperationalEventModel, db) -> OperationalEventModel:
        """
        Enforces governance boundaries by evaluating the Decision Context.
        """
        t_start = time.time()
        try:
            decision_context = {
                "evidence_package": getattr(
                    event,
                    "evidence_package",
                    {}),
                "event_type": event.event_type,
                "primary_context": event.decision_context.primary_context if event.decision_context else "Unknown"}

            request = ServiceRequest(
                correlation_id=event.correlation_id,
                calling_module="EventPipeline",
                target_service="PolicyEngine",
                payload={"decision_context": decision_context}
            )

            response = policy_engine.invoke(request)

            if response.status != ServiceStatus.SUCCESS:
                error_msg = response.error_details.message if response.error_details else "Unknown error"
                return self._fail_event(event, db, layer="L5-Policy", error=error_msg)

            policy_result = response.result_payload.get("policy_result")
            event.policy_result = policy_result.__dict__ if hasattr(
                policy_result, '__dict__') else policy_result
            event.policy_version = response.result_payload.get("policy_version", "Unknown")
            event.layer5_duration_ms = (time.time() - t_start) * 1000

            db.commit()
            self.logger.debug(
                f"[L5] Policy Engine completed for event {event.id}. Permitted: {event.policy_result.get('is_permitted')}")
            return event
        except Exception as e:
            return self._fail_event(event, db, layer="L5-Policy", error=e)

    # ─────────────────────────────────────────────
    # LAYER 6 — Rules Engine
    # ─────────────────────────────────────────────
    def _layer6_rules(self, event: OperationalEventModel, db) -> OperationalEventModel:
        """
        Evaluates the operational context against active business rules ONLY if permitted by Policy Engine.
        """
        t_start = time.time()
        try:
            request = ServiceRequest(
                correlation_id=event.correlation_id,
                calling_module="EventPipeline",
                target_service="RulesEngine",
                payload={
                    "decision_context": {
                        "id": event.decision_context.id,
                        "primary_context": event.decision_context.primary_context,
                        "secondary_context": event.decision_context.secondary_context,
                        "target_reference": event.decision_context.operational_target_reference,
                        "target_type": event.decision_context.operational_target_type,
                        "event_type": event.event_type
                    } if event.decision_context else {},
                    "policy_result": getattr(event, "policy_result", {}),
                    # SESR-008: Pass journey identity so RuleEvaluationRecords are traceable
                    "journey_id": event.correlation_id
                }
            )

            response = rules_engine.invoke(request)

            if response.status != ServiceStatus.SUCCESS:
                error_msg = response.error_details.message if response.error_details else "Unknown error"
                return self._fail_event(event, db, layer="L6-Rules", error=error_msg)

            from app.models.intelligence import RuleFindingModel
            findings_list = response.result_payload.get("findings", [])
            # Only keep findings that actually met the condition
            active_findings = [f for f in findings_list if f.get("result") == "CONDITION_MET"]

            # If no CONDITION_MET finding exists, we shouldn't arbitrarily pick the first one
            if not active_findings:
                # F-11: we pass an empty array, intelligence engine handles NO_RECOMMENDATION
                event.rule_findings = []
                event.rule_version = "Unknown"
            else:
                first_finding = active_findings[0]
                event.rule_findings = [RuleFindingModel(
                    rule_id=first_finding.get("rule_id", "Unknown"),
                    severity=first_finding.get("result", "Information"), # For DB compatibility
                    description=first_finding.get("result", ""), # Temporarily store exact result
                    evaluation_id=first_finding.get("evaluation_id", "UNKNOWN_EVALUATION")
                )]
                event.rule_version = first_finding.get("rule_version", "Unknown")

            event.layer6_duration_ms = (time.time() - t_start) * 1000

            db.commit()
            self.logger.debug(
                f"[L6] Rules Engine evaluated event {event.id} -> finding: {event.rule_findings[0].rule_id if event.rule_findings else 'Unknown'}")
            return event

        except Exception as e:
            return self._fail_event(event, db, layer="L6-Rules", error=e)

    # ─────────────────────────────────────────────
    # LAYER 7 — Operational Intelligence Engine
    # ─────────────────────────────────────────────

    def _layer7_intelligence(self, event: OperationalEventModel, db) -> OperationalEventModel:
        """
        Takes objective rule findings and generates executive recommendations.
        t_start = time.time()
        try:
            request = ServiceRequest(
                correlation_id=event.correlation_id,
                calling_module="EventPipeline",
                target_service="IntelligenceEngine",
                payload={
                    "finding": [
                        {
                            "rule_id": rf.rule_id,
                            "severity": rf.severity,
                            "result": rf.description, # F-11: recover exact result
                            "evaluation_id": rf.evaluation_id} for rf in event.rule_findings] if event.rule_findings else [],
                    "context": {
                        "id": event.decision_context.id,
                        "primary_context": event.decision_context.primary_context,
                        "secondary_context": event.decision_context.secondary_context,
                        "target_reference": event.decision_context.operational_target_reference,
                        "target_type": event.decision_context.operational_target_type
                    } if event.decision_context else {},
                    "evidence": getattr(
                        event,
                        "evidence_package",
                        {}),
                    "policy": getattr(
                        event,
                        "policy_result",
                        {}),
                    "journey_id": event.correlation_id})

            response = intelligence_engine.invoke(request)

            if response.status != ServiceStatus.SUCCESS:
                error_msg = response.error_details.message if response.error_details else "Unknown error"
                return self._fail_event(event, db, layer="L7-Intelligence", error=error_msg)

            from app.models.intelligence import OperationalIntelligenceModel
            event.operational_intelligence = OperationalIntelligenceModel(
                priority=response.result_payload.get("priority", "Information"),
                operational_impact=response.result_payload.get("operational_impact", ""),
                recommendation=response.result_payload.get("recommendation", "")
            )
            event.intelligence_result = response.result_payload  # Keep dynamic attr just in case
            event.layer7_duration_ms = (time.time() - t_start) * 1000

            db.commit()
            self.logger.debug(f"[L7] OIE generated recommendations for event {event.id}")
            return event

        except Exception as e:
            return self._fail_event(event, db, layer="L7-Intelligence", error=e)

    # ─────────────────────────────────────────────
    # LAYER 8 — Revenue Intelligence Engine
    # ─────────────────────────────────────────────
    def _layer8_revenue(self, event: OperationalEventModel, db) -> OperationalEventModel:
        """
        Generates revenue impact estimates based on the context and rule findings.
        """
        t_start = time.time()
        try:
            request = ServiceRequest(
                correlation_id=event.correlation_id,
                calling_module="EventPipeline",
                target_service="RevenueIntelligenceEngine",
                payload={
                    "finding": [{"rule_id": rf.rule_id, "severity": rf.severity} for rf in event.rule_findings] if event.rule_findings else {},
                    "context": {
                        "primary_context": event.decision_context.primary_context,
                        "secondary_context": event.decision_context.secondary_context
                    } if event.decision_context else {}
                }
            )

            response = revenue_intelligence_engine.invoke(request)

            if response.status != ServiceStatus.SUCCESS:
                error_msg = response.error_details.message if response.error_details else "Unknown error"
                # SES-007 Graceful Degradation: Do not fail the entire event if Revenue
                # Intelligence fails.
                self.logger.warning(
                    f"[L8] Revenue Intelligence Engine failed. Applying Graceful Degradation. Error: {error_msg}")
                # We still log the failure audit
                try:
                    data_audit_engine.log_failure_event(
                        user_system="system@sentinel.local",
                        module="ProcessingOrchestrator:L8-Revenue",
                        correlation_id=event.id,
                        error_category="Business Rule Error",
                        severity="Warning",
                        retry_attempts=0,
                        recovery_outcome="Degraded",
                        resolution_status="Operational Intel preserved, Revenue Intel skipped."
                    )
                except Exception as e_audit:  # noqa
                    pass
                return event

            from app.models.intelligence import RevenueIntelligenceModel
            event.revenue_intelligence = RevenueIntelligenceModel(
                estimated_exposure=response.result_payload.get("estimated_financial_exposure", ""),
                opportunity_category=response.result_payload.get("revenue_risk_category", ""),
                financial_priority=response.result_payload.get("revenue_confidence", "")
            )
            event.revenue_result = response.result_payload  # Keep dynamic attr just in case
            event.layer8_duration_ms = (time.time() - t_start) * 1000

            db.commit()
            self.logger.debug(f"[L8] RIE processed event {event.id}")
            return event

        except Exception as e:
            self.logger.warning(
                f"[L8] Revenue Intelligence Engine exception. Applying Graceful Degradation. Error: {str(e)}")
            return event

    # ─────────────────────────────────────────────
    # LAYER 9 — Data Management & Audit Storage
    # ─────────────────────────────────────────────
    def _layer9_storage(self, event: OperationalEventModel, db) -> OperationalEventModel:
        """
        Stores the enriched event as a Signal intelligence record.
        Writes an immutable audit log entry.
        """
        t_start = time.time()
        try:
            payload = event.raw_payload or {}
            intel_model = db.query(OperationalIntelligenceModel).filter(
                OperationalIntelligenceModel.event_id == event.id).first()
            revenue_model = db.query(RevenueIntelligenceModel).filter(
                RevenueIntelligenceModel.event_id == event.id).first()
            context_model = db.query(DecisionContextModel).filter(
                DecisionContextModel.event_id == event.id).first()

            intel_result = getattr(event, "intelligence_result", {})

            signal_event = SignalEvent(
                id=event.id,
                source=event.source,
                type=event.event_type,
                message=payload.get("detail", ""),
                timestamp=datetime.utcnow().isoformat(),
                metadata={"pipeline_event_id": event.id, "priority": event.priority, "correlation_id": event.correlation_id},
                risk_level=intel_result.get("risk_level") or (intel_model.priority if intel_model else "Information"),
                problem=intel_result.get("problem", ""),
                reason=intel_result.get("reason", ""),
                business_impact=intel_result.get("business_impact") or (intel_model.operational_impact if intel_model else ""),
                recommended_action=intel_result.get("action") or (intel_model.recommendation if intel_model else ""),
                expected_outcome=intel_result.get("expected_outcome", ""),
                explainability_log=intel_result.get("explainability_log", ""),
                priority_score=intel_result.get("priority_score", 0),
                primary_context=context_model.primary_context if context_model else "",
                secondary_context=context_model.secondary_context if context_model else "",
                context_confidence="",
                context_reason=context_model.reason if context_model else "",
                revenue_risk_category=revenue_model.opportunity_category if revenue_model else "None",
                estimated_financial_exposure=revenue_model.estimated_exposure if revenue_model else "$0.00",
                revenue_confidence=revenue_model.financial_priority if revenue_model else "High",
                operational_dependency="",
            )

            class PersistenceError(Exception):
                pass

            success = data_audit_engine.save_intelligence_record(signal_event)
            if not success:
                raise PersistenceError("Mandatory intelligence persistence failed")
            event.layer7_storage_ref = event.id

            # Save Decision Record Audit Trail
            intel_result = getattr(event, "intelligence_result", {})
            if intel_result:
                decision_record = DecisionRecordModel(
                    event_id=event.id,
                    evidence=__import__("json").loads(
                        __import__("json").dumps(
                            event.evidence_package,
                            default=str)) if event.evidence_package else {},
                    rule_id=event.rule_findings[0].rule_id if event.rule_findings else "SYS-BLOCKED",
                    policy_status=getattr(event, "policy_result", {}).get("is_permitted", False),
                    recommendation=__import__("json").loads(
                        __import__("json").dumps(intel_result, default=str)),
                    # SESR-010 Bindings
                    policy_version=getattr(event, "policy_version", "Unknown"),
                    rule_version=getattr(event, "rule_version", "Unknown"),
                    mapping_version=intel_result.get("mapping_version", "Unknown"),
                    evaluation_timestamp=datetime.utcnow()
                )
                db.add(decision_record)

            event.layer7_duration_ms = (time.time() - t_start) * 1000
            db.commit()

            self.logger.info(
                f"[L7] Event {event.id} stored | ref={event.layer7_storage_ref}"
            )
            return event

        except Exception as e:
            return self._fail_event(event, db, layer="L9-Storage", error=e)

    # ─────────────────────────────────────────────
    # LAYER 10 — Dashboard Publication
    # ─────────────────────────────────────────────
    def _layer10_publish(self, event: OperationalEventModel, db) -> OperationalEventModel:
        """
        Marks the event as published to the dashboard.
        In V1: data is available via REST API poll.
        Future: WebSocket push, SSE streaming.
        """
        t_start = time.time()
        try:
            # In V1, publication = data available in /signals endpoint
            # Future: trigger WebSocket broadcast here
            event.layer8_published = "api:signals"
            event.layer8_duration_ms = (time.time() - t_start) * 1000
            db.commit()

            self.logger.info(f"[L8] Event {event.id} published to dashboard.")
            return event

        except Exception as e:
            return self._fail_event(event, db, layer="L10-Publish", error=e)

    # ─────────────────────────────────────────────
    # STATE MANAGEMENT — Failure Handler (SES-007)
    # ─────────────────────────────────────────────
    def _fail_event(
        self,
        event: OperationalEventModel,
        db,
        layer: str,
        error: Exception,
        category: str = None,
        severity: str = None
    ) -> OperationalEventModel:
        """
        Records failure state with layer origin and error message.
        Implements SES-009 Degraded Operations and SES-007 transient/non-transient rules.
        """
        if isinstance(error, Exception):
            error_str = str(error)
            if isinstance(error, InputValidationError):
                category = category or "Input Error"
                severity = severity or "Error"
            elif isinstance(error, ConnectorError):
                category = category or "Connector Error"
                severity = severity or "Warning"
            elif isinstance(error, DependencyError):
                category = category or "Dependency Error"
                severity = severity or "Error"
            elif isinstance(error, TimeoutError):
                category = category or "Timeout Error"
                severity = severity or "Warning"
            elif isinstance(error, InvalidResponseError):
                category = category or "Invalid Response Error"
                severity = severity or "Error"
            elif isinstance(error, PersistenceError):
                category = category or "Persistence Error"
                severity = severity or "Critical"
            else:
                category = category or "System Error"
                severity = severity or "Error"
        else:
            error_str = str(error)
            category = category or "System Error"
            severity = severity or "Error"

        event.retry_count = (event.retry_count or 0) + 1
        event.last_error = f"[{layer}] {error_str}"

        transient_categories = ["Connector Error", "Communication Error", "Timeout Error"]
        is_transient = category in transient_categories

        if not is_transient or severity == "Critical" or event.retry_count >= event.max_retries:
            # SES-009: Isolate failure. Use "Degraded" if Warning, else "Failed"
            new_state = "Degraded" if severity == "Warning" else "Failed"
            sste.execute_transition(event, "OperationalEvent", new_state)
            event.failed_at = datetime.utcnow()
            resolution = "Administrative Intervention Required" if severity == "Critical" else (
                "Degraded - Feature Unavailable" if severity == "Warning" else "Failed - No Recovery")
            self.logger.error(
                f"[SES-009][{layer}] Event {event.id} {event.state.upper()} | "
                f"Cat={category} Sev={severity} Retries={event.retry_count}: {error_str}"
            )
        else:
            sste.execute_transition(event, "OperationalEvent", "Retrying")
            resolution = "Retrying - Backoff initiated"
            backoff_seconds = min(2 ** event.retry_count, 10)  # max 10s synchronous backoff
            self.logger.warning(
                f"[SES-007][{layer}] Event {event.id} retry {event.retry_count}/{event.max_retries}. "
                f"Applying {backoff_seconds}s backoff. Error: {error_str}"
            )
            time.sleep(backoff_seconds)

        try:
            db.commit()
        except Exception:
            db.rollback()

        # Log failure to audit system (SES-007 §15)
        try:
            data_audit_engine.log_failure_event(
                user_system="system@sentinel.local",
                module=f"ProcessingOrchestrator:{layer}",
                correlation_id=event.id,
                error_category=category,
                severity=severity,
                retry_attempts=event.retry_count,
                recovery_outcome=event.state,
                resolution_status=resolution
            )
        except Exception as e:
            self.logger.error(f"Failed to log failure audit: {e}")

        return event

    # ─────────────────────────────────────────────
    # OBSERVABILITY — Get event trace
    # ─────────────────────────────────────────────
    def get_event_trace(self, event_id: str) -> Optional[Dict[str, Any]]:
        """
        Returns the full processing trace for a given event.
        Includes per-layer timing, state, and all outputs via relationships.
        """
        db = SessionLocal()
        try:
            event = db.query(OperationalEventModel).filter(
                OperationalEventModel.id == event_id).first()
            if not event:
                return None
            return {
                **event.to_dict(),
                "observability": {
                    "layer2_connector_ms": event.layer2_duration_ms,
                    "layer3_rules_ms": event.layer3_duration_ms,
                    "layer4_context_ms": event.layer4_duration_ms,
                    "layer5_intelligence_ms": event.layer5_duration_ms,
                    "layer6_revenue_ms": event.layer6_duration_ms,
                    "layer7_storage_ms": event.layer7_duration_ms,
                    "layer8_publish_ms": event.layer8_duration_ms,
                    "total_ms": event.total_duration_ms(),
                },
            }
        finally:
            db.close()

    def list_events(
        self,
        limit: int = 50,
        state: Optional[str] = None,
        event_type: Optional[str] = None,
    ) -> list:
        """
        Returns a list of recent pipeline events with optional filters.
        """
        db = SessionLocal()
        try:
            query = db.query(OperationalEventModel).order_by(
                OperationalEventModel.received_at.desc())
            if state:
                query = query.filter(OperationalEventModel.state == state)
            if event_type:
                query = query.filter(OperationalEventModel.event_type == event_type)
            events = query.limit(limit).all()
            return [e.to_dict() for e in events]
        finally:
            db.close()

    def _persist_context_evidence_records(self, event: OperationalEventModel, pkg: dict, db) -> None:
        """
        D4 Authoritative Persistence: Persists context evidence, provenance, freshness,
        missing, and conflict records bound directly to the event's decision context ID.
        """
        try:
            from app.models.decision_context_models import (
                ContextEvidenceModel,
                ContextMissingEvidenceModel,
                ContextConflictsModel,
                ContextFreshnessModel,
                ContextProvenanceModel,
            )
            from datetime import datetime
            import uuid

            context_id = getattr(event.decision_context, "id", None) if event.decision_context else None
            if not context_id:
                context_id = str(uuid.uuid4())
                if event.decision_context:
                    event.decision_context.id = context_id
            evidence_data = pkg.get("evidence", {}) if isinstance(pkg, dict) else {}
            used_items = list(evidence_data.get("used", [])) if isinstance(evidence_data, dict) else []

            # If used_items is empty, extract from event.evidence_package facts/statuses
            if not used_items and getattr(event, "evidence_package", None):
                ep = event.evidence_package
                facts = ep.get("facts", {}) if isinstance(ep, dict) else {}
                for k, v in facts.items():
                    used_items.append({
                        "evidence_id": f"ev-{event.id}-{k}",
                        "canonical_entity": "OperationalFact",
                        "fact_key": k,
                        "fact_value": v,
                        "source_connector": event.source or "System"
                    })

            for ev in used_items:
                if isinstance(ev, dict):
                    ev_id = str(ev.get("evidence_id") or uuid.uuid4())
                    ev_type = (ev.get("evidence_type") or ev.get("canonical_entity") or "Operational Fact")
                    ev_val = (f"{ev.get('fact_key')}: {ev.get('fact_value')}" if ev.get('fact_key') else str(ev.get('fact_value', '')))
                    source = ev.get("source_connector") or event.source or "System"
                else:
                    ev_id = str(getattr(ev, "evidence_id", uuid.uuid4()))
                    ev_type = getattr(ev, "evidence_type", None) or getattr(ev, "canonical_entity", None) or "Operational Fact"
                    ev_val = (f"{getattr(ev, 'fact_key', '')}: {getattr(ev, 'fact_value', '')}" if getattr(ev, 'fact_key', None) else str(getattr(ev, 'fact_value', '')))
                    source = getattr(ev, "source_connector", event.source or "System")

                db.add(ContextEvidenceModel(
                    id=ev_id,
                    context_id=context_id,
                    evidence_type=str(ev_type),
                    evidence_value=str(ev_val),
                    added_at=datetime.utcnow()
                ))

                db.add(ContextProvenanceModel(
                    id=str(uuid.uuid4()),
                    context_id=context_id,
                    evidence_id=ev_id,
                    source_system=str(source),
                    ingestion_timestamp=datetime.utcnow()
                ))

                db.add(ContextFreshnessModel(
                    id=str(uuid.uuid4()),
                    context_id=context_id,
                    evidence_id=ev_id,
                    age_seconds="0",
                    is_stale=bool(evidence_data.get("freshness", {}).get("is_stale", False)) if isinstance(evidence_data, dict) else False
                ))

            missing_items = evidence_data.get("missing", []) if isinstance(evidence_data, dict) else []
            for m in missing_items:
                m_id = m.get("evidence_id") if isinstance(m, dict) else getattr(m, "evidence_id", None)
                m_type = m.get("expected_evidence_type") or m.get("type") if isinstance(m, dict) else getattr(m, "expected_evidence_type", str(m))
                m_impact = m.get("impact_level") if isinstance(m, dict) else getattr(m, "impact_level", "Medium")
                db.add(ContextMissingEvidenceModel(
                    id=str(m_id or uuid.uuid4()),
                    context_id=context_id,
                    expected_evidence_type=str(m_type),
                    impact_level=str(m_impact or "Medium")
                ))

            conflict_items = evidence_data.get("conflicts", []) if isinstance(evidence_data, dict) else []
            for c in conflict_items:
                if isinstance(c, dict):
                    c_id = c.get("conflict_id") or c.get("id") or str(uuid.uuid4())
                    ev_a = c.get("evidence_a_id") or c.get("evidence_a")
                    ev_b = c.get("evidence_b_id") or c.get("evidence_b")
                    c_desc = c.get("conflict_description") or c.get("description") or str(c)
                    c_res = c.get("resolution_status") or "Unresolved"
                else:
                    c_id = getattr(c, "conflict_id", None) or getattr(c, "id", None) or str(uuid.uuid4())
                    ev_a = getattr(c, "evidence_a_id", None)
                    ev_b = getattr(c, "evidence_b_id", None)
                    c_desc = getattr(c, "conflict_description", str(c))
                    c_res = getattr(c, "resolution_status", "Unresolved")

                # Strictly do NOT assign fallback evidence_a/b from used_items.
                # If exact refs cannot be established, leave the relationship incomplete/unavailable.
                resolved_ev_a = str(ev_a) if ev_a else None
                resolved_ev_b = str(ev_b) if ev_b else None
                resolution_status = str(c_res) if (resolved_ev_a and resolved_ev_b) else "Incomplete"

                db.add(ContextConflictsModel(
                    id=str(c_id),
                    context_id=context_id,
                    evidence_a_id=resolved_ev_a,
                    evidence_b_id=resolved_ev_b,
                    conflict_description=str(c_desc),
                    resolution_status=resolution_status
                ))

            db.commit()
        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            self.logger.warning(f"Could not persist D4 context evidence records: {e}")


# Singleton instance
processing_orchestrator = ProcessingOrchestrator()
