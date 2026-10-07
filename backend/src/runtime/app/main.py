from fastapi import FastAPI
from app.api.endpoints import jwks
from starlette.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import setup_logging
from app.api.v1.api import api_router
from app.models.user import User, UserRole
from app.models.otp import OTPModel  # noqa

# Create tables in SQLite/PostgreSQL (if they don't exist)
# F-28: Transitioned to Alembic migrations.
# Run migrations via `alembic upgrade head` instead of create_all()
# Base.metadata.create_all(bind=engine)

# SES-010: Initialize structured JSON logging for observability
setup_logging(settings.LOG_LEVEL if hasattr(settings, "LOG_LEVEL") else "INFO")


def create_app() -> FastAPI:
    """
    SBN Sentinel Backend Factory.
    Initializes the FastAPI application with enterprise configurations.
    """
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        openapi_url=f"{settings.API_V1_STR}/openapi.json",
        description="Core Intelligence Backend for SBN Sentinel Revenue Architecture."
    )

    # Set all CORS enabled origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Allow all domains including Vercel
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount JWKS directly on app — public URL is /.well-known/jwks.json (NOT under /api/v1)
    app.include_router(jwks.router)
    # Mount the v1 API router under /api/v1
    app.include_router(api_router, prefix=settings.API_V1_STR)

    @app.get("/")
    def root():
        return {
            "message": "Welcome to SBN Sentinel Core Engine",
            "documentation": "/docs"
        }

    @app.on_event("startup")
    def seed_data():
        """
        Auto-seed a super_admin account on first startup and initialize governance seeds.
        """
        from app.services.governance_registry import initialize_registry_seeds
        initialize_registry_seeds()
        # Auto-seed a super_admin account on first startup.
        # Default credentials: superadmin@sbnsentinel.com / SBNAdmin@2024
        from app.db.database import SessionLocal
        from app.core.security import get_password_hash
        db = SessionLocal()
        try:
            existing = db.query(User).filter(User.email == "superadmin@sbnsentinel.com").first()
            if not existing:
                admin = User(
                    email="superadmin@sbnsentinel.com",
                    hashed_password=get_password_hash(settings.BOOTSTRAP_ADMIN_PASSWORD),
                    full_name="SBN Super Admin",
                    role=UserRole.SYSTEM_ADMINISTRATOR.value,
                    is_active=True
                )
                db.add(admin)
                db.commit()
                print("[SUCCESS] Super Admin seeded: superadmin@sbnsentinel.com / [HIDDEN]")
            else:
                print("[SUCCESS] Super Admin already exists.")

        except Exception as e:
            print(f"[WARNING] Could not seed bootstrap data: {e}")
        finally:
            db.close()

    seed_data()

    return app


app = create_app()
