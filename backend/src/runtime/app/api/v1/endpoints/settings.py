from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.settings import SettingsModel
from app.models.user import User, UserRole
from app.api.deps import get_current_user, RoleChecker
from app.schemas.settings import SettingsUpdate, SettingsResponse
from app.core.email import send_email

router = APIRouter()


@router.get("", response_model=SettingsResponse, dependencies=[Depends(RoleChecker([UserRole.SYSTEM_ADMINISTRATOR.value, UserRole.ORGANIZATION_ADMINISTRATOR.value, UserRole.CLINIC_MANAGER.value]))])
def get_settings(db: Session = Depends(get_db)):
    """
    Retrieve clinical settings. Return default if none exists, but do not write to DB.
    """
    settings = db.query(SettingsModel).first()
    if not settings:
        raise HTTPException(status_code=404, detail="Settings not configured")
    return settings


@router.post("", response_model=SettingsResponse, dependencies=[Depends(RoleChecker([UserRole.SYSTEM_ADMINISTRATOR.value, UserRole.ORGANIZATION_ADMINISTRATOR.value, UserRole.CLINIC_MANAGER.value]))])
def update_settings(payload: SettingsUpdate, db: Session = Depends(get_db)):
    """
    Update clinical settings. Creates a default record first if none exists.
    """
    settings = db.query(SettingsModel).first()
    if not settings:
        settings = SettingsModel()
        db.add(settings)
        db.commit()
        db.refresh(settings)

    # Update only the provided fields
    update_data = payload.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(settings, key, value)

    db.commit()
    db.refresh(settings)
    return settings


@router.get("/team", dependencies=[Depends(RoleChecker([UserRole.SYSTEM_ADMINISTRATOR.value, UserRole.ORGANIZATION_ADMINISTRATOR.value, UserRole.CLINIC_MANAGER.value]))])
def get_team_members(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> List[Dict[str, Any]]:
    """Retrieve all clinic users in the same organization."""
    query = db.query(User).filter(User.role != UserRole.SYSTEM_ADMINISTRATOR.value)
    if current_user.role != UserRole.SYSTEM_ADMINISTRATOR.value:
        query = query.filter(User.org_id == current_user.org_id)
    users = query.all()
    return [
        {
            "id": str(u.id),
            "name": u.full_name or "Unknown",
            "email": u.email,
            "role": u.role,
            "status": "Active" if u.is_active else "Inactive"
        }
        for u in users
    ]


@router.post("/team/invite", dependencies=[Depends(RoleChecker([UserRole.SYSTEM_ADMINISTRATOR.value, UserRole.ORGANIZATION_ADMINISTRATOR.value, UserRole.CLINIC_MANAGER.value]))])
def invite_team_member(payload: Dict[str, Any], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Invite a new staff member to the clinic."""
    email = payload.get("email")
    name = payload.get("name", "New Staff")
    role = payload.get("role", UserRole.FRONT_DESK.value)

    if not email:
        raise HTTPException(status_code=400, detail="Email is required")

    try:
        requested_role = UserRole(role)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of {[e.value for e in UserRole]}")

    hierarchy = {
        UserRole.SYSTEM_ADMINISTRATOR.value: 100,
        UserRole.ORGANIZATION_ADMINISTRATOR.value: 80,
        UserRole.CLINIC_MANAGER.value: 60,
        UserRole.FRONT_DESK.value: 20,
        UserRole.READ_ONLY_AUDITOR.value: 10,
        UserRole.UNASSIGNED.value: 0
    }
    if hierarchy.get(requested_role.value, 0) > hierarchy.get(current_user.role, 0):
        raise HTTPException(status_code=403, detail="Cannot assign a role higher than your own")

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    target_org = current_user.org_id

    new_user = User(
        email=email,
        full_name=name,
        role=role,
        hashed_password="",
        is_active=False,
        org_id=target_org
    )
    db.add(new_user)

    import secrets
    from datetime import datetime, timedelta
    from app.models.otp import OTPModel

    recent_otp = db.query(OTPModel).filter(OTPModel.email == email, OTPModel.created_at >= datetime.utcnow() - timedelta(minutes=1)).first()
    if recent_otp:
        raise HTTPException(status_code=429, detail="Please wait 1 minute before inviting again.")

    otp = str(secrets.randbelow(900000) + 100000)
    db_otp = OTPModel(email=email, otp_code=otp, purpose="invite")
    db.add(db_otp)

    db.commit()
    db.refresh(new_user)

    # Send invitation email
    login_url = "http://localhost:3000"
    html_content = f"""
    <html>
        <body>
            <h2>Welcome to SBN Sentinel!</h2>
            <p>Hi {name},</p>
            <p>You have been invited to join the SBN Sentinel Clinic Management platform.</p>
            <p>Your activation OTP is: <strong>{otp}</strong></p>
            <p>Please go to the login screen, click "Activate Account", and enter this code to set up your password.</p>
            <br/>
            <a href="{login_url}">Click here to activate your account</a>
        </body>
    </html>
    """
    send_email(
        to_email=email,
        subject="You are invited to SBN Sentinel",
        body=html_content,
        is_html=True)

    return {
        "id": str(new_user.id),
        "name": new_user.full_name,
        "email": new_user.email,
        "role": new_user.role,
        "status": "Pending"
    }


@router.delete("/team/{user_id}", dependencies=[Depends(RoleChecker([UserRole.SYSTEM_ADMINISTRATOR.value, UserRole.ORGANIZATION_ADMINISTRATOR.value, UserRole.CLINIC_MANAGER.value]))])
def revoke_team_member(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Revoke access for a team member."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == UserRole.SYSTEM_ADMINISTRATOR.value:
        raise HTTPException(status_code=403, detail="Cannot revoke super admin")
    if current_user.role != UserRole.SYSTEM_ADMINISTRATOR.value and user.org_id != current_user.org_id:
        raise HTTPException(status_code=403, detail="Cannot revoke user outside your organization")

    hierarchy = {
        UserRole.SYSTEM_ADMINISTRATOR.value: 100,
        UserRole.ORGANIZATION_ADMINISTRATOR.value: 80,
        UserRole.CLINIC_MANAGER.value: 60,
        UserRole.FRONT_DESK.value: 20,
        UserRole.READ_ONLY_AUDITOR.value: 10,
        UserRole.UNASSIGNED.value: 0
    }
    if hierarchy.get(user.role, 0) > hierarchy.get(current_user.role, 0):
        raise HTTPException(status_code=403, detail="Cannot revoke a user with a higher role")

    from datetime import datetime
    user.is_active = False
    user.token_invalid_before = datetime.utcnow()
    db.commit()
    return {"message": "Access revoked successfully"}


@router.get("/integrations", dependencies=[Depends(RoleChecker([UserRole.SYSTEM_ADMINISTRATOR.value, UserRole.ORGANIZATION_ADMINISTRATOR.value, UserRole.CLINIC_MANAGER.value]))])
def get_integrations(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> List[Dict[str, Any]]:
    """Get clinic integrations status based on actual connector runtime state."""
    from app.services.connector_manager import connector_runtime_state

    pf_state = connector_runtime_state.get("PRACTICE_FUSION")
    return [
        {
            "id": "PRACTICE_FUSION",
            "name": "Practice Fusion EHR",
            "type": "EHR",
            "connected": pf_state.capability_state == "AUTHORIZED_READY" and not pf_state.is_stale,
            "lastSync": pf_state.last_verified_at.isoformat() + "Z" if pf_state.last_verified_at else "Never"
        }
    ]


@router.post("/integrations/{integration_id}/toggle", dependencies=[Depends(RoleChecker([UserRole.SYSTEM_ADMINISTRATOR.value, UserRole.ORGANIZATION_ADMINISTRATOR.value]))])
def toggle_integration(integration_id: str, db: Session = Depends(get_db)):
    """Toggle the connected status of an integration. Removed per F-20 (No shadow state allowed)."""
    raise HTTPException(status_code=403, detail="Integrations cannot be manually toggled. They are governed by actual connector state.")


@router.post("/send-sms-reminder")
def trigger_sms_reminder(payload: Dict[str, Any]):
    """
    Trigger automated 24-hour appointment reminder SMS via Twilio / Outreach Engine.
    """
    raise HTTPException(status_code=501, detail="Outbound SMS is disabled in V1 per security audit (F-01).")


@router.post("/send-email-report")
def trigger_email_report(payload: Dict[str, Any] = None):
    """
    Trigger automated Daily Secure Email Executive Report via Gmail SMTP Gateway.
    """
    raise HTTPException(status_code=501, detail="Outbound Email is disabled in V1 per security audit (F-01).")
