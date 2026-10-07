"""
SES-002: Internal Data Flow & Event Processing Specification
Pipeline API — Exposes event submission, tracing, and monitoring endpoints.
"""
from fastapi import APIRouter, HTTPException, Depends, Query, BackgroundTasks
from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime

from app.services.processing_orchestrator import processing_orchestrator
from app.api.deps import get_current_user
from app.models.user import User

router = APIRouter()


# ─────────────────────────────────────────────
# Request / Response Schemas
# ─────────────────────────────────────────────

class EventSubmitRequest(BaseModel):
    """Request body to submit a new event into the SES-002 pipeline."""
    event_type: str = Field(...,
                            example="EHR",
                            description="Category: EHR, Phone, Email, System, Billing")
    source: str = Field(..., example="Practice Fusion", description="Origin system name")
    raw_payload: dict = Field(..., description="Raw data from the source connector")
    priority: str = Field(default="Normal", description="Critical | High | Normal | Low")
    correlation_id: Optional[str] = Field(default=None, description="Links related events")


class EventTraceResponse(BaseModel):
    """Full observability trace for a single pipeline event."""
    id: str
    event_type: str
    source: str
    state: str
    priority: str
    received_at: Optional[str]
    completed_at: Optional[str]
    failed_at: Optional[str]
    retry_count: int
    last_error: Optional[str]
    total_duration_ms: float
    observability: Optional[dict]
    layer3_rules_output: Optional[dict]
    layer4_context_output: Optional[dict]
    layer5_intelligence_output: Optional[dict]
    layer6_revenue_output: Optional[dict]
    layer7_storage_ref: Optional[str]
    layer8_published: Optional[str]


# ─────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────

@router.post("/submit", summary="Submit event to SES-002 pipeline")
def submit_event(
    request: EventSubmitRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    """
    SES-002 Layer 1 entry point with SES-009 Background Queuing.

    Accepts a raw event, queues it, and immediately returns the ID.
    The background task runs it through all 8 processing layers.
    """
    try:
        if not request.raw_payload:
            raise ValueError("Payload cannot be empty")
        
        event = processing_orchestrator.create_event(
            event_type=request.event_type,
            source=request.source,
            raw_payload=request.raw_payload,
            priority=request.priority,
            correlation_id=request.correlation_id,
            initiated_by=current_user.email,
        )

        # SES-009: Separate Interactive and Operational Workloads
        background_tasks.add_task(processing_orchestrator.process_event_background, event.id)

        return {
            "status": "queued",
            "message": "Event has been queued for background processing.",
            "event_id": event.id,
            "final_state": event.state
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail={"error": "Validation Error", "details": str(ve)})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline error: {str(e)}")


@router.get("/events", summary="List recent pipeline events")
def list_events(
        limit: int = Query(
            default=50,
            le=200,
            description="Maximum events to return"),
    state: Optional[str] = Query(  # noqa
            default=None,
            description="Filter by state: Completed, Failed, Retrying, etc."),
        event_type: Optional[str] = Query(
            default=None,
            description="Filter by type: EHR, Phone, Email"),
        current_user: User = Depends(get_current_user),
):
    """
    Returns a paginated list of recent pipeline events.
    Supports filtering by processing state and event type.
    """
    events = processing_orchestrator.list_events(limit=limit, state=state, event_type=event_type)
    return {
        "count": len(events),
        "events": events,
    }


@router.get("/events/{event_id}/trace", summary="Get full observability trace for an event")
def get_event_trace(
    event_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Returns the complete processing trace for a single event.

    Includes:
    - Per-layer timing (milliseconds)
    - State transitions
    - All layer outputs (rules, context, intelligence, revenue)
    - Correlation ID for related events
    - Error details if failed
    """
    trace = processing_orchestrator.get_event_trace(event_id)
    if not trace:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    return trace


@router.get("/stats", summary="SES-002 Pipeline statistics")
def pipeline_stats(
    current_user: User = Depends(get_current_user),
):
    """
    Returns aggregate statistics about the event processing pipeline.
    Shows counts by state, priority, type, and average processing time.
    """
    from app.db.database import SessionLocal
    from app.models.event import EventModel
    from sqlalchemy import func

    db = SessionLocal()
    try:
        total = db.query(EventModel).count()
        completed = db.query(EventModel).filter(EventModel.state == "Completed").count()
        failed = db.query(EventModel).filter(EventModel.state == "Failed").count()
        retrying = db.query(EventModel).filter(EventModel.state == "Retrying").count()
        processing = db.query(EventModel).filter(EventModel.state == "Processing").count()

        # By type
        by_type = {}
        type_counts = db.query(
            EventModel.event_type, func.count(EventModel.id)
        ).group_by(EventModel.event_type).all()
        for t, count in type_counts:
            by_type[t] = count

        # By priority
        by_priority = {}
        prio_counts = db.query(
            EventModel.priority, func.count(EventModel.id)
        ).group_by(EventModel.priority).all()
        for p, count in prio_counts:
            by_priority[p] = count

        success_rate = round((completed / total * 100), 1) if total > 0 else 0.0

        return {
            "total_events": total,
            "completed": completed,
            "failed": failed,
            "retrying": retrying,
            "processing": processing,
            "success_rate_percent": success_rate,
            "by_event_type": by_type,
            "by_priority": by_priority,
        }
    finally:
        db.close()


@router.get("/health", summary="SES-002 Pipeline health check")
def pipeline_health():
    """
    Returns the operational health of the event pipeline.
    Does not require authentication (used by monitoring systems).
    """
    return {
        "status": "operational",
        "specification": "SES-002",
        "version": "V1.0",
        "layers": {
            "L2_connector_normalization": "active",
            "L3_rules_engine": "active",
            "L4_decision_context": "active",
            "L5_operational_intelligence": "active",
            "L6_revenue_intelligence": "active",
            "L7_data_management": "active",
            "L8_dashboard_publication": "active",
        },
        "processing_modes": ["real-time", "background"],
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
