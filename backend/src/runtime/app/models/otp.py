from sqlalchemy import Column, Integer, String, DateTime, Boolean
from datetime import datetime
from app.models.signal import Base


class OTPModel(Base):
    __tablename__ = "otps"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True)
    otp_code = Column(String)
    purpose = Column(String)  # 'signup' or 'reset_password'
    created_at = Column(DateTime, default=datetime.utcnow)
    is_used = Column(Boolean, default=False)
    attempts = Column(Integer, default=0)
