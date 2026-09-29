from datetime import timedelta, datetime
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.user import User
from pydantic import BaseModel, EmailStr
from app.schemas.user import UserLogin, Token, UserResponse
from app.core.security import verify_password, get_password_hash, create_access_token
from app.core.config import settings
from app.services.data_audit_engine import data_audit_engine
from app.api.deps import get_current_user

router = APIRouter()


class RegisterInitiateRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    role: str = "clinic_admin"


@router.post("/register/initiate")
def initiate_registration(user_in: RegisterInitiateRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == user_in.email).first()
    if user:
        raise HTTPException(
            status_code=400,
            detail="The user with this email already exists in the system.",
        )

    import random
    from app.models.otp import OTPModel
    from app.core.email import send_email

    otp = str(random.randint(100000, 999999))
    db_otp = OTPModel(email=user_in.email, otp_code=otp, purpose="signup")
    db.add(db_otp)
    db.commit()

    html_body = f"""
    <h2>SBN Sentinel Clinic Registration</h2>
    <p>Your verification code is: <strong>{otp}</strong></p>
    <p>Please enter this code in the app to complete your registration.</p>
    """
    background_tasks.add_task(send_email, user_in.email, "SBN Sentinel - Verification Code", html_body, is_html=True)

    return {"message": "OTP sent successfully."}


class RegisterVerifyRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    otp: str
    role: str = "clinic_admin"


@router.post("/register")
def register_user(user_in: RegisterVerifyRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == user_in.email).first()
    if user:
        raise HTTPException(status_code=400, detail="The user already exists.")

    from app.models.otp import OTPModel
    otp_record = db.query(OTPModel).filter(
        OTPModel.email == user_in.email,
        OTPModel.otp_code == user_in.otp,
        OTPModel.purpose == "signup",
        OTPModel.is_used == False  # noqa: E712
    ).order_by(OTPModel.created_at.desc()).first()

    if not otp_record or datetime.utcnow() - otp_record.created_at > timedelta(minutes=settings.OTP_EXPIRE_MINUTES):
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")

    from app.models.user import UserRole
    # Map frontend role to backend UserRole
    assigned_role = UserRole.SYSTEM_ADMINISTRATOR.value
    if user_in.role == 'clinic_admin':
        assigned_role = UserRole.SYSTEM_ADMINISTRATOR.value

    user = User(
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        full_name=user_in.full_name,
        role=assigned_role
    )
    db.add(user)
    otp_record.is_used = True
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login_access_token(user_in: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == user_in.email).first()
    if not user or not verify_password(user_in.password, user.hashed_password):
        # SIAME / SES-008: Log failed authentication attempt
        data_audit_engine._log_internal(
            db,
            user_system=user_in.email,
            action="SECURITY:FAILED_LOGIN",
            module="Authentication",
            correlation_id="Auth")
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    elif not user.is_active:
        data_audit_engine._log_internal(
            db,
            user_system=user_in.email,
            action="SECURITY:FAILED_LOGIN_INACTIVE",
            module="Authentication",
            correlation_id="Auth")
        raise HTTPException(status_code=400, detail="Inactive user")

    # SIAME / SES-008: Log successful authentication
    data_audit_engine._log_internal(
        db,
        user_system=user.email,
        action="SECURITY:USER_LOGIN",
        module="Authentication",
        correlation_id="Auth")

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(user.id), "role": user.role}, expires_delta=access_token_expires
    )
    db.commit()
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user
    }


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user:
        return {"message": "If that email is registered, a password reset OTP has been sent."}

    # Generate OTP
    import random
    from app.models.otp import OTPModel
    from app.core.email import send_email

    otp = str(random.randint(100000, 999999))
    db_otp = OTPModel(email=user.email, otp_code=otp, purpose="reset_password")
    db.add(db_otp)
    db.commit()

    # Send email
    html_body = f"""
    <h2>SBN Sentinel Reset Password</h2>
    <p>Your one-time password (OTP) is: <strong>{otp}</strong></p>
    <p>Please enter this code in the app to reset your password. It will expire soon.</p>
    """
    send_email(user.email, "SBN Sentinel - Reset Password OTP", html_body, is_html=True)

    return {"message": "If that email is registered, a password reset OTP has been sent."}


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    otp: str
    new_password: str


@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    from app.models.otp import OTPModel
    otp_record = db.query(OTPModel).filter(
        OTPModel.email == payload.email,
        OTPModel.otp_code == payload.otp,
        OTPModel.purpose == "reset_password",
        OTPModel.is_used == False  # noqa: E712
    ).order_by(OTPModel.created_at.desc()).first()

    if not otp_record or datetime.utcnow() - otp_record.created_at > timedelta(minutes=settings.OTP_EXPIRE_MINUTES):
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")

    user = db.query(User).filter(User.email == payload.email).first()
    if not user:
        raise HTTPException(status_code=400, detail="User not found")

    user.hashed_password = get_password_hash(payload.new_password)
    otp_record.is_used = True
    db.commit()
    return {"message": "Password has been successfully reset."}


@router.post("/accept-invite")
def accept_invite(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    from app.models.otp import OTPModel
    otp_record = db.query(OTPModel).filter(
        OTPModel.email == payload.email,
        OTPModel.otp_code == payload.otp,
        OTPModel.purpose == "invite",
        OTPModel.is_used == False  # noqa: E712
    ).order_by(OTPModel.created_at.desc()).first()

    if not otp_record or datetime.utcnow() - otp_record.created_at > timedelta(minutes=settings.OTP_EXPIRE_MINUTES):
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")

    user = db.query(User).filter(User.email == payload.email).first()
    if not user:
        raise HTTPException(status_code=400, detail="User not found")

    user.hashed_password = get_password_hash(payload.new_password)
    user.is_active = True
    otp_record.is_used = True
    db.commit()
    return {"message": "Account has been successfully activated."}


@router.get("/me", response_model=UserResponse)
def read_user_me(current_user: User = Depends(get_current_user)):
    """
    Returns the secure profile of the currently authenticated user.
    """
    return current_user
