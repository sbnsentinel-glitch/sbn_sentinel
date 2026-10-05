from typing import List

from app.core.config import settings
from app.db.database import get_db
from app.models.user import User, UserRole
from app.services.data_audit_engine import data_audit_engine
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

# This expects the token in the Authorization header: `Bearer <token>`
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/login")


def get_current_user(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme)
) -> User:
    """
    Validates the JWT token and returns the current user.
    Enforces SIAME Session Management and Authentication.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.id == int(user_id)).first()
    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user account")

    iat = payload.get("iat")
    if user.token_invalid_before and iat:
        from datetime import datetime
        iat_dt = datetime.utcfromtimestamp(iat)
        invalid_before = user.token_invalid_before.replace(tzinfo=None) if user.token_invalid_before.tzinfo else user.token_invalid_before
        if iat_dt < invalid_before:
            raise credentials_exception

    return user


class RoleChecker:
    """
    Enforces Role-Based Access Control (RBAC).
    Usage: Depends(RoleChecker(["system_admin", "executive"]))
    """

    def __init__(self, allowed_roles: List[str]):
        self.allowed_roles = allowed_roles

    def __call__(self, current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in self.allowed_roles:
            # SIAME & SES-008 Audit hook: Log permission denial
            db = next(get_db())
            data_audit_engine._log_internal(
                db,
                user_system=current_user.email,
                action="PERMISSION_DENIED",
                module="Authorization",
                correlation_id=None
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required roles: {', '.join(self.allowed_roles)}"
            )
        return current_user


def get_scoped_user(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role == UserRole.SYSTEM_ADMINISTRATOR.value:
        return current_user
    if not current_user.org_id or current_user.org_id == "UNASSIGNED":
        raise HTTPException(status_code=403, detail="Missing required organization scope")
    return current_user


def verify_object_scope(db: Session, current_user: User, target_reference: str):
    if current_user.role == UserRole.SYSTEM_ADMINISTRATOR.value:
        return
    if not target_reference:
        raise HTTPException(status_code=403, detail="Cannot verify scope: missing target reference")

    from app.models.encounter import EncounterModel
    from app.models.organization import OrganizationClinicModel
    target_org_id = None

    encounter = db.query(EncounterModel).filter(EncounterModel.id == target_reference).first()
    if encounter:
        if encounter.clinic_id:
            org = db.query(OrganizationClinicModel).filter(OrganizationClinicModel.id == encounter.clinic_id).first()
            if org:
                target_org_id = org.organization_id
    else:
        org = db.query(OrganizationClinicModel).filter(OrganizationClinicModel.id == target_reference).first()
        if org:
            target_org_id = org.organization_id

    if not target_org_id:
        raise HTTPException(status_code=403, detail="Cannot resolve authoritative target ownership")
    if target_org_id != current_user.org_id:
        raise HTTPException(status_code=403, detail="Target is outside user's governed scope")
