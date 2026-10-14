import httpx
import pytest
from triage_sentinel.main import app


async def request(
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.request(method, path, headers=headers)


@pytest.mark.anyio
async def test_health_endpoint_returns_ok() -> None:
    response = await request("GET", "/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_health_endpoint_allows_frontend_origin() -> None:
    response = await request(
        "OPTIONS",
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
