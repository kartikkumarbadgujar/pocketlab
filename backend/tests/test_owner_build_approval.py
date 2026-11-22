from pathlib import Path

from app.config import settings
from tests.apk_factory import valid_apk_bytes


def _apk() -> bytes:
    return valid_apk_bytes()


def _upload(client, headers):
    pid = client.post("/api/v1/projects", json={"name": "Approve"}, headers=headers).json()["id"]
    build = client.post(f"/api/v1/projects/{pid}/builds",
                        files={"file": ("app.apk", _apk(), "application/vnd.android.package-archive")}, headers=headers).json()
    return pid, build["id"]


OK = {"reason": "My own app", "confirm_authorized": True}


def test_owner_can_approve_own_build(client, auth_headers):
    headers, _ = auth_headers
    pid, bid = _upload(client, headers)
    r = client.post(f"/api/v1/projects/{pid}/builds/{bid}/approve", json=OK, headers=headers)
    assert r.status_code == 200 and r.json()["scan_status"] == "approved"
    builds = client.get(f"/api/v1/projects/{pid}/builds", headers=headers).json()
    assert builds[0]["scan_status"] == "approved"


def test_approval_requires_confirmation(client, auth_headers):
    headers, _ = auth_headers
    pid, bid = _upload(client, headers)
    r = client.post(f"/api/v1/projects/{pid}/builds/{bid}/approve", json={**OK, "confirm_authorized": False}, headers=headers)
    assert r.status_code == 422


def test_other_user_cannot_approve(client, auth_headers):
    headers, _ = auth_headers
    pid, bid = _upload(client, headers)
    client.post("/api/v1/auth/register", json={"email": "b@example.com", "password": "another long password"})
    other = client.post("/api/v1/auth/login", json={"email": "b@example.com", "password": "another long password"}).json()
    r = client.post(f"/api/v1/projects/{pid}/builds/{bid}/approve", json=OK, headers={"Authorization": f"Bearer {other['access_token']}"})
    assert r.status_code == 404


def test_tampered_file_is_refused(client, auth_headers):
    headers, _ = auth_headers
    pid, bid = _upload(client, headers)
    stored = next(Path(settings.upload_dir).rglob("*.apk"))
    stored.write_bytes(stored.read_bytes() + b"tampered")
    r = client.post(f"/api/v1/projects/{pid}/builds/{bid}/approve", json=OK, headers=headers)
    assert r.status_code == 409 and "SHA-256" in r.json()["detail"]


def test_operator_only_mode(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    pid, bid = _upload(client, headers)
    monkeypatch.setattr(settings, "allow_owner_build_approval", False)
    assert client.post(f"/api/v1/projects/{pid}/builds/{bid}/approve", json=OK, headers=headers).status_code == 403


def test_audit_outcome_fits_column(client, auth_headers):
    # PostgreSQL enforces VARCHAR lengths (SQLite does not), so a long reason must not break approval.
    from app.main import app
    from app.database import get_db
    from app.models import AuditLog
    from sqlalchemy import select
    headers, _ = auth_headers
    pid, bid = _upload(client, headers)
    r = client.post(f"/api/v1/projects/{pid}/builds/{bid}/approve", json={**OK, "reason": "x" * 300}, headers=headers)
    assert r.status_code == 200
    db = next(app.dependency_overrides[get_db]())
    row = db.scalars(select(AuditLog).where(AuditLog.action == "build.approve")).first()
    assert len(row.outcome) <= AuditLog.__table__.c.outcome.type.length
