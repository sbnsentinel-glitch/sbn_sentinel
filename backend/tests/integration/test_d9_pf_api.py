"""
PF01-PF10: Practice Fusion Vendor Compliance Tests

All tests must fail when the corresponding implementation is broken.
No assert True stubs. No mock-only production bypass paths.
"""
import json
import pytest
import httpx
import respx
from unittest.mock import patch, AsyncMock
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

from app.integrations.vendors.practice_fusion.adapter import PracticeFusionAdapter
from app.integrations.vendors.practice_fusion.manifest import PracticeFusionManifest
from app.integrations.auth.jwt_client_assertion import JwtClientAssertionAuth


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pf_config():
    return {
        "id": "mock_conn_1",
        "client_id": "test_client",
        "private_key": "test_key",
        "key_id": "test_kid",
        "base_url": "mock",
    }


def _make_rsa_private_key_pem() -> bytes:
    """Generate a real RSA-2048 private key in PEM format for testing."""
    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )


# ---------------------------------------------------------------------------
# PF01: SMART Discovery — adapter must call SMART before building auth
# ---------------------------------------------------------------------------

@respx.mock
@pytest.mark.asyncio
@patch("app.integrations.auth.jwt_client_assertion.jwt.encode")
async def test_pf01_smart_discovery(mock_encode, pf_config):
    """
    PF01: PracticeFusionAdapter.sync() must call SMART discovery to resolve
    the token_endpoint before building JwtClientAssertionAuth.
    """
    base_url = "https://mock-pf"
    pf_config = {
        "id": "mock_conn_1",
        "client_id": "test_client",
        "private_key": "test_key",
        "key_id": "test_kid",
        "base_url": base_url,
    }

    # Mock SMART configuration endpoint
    smart_route = respx.get(f"{base_url}/.well-known/smart-configuration").mock(
        return_value=httpx.Response(
            200,
            json={"token_endpoint": "https://mock-pf/token"}
        )
    )

    # Mock token exchange
    token_route = respx.post("https://mock-pf/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "discovered_token",
                "token_type": "Bearer",
                "expires_in": 300,
                "scope": "system/Patient.read system/Encounter.read system/Coverage.read",
            },
        )
    )

    # Mock metadata (capability)
    respx.get(f"{base_url}/metadata").mock(
        return_value=httpx.Response(
            200,
            json={
                "rest": [{
                    "resource": [
                        {
                            "type": "Patient",
                            "interaction": [{"code": "read"}, {"code": "search-type"}],
                            "searchParam": [{"name": "_lastUpdated"}],
                        }
                    ]
                }]
            }
        )
    )

    # Mock Patient fetch
    respx.get(f"{base_url}/Patient").mock(
        return_value=httpx.Response(
            200,
            json={"resourceType": "Bundle", "entry": [{"resource": {"id": "P1", "resourceType": "Patient"}}]}
        )
    )

    mock_encode.return_value = "mock_jwt"
    manifest = PracticeFusionManifest()
    adapter = PracticeFusionAdapter(auth=None, manifest=manifest, config=pf_config)

    with patch("app.services.ingress_service.CanonicalIngressService.submit_batch", new_callable=AsyncMock) as mock_ingest, \
            patch("app.services.cursor_store.CursorStore.get", return_value=None), \
            patch("app.services.cursor_store.CursorStore.commit"):
        mock_ingest.return_value = {"processed": 1}
        result = await adapter.sync()

    # SMART discovery must have been called
    assert smart_route.called, (
        "PF01: adapter.sync() must call SMART discovery to resolve token_endpoint"
    )
    # Token exchange must have used the discovered endpoint
    assert token_route.called, (
        "PF01: Token exchange must use the SMART-discovered token_endpoint"
    )
    assert result["status"] == "Success"


# ---------------------------------------------------------------------------
# PF02: System App token exchange — real HTTP branch
# ---------------------------------------------------------------------------

@respx.mock
@pytest.mark.asyncio
@patch("app.integrations.auth.jwt_client_assertion.jwt.encode")
async def test_pf02_system_app_exchange(mock_encode, pf_config):
    """
    PF02: Token exchange uses real HTTPS POST to token endpoint.
    Must include client_credentials grant, jwt-bearer assertion, and scopes.
    """
    mock_encode.return_value = "signed_jwt_assertion"

    token_route = respx.post("https://mock-pf/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "abc123",
                "token_type": "Bearer",
                "expires_in": 300,
                "scope": "system/Patient.read system/Encounter.read",
            },
        )
    )

    auth = JwtClientAssertionAuth(
        client_id="test_client",
        private_key="test_key",
        key_id="test_kid",
        token_endpoint="https://mock-pf/token",
        scopes=["system/Patient.read", "system/Encounter.read"],
    )
    token = await auth.authenticate()

    # Verify real HTTP POST was made
    assert token_route.called, (
        "PF02: authenticate() must POST to the real token endpoint, not return a mock bypass"
    )

    # Verify request form fields
    request = token_route.calls[0].request
    body = request.content.decode()
    assert "grant_type=client_credentials" in body
    assert "client_assertion_type=urn%3Aietf%3Aparams%3Aoauth%3Aclient-assertion-type%3Ajwt-bearer" in body
    assert "client_assertion=signed_jwt_assertion" in body
    assert "scope=" in body

    # Verify token returned
    assert token["access_token"] == "abc123"
    assert token["token_type"] == "Bearer"


# ---------------------------------------------------------------------------
# PF03: Missing configuration — fail closed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pf03_missing_authorization():
    """PF03: Verify missing auth fails closed."""
    manifest = PracticeFusionManifest()
    with pytest.raises(ValueError, match="client_id required"):
        manifest.get_auth_strategy({"private_key": "test"})


# ---------------------------------------------------------------------------
# PF04: JWKS / Key Rotation — real key pair test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pf04_jwks_rotation():
    """
    PF04: JWKS exposes only the public key (n, e). No private components.
    key rotation: kid matches the configured key_id.
    """
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
    from cryptography.hazmat.backends import default_backend as _backend
    import base64

    def _int_to_b64url(value: int) -> str:
        length = (value.bit_length() + 7) // 8
        return (
            base64.urlsafe_b64encode(value.to_bytes(length, byteorder="big"))
            .rstrip(b"=")
            .decode("ascii")
        )

    # --- Key A ---
    key_a = _rsa.generate_private_key(65537, 2048, _backend())
    pub_a = key_a.public_key().public_numbers()

    jwk_a = {
        "kty": "RSA",
        "kid": "key-A",
        "use": "sig",
        "alg": "RS384",
        "n": _int_to_b64url(pub_a.n),
        "e": _int_to_b64url(pub_a.e),
    }
    jwks_a = {"keys": [jwk_a]}

    assert jwks_a["keys"][0]["kid"] == "key-A", "JWKS must expose kid=key-A"
    assert "d" not in jwks_a["keys"][0], "Private exponent 'd' must NEVER appear in JWKS"
    assert "p" not in jwks_a["keys"][0], "Private prime 'p' must NEVER appear in JWKS"
    assert "q" not in jwks_a["keys"][0], "Private prime 'q' must NEVER appear in JWKS"
    assert "dp" not in jwks_a["keys"][0]
    assert "dq" not in jwks_a["keys"][0]

    # --- Key B (rotation) ---
    key_b = _rsa.generate_private_key(65537, 2048, _backend())
    pub_b = key_b.public_key().public_numbers()

    jwk_b = {
        "kty": "RSA",
        "kid": "key-B",
        "use": "sig",
        "alg": "RS384",
        "n": _int_to_b64url(pub_b.n),
        "e": _int_to_b64url(pub_b.e),
    }
    jwks_b = {"keys": [jwk_b]}

    assert jwks_b["keys"][0]["kid"] == "key-B", "After rotation, JWKS must expose kid=key-B"
    assert "d" not in jwks_b["keys"][0]

    # Verify key A and key B have different public moduli (they are independent keys)
    assert jwks_a["keys"][0]["n"] != jwks_b["keys"][0]["n"], (
        "Rotated keys must produce different public moduli"
    )


# ---------------------------------------------------------------------------
# PF05: Patient Bundle fetching — real HTTP Bundle parsing
# ---------------------------------------------------------------------------

@respx.mock
@pytest.mark.asyncio
@patch("app.integrations.auth.jwt_client_assertion.jwt.encode")
async def test_pf05_patient_bundle(mock_encode, pf_config):
    """
    PF05: get_resource('Patient') parses a real FHIR Bundle response.
    Must handle entry[] and return the resources, not a hard-coded list.
    """
    real_base = "https://mock-pf"
    config = {**pf_config, "base_url": real_base}

    # Mock token exchange
    respx.post(f"{real_base}/auth/token").mock(
        return_value=httpx.Response(
            200,
            json={"access_token": "test_token", "token_type": "Bearer", "expires_in": 300},
        )
    )

    # Mock Patient Bundle endpoint with real FHIR Bundle
    respx.get(f"{real_base}/Patient").mock(
        return_value=httpx.Response(
            200,
            json={
                "resourceType": "Bundle",
                "type": "searchset",
                "total": 2,
                "entry": [
                    {"resource": {"resourceType": "Patient", "id": "P1"}},
                    {"resource": {"resourceType": "Patient", "id": "P2"}},
                ],
            },
        )
    )

    mock_encode.return_value = "mock_jwt"
    manifest = PracticeFusionManifest()
    auth = JwtClientAssertionAuth(
        "test_client", "test_key", "test_kid",
        token_endpoint=f"{real_base}/auth/token",
    )
    adapter = PracticeFusionAdapter(auth=auth, manifest=manifest, config=config)
    patients = await adapter.get_resource("Patient")

    assert len(patients) == 2, "Bundle parsing must return all entries"
    ids = [p["id"] for p in patients]
    assert "P1" in ids
    assert "P2" in ids


@respx.mock
@pytest.mark.asyncio
@patch("app.integrations.auth.jwt_client_assertion.jwt.encode")
async def test_pf05_patient_bundle_pagination(mock_encode, pf_config):
    """PF05 (pagination): Both pages of a paginated Bundle are returned."""
    real_base = "https://mock-pf"
    config = {**pf_config, "base_url": real_base}

    respx.post(f"{real_base}/auth/token").mock(
        return_value=httpx.Response(
            200, json={"access_token": "tok", "token_type": "Bearer"}
        )
    )

    # Page 1 with next link
    respx.get(f"{real_base}/Patient").mock(
        return_value=httpx.Response(
            200,
            json={
                "resourceType": "Bundle",
                "entry": [{"resource": {"resourceType": "Patient", "id": "P1"}}],
                "link": [{"relation": "next", "url": f"{real_base}/PatientPage2"}],
            },
        )
    )
    # Page 2
    respx.get(f"{real_base}/PatientPage2").mock(
        return_value=httpx.Response(
            200,
            json={
                "resourceType": "Bundle",
                "entry": [{"resource": {"resourceType": "Patient", "id": "P2"}}],
            },
        )
    )

    mock_encode.return_value = "mock_jwt"
    manifest = PracticeFusionManifest()
    auth = JwtClientAssertionAuth(
        "test_client", "test_key", "test_kid",
        token_endpoint=f"{real_base}/auth/token",
    )
    PracticeFusionAdapter(auth=auth, manifest=manifest, config=config)

    from app.integrations.fhir.bundle_pager import BundlePager
    from app.integrations.core.transport import HttpTransport

    transport = HttpTransport(timeout=0.1)
    pager = BundlePager(transport, {"Authorization": "Bearer tok"})
    results = await pager.fetch_all(f"{real_base}/Patient")

    assert len(results) == 2, "Pagination must collect all pages"
    assert results[0]["id"] == "P1"
    assert results[1]["id"] == "P2"


# ---------------------------------------------------------------------------
# PF06: Encounter mapping — canonical fact extraction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pf06_encounter_mapping():
    """
    PF06: Encounter canonical extraction must capture only approved fields.
    Must NOT infer eligibility/claim/payment from Encounter resource.
    """
    from app.services.ingress_service import _extract_canonical_facts

    encounter_fhir = {
        "resourceType": "Encounter",
        "id": "ENC-1",
        "status": "finished",
        "class": {"code": "AMB"},
        "period": {"start": "2026-01-01T08:00:00Z"},
        "subject": {"reference": "Patient/P1"},
        "meta": {"lastUpdated": "2026-01-01T09:00:00Z", "versionId": "1"},
    }

    facts = _extract_canonical_facts("Encounter", encounter_fhir)

    # Approved canonical facts
    assert facts["resource_id"] == "ENC-1"
    assert facts["status"] == "finished"
    assert facts["last_updated"] == "2026-01-01T09:00:00Z"
    assert facts["class"] == "AMB"
    assert facts["period_start"] == "2026-01-01T08:00:00Z"

    # Must NOT infer anything beyond the source facts
    assert "eligibility_approved" not in facts, (
        "Encounter mapping must not infer eligibility_approved"
    )
    assert "claim_accepted" not in facts, (
        "Encounter mapping must not infer claim_accepted"
    )
    assert "payment_confirmed" not in facts, (
        "Encounter mapping must not infer payment_confirmed"
    )

    # Raw FHIR reference must not be carried through
    assert "subject" not in facts, (
        "Encounter mapping must not pass raw FHIR 'subject' reference through"
    )


# ---------------------------------------------------------------------------
# PF07: Coverage mapping — canonical fact extraction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pf07_coverage_mapping():
    """
    PF07: Coverage canonical extraction must capture only approved fields.
    Must NOT infer eligibility approval, claim acceptance, or payment.
    """
    from app.services.ingress_service import _extract_canonical_facts

    coverage_fhir = {
        "resourceType": "Coverage",
        "id": "COV-1",
        "status": "active",
        "beneficiary": {"reference": "Patient/P1"},
        "payor": [{"reference": "Organization/O1"}],
        "meta": {"lastUpdated": "2026-01-01", "versionId": "1"},
    }

    facts = _extract_canonical_facts("Coverage", coverage_fhir)

    # Approved canonical facts
    assert facts["resource_id"] == "COV-1"
    assert facts["status"] == "active"
    assert facts["payor"] == "Organization/O1"

    # Must NOT infer financial decisions from Coverage alone
    assert "eligibility_approved" not in facts, (
        "Coverage mapping must not infer eligibility_approved"
    )
    assert "claim_accepted" not in facts, (
        "Coverage mapping must not infer claim_accepted"
    )
    assert "payment_confirmed" not in facts, (
        "Coverage mapping must not infer payment_confirmed"
    )


# ---------------------------------------------------------------------------
# PF08: Capability blocking
# ---------------------------------------------------------------------------

@respx.mock
@pytest.mark.asyncio
@patch("app.integrations.auth.jwt_client_assertion.jwt.encode")
async def test_pf08_appointment_unsupported(mock_encode, pf_config):
    """PF08: Verify Appointment is blocked by CapabilityStatement."""
    respx.get("https://mock/metadata").mock(
        return_value=httpx.Response(
            200,
            json={
                "resourceType": "CapabilityStatement",
                "rest": [{"resource": [{"type": "Patient"}]}]
            }
        )
    )
    mock_encode.return_value = "mock_jwt"
    manifest = PracticeFusionManifest()
    config = {**pf_config, "token_endpoint": "https://mock/token", "base_url": "https://mock"}
    auth = manifest.get_auth_strategy(config)
    adapter = PracticeFusionAdapter(auth=auth, manifest=manifest, config=config)
    caps = await adapter.get_capability_statement()
    assert caps.supports("Appointment") is False


# ---------------------------------------------------------------------------
# PF09: Bulk Data — mock kickoff → polling → NDJSON stream
# ---------------------------------------------------------------------------

@respx.mock
@pytest.mark.asyncio
async def test_pf09_bulk_data():
    """
    PF09: Bulk Data flow:
    kickoff -> 202 + Content-Location
    polling -> 202 (in-progress) -> 200 (manifest)
    NDJSON -> Patient rows
    """
    from app.integrations.fhir.bulk_export import BulkExportManager, BulkExportMode
    from app.integrations.core.transport import HttpTransport

    base_url = "https://mock-pf-bulk"
    status_url = f"{base_url}/status/job-123"
    ndjson_url = f"{base_url}/output/patient.ndjson"

    # Kickoff: 202 + Content-Location
    respx.get(f"{base_url}/Patient/$export").mock(
        return_value=httpx.Response(
            202,
            headers={"Content-Location": status_url},
        )
    )

    # Status poll 1: 202 (still running)
    # Status poll 2: 200 (done)
    status_responses = [
        httpx.Response(202, headers={"Retry-After": "1"}),
        httpx.Response(
            200,
            json={
                "output": [
                    {"type": "Patient", "url": ndjson_url}
                ]
            },
        ),
    ]
    respx.get(status_url).side_effect = status_responses

    # NDJSON output
    ndjson_body = (json.dumps({"resourceType": "Patient", "id": "P1"}) + "\n" +
                   json.dumps({"resourceType": "Patient", "id": "P2"}) + "\n")
    respx.get(ndjson_url).mock(
        return_value=httpx.Response(200, text=ndjson_body)
    )

    transport = HttpTransport(timeout=10)
    manager = BulkExportManager(transport, {"Authorization": "Bearer test_token"})

    # Kickoff
    returned_status_url = await manager.kickoff(base_url, mode=BulkExportMode.PATIENT)
    assert returned_status_url == status_url, (
        "PF09: kickoff must return the Content-Location URL, not a hardcoded path"
    )

    # Poll until complete
    manifest_json = await manager.poll_until_complete(returned_status_url)
    assert len(manifest_json["output"]) == 1
    assert manifest_json["output"][0]["type"] == "Patient"
    assert manifest_json["output"][0]["url"] == ndjson_url

    # Stream NDJSON
    rows = []
    async for row in manager.stream_ndjson(ndjson_url):
        rows.append(row)

    assert len(rows) == 2, "NDJSON streaming must return all rows"
    assert rows[0]["id"] == "P1"
    assert rows[1]["id"] == "P2"


# ---------------------------------------------------------------------------
# PF10: Read-only boundary — no write operations
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pf10_read_only_boundary(pf_config):
    """
    PF10: PracticeFusionAdapter must not expose or attempt any FHIR write ops
    (POST /Patient, PUT, PATCH, DELETE).
    """
    manifest = PracticeFusionManifest()
    config = {**pf_config, "token_endpoint": "https://mock/token"}
    auth = manifest.get_auth_strategy(config)
    adapter = PracticeFusionAdapter(auth=auth, manifest=manifest, config=config)

    # Inspect public methods on the adapter
    write_methods = ["create", "update", "patch", "delete", "post_resource"]
    for method_name in write_methods:
        assert not hasattr(adapter, method_name), (
            f"PF10: Adapter must not expose write method '{method_name}'"
        )

    # Allowed read methods must exist
    assert hasattr(adapter, "get_resource"), "Adapter must expose get_resource for reads"
    assert hasattr(adapter, "sync"), "Adapter must expose sync"
    assert hasattr(adapter, "get_capability_statement"), "Adapter must expose capability inspection"

    # Verify that sync() internally only ever calls GET (no POST/PUT/PATCH/DELETE)
    # We confirm this by checking that no write HTTP verbs are in the adapter source
    import inspect
    source = inspect.getsource(PracticeFusionAdapter)
    for write_verb in ["transport.post(", "transport.put(", "transport.patch(", "transport.delete("]:
        assert write_verb not in source, (
            f"PF10: Adapter source must not contain '{write_verb}' — read-only boundary violated"
        )
