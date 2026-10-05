from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.settings import SettingsModel
from app.models.user import User, UserRole
from app.api.deps import get_current_user, RoleChecker
from app.models.integration import IntegrationModel
from app.schemas.settings import SettingsUpdate, SettingsResponse
from app.core.email import send_email

router = APIRouter()


@router.get("", response_model=SettingsResponse)
def get_settings(db: Session = Depends(get_db)):
    """
    Retrieve clinical settings. If none exist in the database,
    initialize and return default settings.
    """
    settings = db.query(SettingsModel).first()
    if not settings:
        settings = SettingsModel(
            practice_name="Sentinel Health Urgent Care",
            practice_phone="(555) 019-2834",
            timezone="Eastern Time (US & Canada)",
            open_time="08:00",
            close_time="20:00",
            language="en",
            theme_mode="system",
            scheduling_aggressiveness=2,
            auto_outreach=True,
            confidence_threshold="85% (Recommended)",
            ai_model="gpt-4o",
            notify_sms=True,
            notify_email=False,
            notify_desktop=True,
            notify_copay=True,
            reminder_interval="24h",
            active_plan="professional",
            payment_card="Visa ending in 4242"
        )
        db.add(settings)
        db.commit()
        db.refresh(settings)
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


@router.get("/team")
def get_team_members(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    """Retrieve all clinic users (except super admins)."""
    users = db.query(User).filter(User.role != UserRole.SYSTEM_ADMINISTRATOR.value).all()
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

    new_user = User(
        email=email,
        full_name=name,
        role=role,
        hashed_password="",
        is_active=False
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


@router.get("/integrations")
def get_integrations(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    """Get clinic integrations status."""
    integrations = db.query(IntegrationModel).all()

    return [
        {
            "id": i.id,
            "name": i.name,
            "type": i.type,
            "connected": i.connected,
            "lastSync": i.lastSync
        }
        for i in integrations
    ]


@router.post("/integrations/{integration_id}/toggle", dependencies=[Depends(RoleChecker([UserRole.SYSTEM_ADMINISTRATOR.value, UserRole.ORGANIZATION_ADMINISTRATOR.value]))])
def toggle_integration(integration_id: str, db: Session = Depends(get_db)):
    """Toggle the connected status of an integration."""
    integration = db.query(IntegrationModel).filter(IntegrationModel.id == integration_id).first()
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")

    integration.connected = not integration.connected
    integration.lastSync = 'Just now' if integration.connected else 'Never'
    db.commit()
    db.refresh(integration)

    return {
        "id": integration.id,
        "connected": integration.connected,
        "lastSync": integration.lastSync
    }


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
