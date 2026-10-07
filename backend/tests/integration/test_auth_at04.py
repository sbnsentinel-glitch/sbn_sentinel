from fastapi.testclient import TestClient
from app.main import app
from app.api.v1.endpoints.auth import _ip_rate_limits


def test_a004_cross_endpoint_ip_abuse_limits():
    """
    AT-04: Demonstrate cross-endpoint/IP abuse controls.
    """
    client = TestClient(app)
    _ip_rate_limits.clear()

    # 10 requests should hit the limit (10 allowed, 11th fails)
    for _ in range(10):
        client.post("/api/v1/auth/forgot-password", json={"email": "attacker@example.com"})

    # The 11th request across a DIFFERENT endpoint should also be blocked because it's based on IP, not endpoint.
    res_login = client.post("/api/v1/auth/login", json={"email": "attacker@example.com", "password": "wrong"})
    assert res_login.status_code == 429, "Cross-endpoint IP rate limit failed!"

    # Cleanup to not affect other tests
    _ip_rate_limits.clear()
