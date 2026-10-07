import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_a004_cross_endpoint_ip_abuse_limits(async_client: AsyncClient):
    """
    AT-04: Demonstrate cross-endpoint/IP abuse controls.
    """
    # 10 requests should hit the limit (10 allowed, 11th fails)
    for _ in range(10):
        await async_client.post("/api/v1/auth/forgot-password", json={"email": "attacker@example.com"})
            
    # The 11th request across a DIFFERENT endpoint should also be blocked because it's based on IP, not endpoint.
    res_login = await async_client.post("/api/v1/auth/login", json={"email": "attacker@example.com", "password": "wrong"})
    assert res_login.status_code == 429, "Cross-endpoint IP rate limit failed!"
