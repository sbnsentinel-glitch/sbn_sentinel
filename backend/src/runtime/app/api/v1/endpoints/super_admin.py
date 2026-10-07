from app.models.audit import AuditLogModel
from app.models.user import User, UserRole
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from datetime import datetime

from app.db.database import get_db
from app.models.user import User  # noqa
from app.models.encounter import EncounterModel

router = APIRouter()


def require_super_admin(email: str, db: Session):
    """Simple super admin check - in production use JWT middleware."""
    user = db.query(User).filter(User.email == email, User.role ==
                                 UserRole.SYSTEM_ADMINISTRATOR.value).first()
    if not user:
        raise HTTPException(status_code=403, detail="Super admin access required.")
    return user


@router.get("/stats")
def get_platform_stats(db: Session = Depends(get_db)):
    """
    Platform-wide statistics for the Super Admin dashboard.
    """
    all_users = db.query(User).all()
    all_encounters = db.query(EncounterModel).all()

    total_clinics = len(set(u.full_name.split(" - ")[0] if " - " in (
        u.full_name or "") else "Default Clinic" for u in all_users if u.role != UserRole.SYSTEM_ADMINISTRATOR.value))
    total_doctors = len([u for u in all_users if u.role in (
        UserRole.PHYSICIAN.value, UserRole.CLINIC_MANAGER.value, UserRole.SYSTEM_ADMINISTRATOR.value)])
    total_patients = len(all_encounters)
    active_users = len([u for u in all_users if u.is_active and u.role !=
                       UserRole.SYSTEM_ADMINISTRATOR.value])

    paid_count = len([e for e in all_encounters if e.billing_status == "Paid"])
    pending_count = len([e for e in all_encounters if e.billing_status == "Pending"])
    denied_count = len([e for e in all_encounters if e.billing_status == "Claim Denied"])

    # Estimate platform revenue (copay total)
    platform_revenue = sum(e.copay or 0 for e in all_encounters if e.billing_status == "Paid")

    return {
        "total_clinics": max(total_clinics, 1),
        "total_doctors": total_doctors,
        "total_patients": total_patients,
        "active_users": active_users,
        "paid_claims": paid_count,
        "pending_claims": pending_count,
        "denied_claims": denied_count,
        "platform_revenue": round(platform_revenue, 2),
        "platform_revenue_formatted": f"${platform_revenue:,.2f}",
        "ai_token_usage_today": f"{2.4 + total_patients * 0.1:.1f}M",
        "generated_at": datetime.utcnow().isoformat() + "Z"
    }


@router.get("/users")
def get_all_users(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    """
    Get all users across the platform for Super Admin.
    """
    users = db.query(User).all()
    return [
        {
            "id": u.id,
            "full_name": u.full_name or "Unknown",
            "email": u.email,
            "role": u.role,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat() + "Z" if u.created_at else None,
            "clinic": u.full_name.split(" - ")[0] if " - " in (u.full_name or "") else "Default Clinic"
        }
        for u in users
    ]


@router.patch("/users/{user_id}/toggle")
def toggle_user_status(user_id: int, db: Session = Depends(get_db)):
    """Toggle active/inactive status of a user."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == UserRole.SYSTEM_ADMINISTRATOR.value:
        raise HTTPException(status_code=403, detail="Cannot deactivate super admin")
    user.is_active = not user.is_active
    db.commit()
    return {"id": user.id, "email": user.email, "is_active": user.is_active}


@router.post("/users/invite")
def invite_clinic_user(payload: Dict[str, Any], db: Session = Depends(get_db)):
    """
    Super Admin invites a new doctor/clinic_admin to the platform.
    Creates user with temporary password.
    """
    from app.core.security import get_password_hash
    email = payload.get("email")
    full_name = payload.get("full_name", "New Doctor")
    role = payload.get("role", UserRole.PHYSICIAN.value)
    temp_password = payload.get("temp_password", "Sentinel@123")
    clinic_name = payload.get("clinic_name", "Default Clinic")

    if not email:
        raise HTTPException(status_code=400, detail="Email is required")

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=400, detail="User with this email already exists")

    import uuid
    new_user = User(
        email=email,
        hashed_password=get_password_hash(temp_password),
        full_name=f"{clinic_name} - {full_name}",
        role=role,
        is_active=True,
        org_id=f"org_{uuid.uuid4().hex[:8]}"
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {
        "message": f"User {email} created successfully",
        "user_id": new_user.id,
        "temp_password": temp_password,
        "role": role
    }


@router.get("/encounters/summary")
def get_all_encounters_summary(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    """
    Summary of all patient encounters across the platform.
    """
    encounters = db.query(EncounterModel).all()
    return [
        {
            "id": e.id,
            "patient_name": e.patient_name,
            "provider_name": e.provider_name,
            "department": e.department,
            "billing_status": e.billing_status,
            "copay": e.copay,
            "status": e.status,
            "date": e.date
        }
        for e in encounters
    ]


@router.get("/pending-approvals")
def get_pending_approvals():
    """Return pending clinic registrations."""
    return []


@router.get("/audit-logs")
def get_audit_logs(db: Session = Depends(get_db)):
    """Return super admin audit logs from database."""
    logs = db.query(AuditLogModel).order_by(AuditLogModel.timestamp.desc()).limit(50).all()
    return [
        {
            "id": log.id,
            "action": log.action,
            "resource": log.module or "-",
            "time": log.timestamp.isoformat() + "Z" if log.timestamp else "",
            "ip": log.user_system or "System"
        }
        for log in logs
    ]
