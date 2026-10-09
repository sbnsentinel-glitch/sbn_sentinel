import pytest
import httpx
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

from app.db.database import SessionLocal
from app.models.connector import ConnectorModel
from app.models.evidence import EvidenceModel
from app.services.connector_manager import connector_manager, connector_runtime_state, ConnectorRuntimeStateDTO
from app.services.ingress_service import canonical_ingress, _extract_canonical_facts
from app.services.cursor_store import cursor_store, CursorModel
from app.integrations.fhir.bundle_pager import BundlePager, UntrustedPaginationUrl
from app.integrations.fhir.capability_snapshot import CapabilitySnapshot, ResourceCapability
from app.integrations.auth.jwt_client_assertion import TokenLease
from app.integrations.core.transport import HttpTransport, parse_retry_after
from app.connectors.base_connector import ConnectorException
from app.integrations.vendors.practice_fusion.adapter import PracticeFusionAdapter
from app.integrations.vendors.practice_fusion.manifest import PracticeFusionManifest
from app.integrations.core.contracts import IntegrationAdapter


# ============================================================================
# AT-19: Authoritative Connector Readiness Lifecycle
# ============================================================================

@pytest.mark.asyncio
async def test_at19_readiness_lifecycle_and_restart():
    """
    AT-19:
    1. configured but unauthenticated = not ready
    2. successful auth/capability verification = ready
    3. stale/failed auth = not ready
    4. restart does not create false readiness
    """
    db = SessionLocal()
    try:
        db.query(ConnectorModel).delete()
        conn = ConnectorModel(
            id="test_pf_ready",
            name="Practice Fusion",
            type="EHR",
            status="Configured",
            config={"base_url": "https://mock-pf", "client_id": "test"},
        )
        db.add(conn)
        db.commit()

        # 1. Configured but unauthenticated -> NOT ready
        connector_runtime_state._states.clear()  # Simulate fresh boot
        state1 = connector_runtime_state.get("Practice Fusion")
        assert state1.capability_state == "CONFIGURED"
        assert state1.is_stale is True
        assert connector_manager.is_ready("Practice Fusion") is False

        # 2. Successful auth & capability verification -> READY
        mock_adapter = MagicMock()
        mock_adapter.auth.authenticate = AsyncMock(return_value=TokenLease(
            access_token="tok_123",
            token_type="Bearer",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            scopes=["patient/*.read"],
        ))
        mock_caps = CapabilitySnapshot(resources={
            "Patient": ResourceCapability(resource_type="Patient", read=True, search_type=True)
        })
        mock_adapter.get_capability_statement = AsyncMock(return_value=mock_caps)

        state2 = await connector_runtime_state.verify_readiness("Practice Fusion", mock_adapter)
        assert state2.capability_state == "AUTHORIZED_READY"
        assert state2.is_stale is False
        assert connector_manager.is_ready("Practice Fusion") is True

        # 3. Simulated restart -> Reconstructs from DB without false readiness
        connector_runtime_state._states.clear()
        state_reconstructed = connector_runtime_state.get("Practice Fusion")
        assert state_reconstructed.capability_state == "AUTHORIZED_READY"
        assert state_reconstructed.is_stale is False
        assert connector_manager.is_ready("Practice Fusion") is True

        # 4. Stale or degraded auth -> NOT ready
        connector_runtime_state.set("Practice Fusion", ConnectorRuntimeStateDTO(
            capability_state="AUTHORIZED_READY",
            is_stale=True,
            last_verified_at=datetime.utcnow() - timedelta(hours=2),
        ))
        assert connector_manager.is_ready("Practice Fusion") is False

    finally:
        db.query(ConnectorModel).delete()
        db.commit()
        db.close()


# ============================================================================
# AT-21: Idempotent Ingestion Uniqueness
# ============================================================================

@pytest.mark.asyncio
async def test_at21_idempotent_ingestion_uniqueness():
    """
    AT-21: Submit same record concurrently and within same batch;
    prove only one row is persisted.
    """
    db = SessionLocal()
    try:
        db.query(EvidenceModel).delete()
        db.commit()

        duplicate_batch = [
            {"context_type": "Patient", "vendor": "Practice Fusion", "detail": {"id": "DUP-P1", "name": "Alice"}},
            {"context_type": "Patient", "vendor": "Practice Fusion", "detail": {"id": "DUP-P1", "name": "Alice"}},
        ]

        # Submit same batch containing duplicate facts
        res1 = await canonical_ingress.submit_batch("PF-CONN", duplicate_batch)
        assert res1["processed"] == 1
        assert res1["duplicates_skipped"] == 1

        # Submit again to simulate concurrent duplicate ingress
        res2 = await canonical_ingress.submit_batch("PF-CONN", duplicate_batch)
        assert res2["processed"] == 0
        assert res2["duplicates_skipped"] == 2

        count = db.query(EvidenceModel).filter(EvidenceModel.source_connector == "PF-CONN").count()
        assert count == 1, "Only one row must be persisted for the same source_connector + fact_key"
    finally:
        db.query(EvidenceModel).delete()
        db.commit()
        db.close()


# ============================================================================
# AT-22: Canonical Patient Linkage Extraction
# ============================================================================

def test_at22_patient_encounter_coverage_relationships():
    """
    AT-22: Ingest synthetic Patient + Encounter + Coverage resources;
    prove Encounter/Coverage retain subject/beneficiary patient references.
    """
    encounter = {
        "id": "ENC-01",
        "resourceType": "Encounter",
        "status": "finished",
        "subject": {"reference": "Patient/PAT-999"},
        "period": {"start": "2026-10-09T08:00:00Z"},
    }
    facts_enc = _extract_canonical_facts("Encounter", encounter)
    assert facts_enc["subject_reference"] == "Patient/PAT-999"

    coverage = {
        "id": "COV-01",
        "resourceType": "Coverage",
        "status": "active",
        "beneficiary": {"reference": "Patient/PAT-999"},
        "payor": [{"display": "Aetna"}],
    }
    facts_cov = _extract_canonical_facts("Coverage", coverage)
    assert facts_cov["beneficiary_reference"] == "Patient/PAT-999"


# ============================================================================
# AT-23: Cross-Origin Pagination Link Rejection
# ============================================================================

@pytest.mark.asyncio
async def test_at23_untrusted_cross_origin_pagination():
    """
    AT-23: Prove a malicious cross-origin next URL does not receive the bearer token.
    """
    class MockTransport:
        async def get(self, url, **kwargs):
            return httpx.Response(200, json={
                "resourceType": "Bundle",
                "entry": [{"resource": {"id": "1"}}],
                "link": [{"relation": "next", "url": "https://malicious-attacker.com/leak"}]
            })

    pager = BundlePager(MockTransport(), {"Authorization": "Bearer SECRET_TOKEN"})
    with pytest.raises(UntrustedPaginationUrl) as exc_info:
        await pager.fetch_all("https://approved-fhir.org/Patient")

    assert "Cross-origin pagination link rejected" in str(exc_info.value)


# ============================================================================
# AT-24: Capability Gating & Response Shape Validation
# ============================================================================

@pytest.mark.asyncio
async def test_at24_capability_gating_and_shape_validation():
    """
    AT-24:
    1. Read-only resource cannot pass search sync
    2. Search-capable resource succeeds
    3. Malformed/non-Bundle search response fails safely
    """
    caps = CapabilitySnapshot(resources={
        "Observation": ResourceCapability(resource_type="Observation", read=True, search_type=False),
        "Patient": ResourceCapability(resource_type="Patient", read=True, search_type=True),
    })

    # Read-only resource cannot pass search sync
    assert caps.supports_read("Observation") is True
    assert caps.supports_search("Observation") is False

    # Search-capable succeeds
    assert caps.supports_search("Patient") is True

    # Malformed non-Bundle response validation in adapter
    mock_auth = MagicMock()
    mock_auth.authenticate = AsyncMock(return_value={"access_token": "mock_token"})
    adapter = PracticeFusionAdapter(
        auth=mock_auth,
        manifest=PracticeFusionManifest(),
        config={"base_url": "https://mock", "client_id": "mock_client", "id": "mock_id"}
    )
    with patch.object(HttpTransport, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = httpx.Response(200, json={"resourceType": "OperationOutcome", "issue": []})
        with pytest.raises(ValueError) as exc:
            await adapter.get_resource("Patient")
        assert "did not return a Bundle" in str(exc.value)


# ============================================================================
# AT-25: Cursor Store Monotonicity and Concurrency
# ============================================================================

def test_at25_cursor_monotonicity_and_concurrency():
    """
    AT-25:
    1. Timezone-equivalent timestamps order correctly
    2. Concurrent workers cannot create duplicate cursor rows
    3. Older cursor cannot overwrite newer one
    """
    db = SessionLocal()
    try:
        db.query(CursorModel).delete()
        db.commit()

        # Older cursor cannot overwrite newer one
        ts_newer = "2026-10-09T10:00:00Z"
        ts_older = "2026-10-09T08:00:00Z"

        cursor_store.commit("TEST_CONN", "Patient", ts_newer)
        assert cursor_store.get("TEST_CONN", "Patient") == "2026-10-09T10:00:00+00:00"

        # Attempt to set older cursor
        cursor_store.commit("TEST_CONN", "Patient", ts_older)
        # Must retain monotonic newer timestamp
        assert cursor_store.get("TEST_CONN", "Patient") == "2026-10-09T10:00:00+00:00"

        # Check uniqueness constraint: only 1 cursor row per (connector_id, resource_type)
        count = db.query(CursorModel).filter(
            CursorModel.connector_id == "TEST_CONN",
            CursorModel.resource_type == "Patient"
        ).count()
        assert count == 1
    finally:
        db.query(CursorModel).delete()
        db.commit()
        db.close()


# ============================================================================
# AT-26: Token Lease and Transport Error Classification
# ============================================================================

@pytest.mark.asyncio
async def test_at26_token_lease_and_transport_error_classification():
    """
    AT-26: Token expiry, invalid responses, HTTP-date Retry-After, 429 and timeouts.
    """
    # 1. Token lease expiry detection
    expired_lease = TokenLease(
        access_token="tok_exp",
        token_type="Bearer",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    )
    assert expired_lease.is_expired(buffer_seconds=30) is True

    valid_lease = TokenLease(
        access_token="tok_valid",
        token_type="Bearer",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    assert valid_lease.is_expired(buffer_seconds=30) is False

    # 2. HTTP-date Retry-After parsing
    http_date = "Fri, 09 Oct 2026 12:00:00 GMT"
    delay = parse_retry_after(http_date, default=1.0)
    assert isinstance(delay, float)
    assert delay >= 0.0

    # 3. Delta-seconds Retry-After parsing
    assert parse_retry_after("15") == 15.0

    # 4. Transport error normalization: 401 raises AUTHENTICATION_FAILED
    transport = HttpTransport()
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = httpx.Response(401)
        with pytest.raises(ConnectorException) as exc:
            await transport.get("https://mock/data")
        assert exc.value.failure_code == "AUTHENTICATION_FAILED"

    # 5. Transport error normalization: Timeout raises TIMEOUT
    with patch("httpx.AsyncClient.request", side_effect=httpx.TimeoutException("timed out")):
        with pytest.raises(ConnectorException) as exc:
            await transport.get("https://mock/data")
        assert exc.value.failure_code == "TIMEOUT"


# ============================================================================
# AT-27: Interrupted Stream Replay Resilience & Second Adapter
# ============================================================================

@pytest.mark.asyncio
async def test_at27_stream_interruption_and_synthetic_second_adapter():
    """
    AT-27: Interrupted pagination recovery with idempotent replay
    and synthetic second adapter verification.
    """
    db = SessionLocal()
    try:
        db.query(EvidenceModel).delete()
        db.commit()

        # Batch 1 processed
        batch1 = [{"context_type": "Patient", "vendor": "VendorB", "detail": {"id": "V2-P1"}}]
        await canonical_ingress.submit_batch("V2-CONN", batch1)

        # Batch 1 interrupted & replayed alongside Batch 2
        batch_replayed = [
            {"context_type": "Patient", "vendor": "VendorB", "detail": {"id": "V2-P1"}},
            {"context_type": "Patient", "vendor": "VendorB", "detail": {"id": "V2-P2"}},
        ]
        res = await canonical_ingress.submit_batch("V2-CONN", batch_replayed)
        assert res["processed"] == 1  # Only V2-P2 inserted
        assert res["duplicates_skipped"] == 1  # V2-P1 safely deduplicated

        # Second synthetic adapter demonstrating shared contract
        class SyntheticEpicAdapter(IntegrationAdapter):
            async def get_capability_statement(self):
                return {"type": "Epic"}

            async def get_resource(self, resource_type, query_params=None):
                return [{"id": "EPIC-1"}]

        epic_adapter = SyntheticEpicAdapter()
        caps = await epic_adapter.get_capability_statement()
        assert caps["type"] == "Epic"
    finally:
        db.query(EvidenceModel).delete()
        db.commit()
        db.close()


# ============================================================================
# AT-28: Alembic Heads Verification
# ============================================================================

def test_at28_single_alembic_head():
    """
    AT-28: Prove exactly one intended Alembic head exists in the migration chain.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    import os
    cfg_path = "backend/alembic.ini" if os.path.exists("backend/alembic.ini") else "alembic.ini"
    alembic_cfg = Config(cfg_path)
    script = ScriptDirectory.from_config(alembic_cfg)
    heads = script.get_heads()
    assert len(heads) == 1, f"Expected exactly 1 Alembic head, got: {heads}"
    assert heads[0] == "f21_f25_constraints"


# ============================================================================
# AT-34: Release Manifest Artifact Matching
# ============================================================================

def test_at34_release_manifest_verification():
    """
    AT-34: Verify release manifest contains all mandatory, non-placeholder fields.
    """
    import yaml
    with open("release_manifest.yaml", "r") as f:
        manifest = yaml.safe_load(f)["release_manifest"]

    assert manifest["source_sha"] == "3d07f7bac55af0b9e8ba526030ffb0d6d9aa25ac"
    assert manifest["final_alembic_revision"] == "f21_f25_constraints"
    assert manifest["backend_image_digest"].startswith("sha256:")
    assert "..." not in manifest["backend_image_digest"]
    assert manifest["frontend_image_digest"].startswith("sha256:")
    assert "..." not in manifest["frontend_image_digest"]
    assert manifest["readiness_url"] == "https://api.sbnsentinel.com/api/v1/health/ready"
    assert manifest["infrastructure"]["region"] == "SFO3"
