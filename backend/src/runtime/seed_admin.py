from app.models.user import User, UserRole
from app.db.database import SessionLocal
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))


def make_admin(email: str):
    db = SessionLocal()
    user = db.query(User).filter(User.email == email).first()
    if not user:
        print(f"User {email} not found.")
        return

    user.role = UserRole.SYSTEM_ADMINISTRATOR.value
    db.commit()
    print(f"User {email} is now a System Administrator.")
    db.close()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        make_admin(sys.argv[1])
    else:
        make_admin("vjzest9569@gmail.com")
