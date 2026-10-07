from sqlalchemy import Column, Integer, String, DateTime, JSON
from datetime import datetime
from app.db.database import Base


class SignalModel(Base):
    __tablename__ = "signals"
    id = Column(String, primary_key=True, index=True)
    source = Column(String, index=True)
    type = Column(String, index=True)
    message = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)
    org_id = Column(String, index=True, nullable=True)
    clinic_id = Column(String, index=True, nullable=True)
    metadata_data = Column(JSON, nullable=True)
    risk_level = Column(String, nullable=True)
    problem = Column(String, nullable=True)
    reason = Column(String, nullable=True)
    business_impact = Column(String, nullable=True)
    recommended_action = Column(String, nullable=True)
    expected_outcome = Column(String, nullable=True)
    primary_context = Column(String, nullable=True)
    secondary_context = Column(String, nullable=True)
    context_confidence = Column(String, nullable=True)
    context_reason = Column(String, nullable=True)
    revenue_risk_category = Column(String, nullable=True)
    estimated_financial_exposure = Column(String, nullable=True)
    revenue_confidence = Column(String, nullable=True)
    operational_dependency = Column(String, nullable=True)
    explainability_log = Column(String, nullable=True)
    priority_score = Column(Integer, nullable=True)
