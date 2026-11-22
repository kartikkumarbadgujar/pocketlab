"""Error responses must carry CORS headers, otherwise the browser hides them and the dashboard can only report
"Could not reach the PocketLab API" instead of the real error."""
from app.config import settings
from app.database import get_db
from app.main import app


def _origin():
    return settings.cors_origins[0]


def test_login_success_and_invalid_credentials_have_cors(client):
    origin = _origin()
    client.post("/api/v1/auth/register", json={"email": "cors@example.com", "password": "correct horse battery staple"})
    ok = client.post("/api/v1/auth/login", json={"email": "cors@example.com", "password": "correct horse battery staple"}, headers={"Origin": origin})
    assert ok.status_code == 200 and ok.json()["access_token"]
    assert ok.headers["access-control-allow-origin"] == origin
    bad = client.post("/api/v1/auth/login", json={"email": "cors@example.com", "password": "wrong password here"}, headers={"Origin": origin})
    assert bad.status_code == 401
    assert bad.headers["access-control-allow-origin"] == origin


def test_rate_limited_response_has_cors(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit", "2/minute")
    origin = _origin()
    responses = [client.get("/health/live", headers={"Origin": origin}) for _ in range(3)]
    assert responses[-1].status_code == 429
    assert responses[-1].json()["detail"] == "Rate limit exceeded"
    assert responses[-1].headers["access-control-allow-origin"] == origin


def test_unexpected_server_error_is_json_with_cors(client):
    def broken_db():
        raise RuntimeError("database exploded")
        yield  # pragma: no cover

    app.dependency_overrides[get_db] = broken_db
    origin = _origin()
    response = client.post("/api/v1/auth/login", json={"email": "x@example.com", "password": "whatever-password"}, headers={"Origin": origin})
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert response.headers["access-control-allow-origin"] == origin
