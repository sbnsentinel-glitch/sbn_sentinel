from app.api.v1.endpoints import outcomes
from fastapi import APIRouter, Depends
# Import subsystem routers here when created
from app.api.v1.endpoints import runtime, history
from app.api.v1.endpoints import reality_connectors, signals, auth, insurance, audit, settings, encounters, super_admin, billing, clinics, pasme, pipeline, health, decisions, actions, decision_basis, action_lifecycle


from app.models.user import UserRole
from app.api.deps import get_current_user, RoleChecker
api_router = APIRouter()
# Register Phase 2 Subsystems
# Auth is public
api_router.include_router(auth.router, prefix="/auth", tags=["00_Authentication"])

# JWKS public endpoint
# All other routes protected by SIAME Default Deny (Must be logged in)
protected = [Depends(get_current_user)]

api_router.include_router(reality_connectors.router,
                          prefix="/reality",
                          tags=["000_Reality_Connectors"],
                          dependencies=[Depends(RoleChecker([UserRole.CLINIC_MANAGER.value,
                                                             UserRole.SYSTEM_ADMINISTRATOR.value]))])
api_router.include_router(
    signals.router,
    prefix="/signals",
    tags=["001_Signal_Layer"],
    dependencies=protected)
api_router.include_router(
    insurance.router,
    prefix="/insurance",
    tags=["002_Insurance_Eligibility"],
    dependencies=protected)
api_router.include_router(
    audit.router,
    prefix="/audit",
    tags=["003_Audit_Trails"],
    dependencies=protected)
api_router.include_router(
    settings.router,
    prefix="/settings",
    tags=["Settings"],
    dependencies=protected)
api_router.include_router(
    health.router,
    prefix="/health",
    tags=["Health & Diagnostics"])
api_router.include_router(
    runtime.router,
    prefix="/health",
    tags=["Runtime Status"])

api_router.include_router(
    encounters.router,
    prefix="/encounters",
    tags=["005_Patient_Encounters"],
    dependencies=protected)

# Super Admin requires specific role
api_router.include_router(super_admin.router,
                          prefix="/super-admin",
                          tags=["006_Super_Admin"],
                          dependencies=[Depends(RoleChecker([UserRole.SYSTEM_ADMINISTRATOR.value]))])
api_router.include_router(pasme.router, prefix="/pasme", tags=["009_PASME"])

api_router.include_router(
    billing.router,
    prefix="/billing",
    tags=["007_Billing"],
    dependencies=protected)
api_router.include_router(
    clinics.router,
    prefix="/clinics",
    tags=["008_Clinics"],
    dependencies=protected)

# SES-002: Event Processing Pipeline
api_router.include_router(
    pipeline.router,
    prefix="/pipeline",
    tags=["010_SES002_Event_Pipeline"],
    dependencies=protected)

# SESR-005: Governed Human Decisions
api_router.include_router(
    decisions.router,
    prefix="/decisions",
    tags=["011_SESR005_Decisions"],
    dependencies=protected)

# SESR-006: Governed Operational Actions
api_router.include_router(
    actions.router,
    prefix="/actions",
    tags=["012_SESR006_Actions"],
    dependencies=protected)

# D6.4: Action Lifecycle Read Contract (additional routes under /actions)
api_router.include_router(
    action_lifecycle.router,
    prefix="/actions",
    tags=["015_D6_ActionLifecycle"],
    dependencies=protected)

# SESR-007: Governed Operational Outcomes
api_router.include_router(
    outcomes.router,
    prefix="/outcomes",
    tags=["013_SESR007_Outcomes"],
    dependencies=protected)


# D4 — Decision Basis Read-Only Projection
api_router.include_router(
    decision_basis.router,
    prefix="/decision-basis",
    tags=["014_D4_DecisionBasis"],
    dependencies=protected)

# D8 - History & Reproducibility Read Contract
api_router.include_router(
    history.router,
    prefix="/history",
    tags=["016_D8_History"],
    dependencies=protected)


@api_router.get("/health")
def health_check():
    return {"status": "SBN Sentinel Core is running", "phase": 2}
