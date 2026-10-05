from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.sql import func
from app.db.database import Base

from enum import Enum


class UserRole(str, Enum):
    SYSTEM_ADMINISTRATOR = "System Administrator"
    ORGANIZATION_ADMINISTRATOR = "Organization Administrator"
    CLINIC_MANAGER = "Clinic Manager"
    FRONT_DESK = "Front Desk"
    READ_ONLY_AUDITOR = "Read-Only Auditor"
    UNASSIGNED = "Unassigned"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, index=True)
    role = Column(String, default=UserRole.ORGANIZATION_ADMINISTRATOR.value)
    org_id = Column(String, nullable=True)  # Scope context
    is_active = Column(Boolean, default=True)
    token_invalid_before = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
