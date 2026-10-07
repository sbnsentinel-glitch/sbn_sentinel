from app.core.security import get_password_hash
from app.models.user import User, UserRole
from app.db.database import SessionLocal
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))


def seed_clinic_admin():
    db = SessionLocal()
    email = "clinicadmin@sbnsentinel.com"
    password = "ClinicPassword@123"

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        existing.hashed_password = get_password_hash(password)
        existing.role = UserRole.ORGANIZATION_ADMINISTRATOR.value
        db.commit()
        print(f"Clinic admin {email} was updated with the default password!")
        return

    admin = User(
        email=email,
        hashed_password=get_password_hash(password),
        full_name="Local Clinic Admin",
        role=UserRole.ORGANIZATION_ADMINISTRATOR.value,
        is_active=True
    )
    db.add(admin)
    db.commit()
    print("------------------------------------------")
    print("CLINIC ADMIN CREATED SUCCESSFULLY!")
    print(f"Email: {email}")
    print(f"Password: {password}")
    print("------------------------------------------")
    db.close()


if __name__ == "__main__":
    seed_clinic_admin()
