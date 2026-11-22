import io

import httpx
import pytest

from app.config import settings
from app.routers import assistant as assistant_router
from app.services import blob
from tests.apk_factory import valid_apk_bytes


def _project(client, headers):
    return client.post("/api/v1/projects", json={"name": "Demo"}, headers=headers).json()["id"]


# assistant
def test_assistant_disabled_without_key(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    monkeypatch.setattr(settings, "gemini_api_key", None)
    assert client.get("/api/v1/assistant/status", headers=headers).json()["enabled"] is False
    r = client.post("/api/v1/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=headers)
    assert r.status_code == 503


def test_assistant_requires_auth(client):
    assert client.post("/api/v1/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 401


def test_assistant_sends_workspace_context_and_returns_reply(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    _project(client, headers)
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    seen = {}

    def fake_post(url, json, timeout, headers):
        seen.update(url=url, body=json, headers=headers)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "Use a smaller image cache."}]}}]})

    monkeypatch.setattr(assistant_router.httpx, "post", fake_post)
    r = client.post("/api/v1/assistant/chat", headers=headers, json={"messages": [
        {"role": "user", "content": "Why is my app slow?"}, {"role": "assistant", "content": "Which run?"}, {"role": "user", "content": "The last one"}]})
    assert r.status_code == 200 and r.json()["reply"] == "Use a smaller image cache."
    assert seen["headers"]["x-goog-api-key"] == "test-key"
    system = seen["body"]["systemInstruction"]["parts"][0]["text"]
    assert "#1 Demo" in system and "Never invent" in system
    assert [c["role"] for c in seen["body"]["contents"]] == ["user", "model", "user"]


def test_assistant_maps_upstream_rate_limit(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(assistant_router.httpx, "post", lambda *a, **k: httpx.Response(429, json={}))
    r = client.post("/api/v1/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=headers)
    assert r.status_code == 429 and "busy" in r.json()["detail"]


# direct (Blob) upload
TOKEN = "vercel_blob_rw_StoreAbc123_secretsecretsecret"


def test_upload_token_is_404_when_blob_not_configured(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    monkeypatch.setattr(settings, "blob_read_write_token", None)
    pid = _project(client, headers)
    r = client.post(f"/api/v1/projects/{pid}/builds/upload-token", headers=headers)
    assert r.status_code == 404 and r.json()["detail"].startswith("Direct APK upload")


def test_upload_token_and_register_uploaded_build(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    monkeypatch.setattr(settings, "blob_read_write_token", TOKEN)
    pid = _project(client, headers)
    grant = client.post(f"/api/v1/projects/{pid}/builds/upload-token", headers=headers).json()
    assert grant["pathname"].startswith(f"quarantine/p{pid}/") and grant["client_token"].startswith("vercel_blob_client_StoreAbc123_")

    apk = valid_apk_bytes()

    def fake_download(url, target, max_bytes):
        target.write(apk)
        return len(apk)

    monkeypatch.setattr(blob, "download_to", fake_download)
    url = f"https://storeabc123.private.blob.vercel-storage.com/{grant['pathname']}"
    body = {"url": url, "pathname": grant["pathname"]}
    r = client.post(f"/api/v1/projects/{pid}/builds/from-upload", json=body, headers=headers)
    assert r.status_code == 201, r.text
    assert r.json()["scan_status"] == "quarantined" and r.json()["size_bytes"] == len(apk)
    assert client.post(f"/api/v1/projects/{pid}/builds/from-upload", json=body, headers=headers).status_code == 409


@pytest.mark.parametrize("url,pathname", [
    ("https://evil.example.com/quarantine/p{pid}/" + "a" * 32 + ".apk", "quarantine/p{pid}/" + "a" * 32 + ".apk"),
    ("https://storeabc123.private.blob.vercel-storage.com/other.apk", "quarantine/p{pid}/" + "a" * 32 + ".apk"),
    ("https://storeabc123.private.blob.vercel-storage.com/quarantine/p999/" + "a" * 32 + ".apk", "quarantine/p999/" + "a" * 32 + ".apk"),
])
def test_register_rejects_foreign_urls_and_paths(client, auth_headers, monkeypatch, url, pathname):
    headers, _ = auth_headers
    monkeypatch.setattr(settings, "blob_read_write_token", TOKEN)
    pid = _project(client, headers)
    body = {"url": url.format(pid=pid), "pathname": pathname.format(pid=pid), "package_name": "com.example.app"}
    assert client.post(f"/api/v1/projects/{pid}/builds/from-upload", json=body, headers=headers).status_code == 400
