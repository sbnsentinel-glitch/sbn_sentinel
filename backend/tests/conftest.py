import pytest


@pytest.fixture(autouse=True)
def clear_rate_limits():
    from app.api.v1.endpoints.auth import _ip_rate_limits
    _ip_rate_limits.clear()
