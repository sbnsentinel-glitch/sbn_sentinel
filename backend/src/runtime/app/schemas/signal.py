from pydantic import BaseModel
from typing import Optional, Dict, Any


class SignalEvent(BaseModel):
    id: str
    source: str  # e.g. "Practice Fusion", "Twilio"
    type: str    # e.g. "EHR", "Phone", "Email"
    message: str
    timestamp: str
    metadata: Optional[Dict[str, Any]] = None
    risk_level: Optional[str] = None
    problem: Optional[str] = None
    reason: Optional[str] = None
    business_impact: Optional[str] = None
    recommended_action: Optional[str] = None
    expected_outcome: Optional[str] = None
    explainability_log: Optional[str] = None
    priority_score: Optional[int] = None
    primary_context: Optional[str] = None
    secondary_context: Optional[str] = None
    context_confidence: Optional[str] = None
    context_reason: Optional[str] = None
    revenue_risk_category: Optional[str] = None
    estimated_financial_exposure: Optional[str] = None
    revenue_confidence: Optional[str] = None
    operational_dependency: Optional[str] = None
    org_id: Optional[str] = None
    clinic_id: Optional[str] = None
