"""Vercel Blob storage for APKs on serverless hosting.

Serverless functions reject request and response bodies above ~4.5 MB, so on Vercel the browser uploads the APK
straight to a *private* Blob store with a short-lived client token issued here, and the API then validates the stored
file. The token format mirrors `generateClientTokenFromReadWriteToken` in the official `@vercel/blob` SDK (v2).
Builds stored this way use the storage key ``blob:<url>``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException

from app.config import settings

BLOB_PREFIX = "blob:"
API_URL = "https://vercel.com/api/blob"
API_VERSION = "13"


def enabled() -> bool:
    return bool(settings.blob_read_write_token)


def _token() -> str:
    token = settings.blob_read_write_token
    if not token:
        raise HTTPException(404, "Direct APK upload is not configured on this server")
    return token


def store_id() -> str:
    parts = _token().split("_")
    if len(parts) < 5 or not parts[3]:
        raise HTTPException(500, "BLOB_READ_WRITE_TOKEN has an unexpected format")
    return parts[3]


def client_upload_token(pathname: str, maximum_size_bytes: int, valid_seconds: int = 900) -> str:
    """A client token that lets a browser upload exactly `pathname` (no overwrite, size-capped) for a short time."""
    payload = base64.b64encode(json.dumps({
        "pathname": pathname,
        "maximumSizeInBytes": maximum_size_bytes,
        "addRandomSuffix": False,
        "allowOverwrite": False,
        "validUntil": int((time.time() + valid_seconds) * 1000),
    }, separators=(",", ":")).encode()).decode()
    signature = hmac.new(_token().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"vercel_blob_client_{store_id()}_" + base64.b64encode(f"{signature}.{payload}".encode()).decode()


def check_url(url: str, expected_pathname: str) -> None:
    """Only accept URLs of this store's private blobs at the pathname the token was issued for."""
    parsed = urlparse(url)
    host_ok = parsed.scheme == "https" and parsed.hostname is not None and parsed.hostname.lower().startswith(store_id().lower() + ".") \
        and parsed.hostname.lower().endswith(".blob.vercel-storage.com")
    if not host_ok or parsed.path.lstrip("/") != expected_pathname or parsed.query or parsed.fragment:
        raise HTTPException(400, "Blob URL does not belong to this upload")


def download_to(url: str, target, max_bytes: int) -> int:
    size = 0
    with httpx.stream("GET", url, headers={"Authorization": f"Bearer {_token()}"}, timeout=60, follow_redirects=False) as response:
        if response.status_code == 404:
            raise HTTPException(400, "The uploaded APK was not found in storage")
        if response.status_code != 200:
            raise HTTPException(502, f"Blob storage returned HTTP {response.status_code}")
        for chunk in response.iter_bytes(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise HTTPException(413, "APK exceeds configured size limit")
            target.write(chunk)
    return size


def read_range(url: str, offset: int, length: int) -> tuple[bytes, int]:
    """Bytes [offset, offset+length) of a blob and the blob's total size."""
    response = httpx.get(url, headers={"Authorization": f"Bearer {_token()}", "Range": f"bytes={offset}-{offset + length - 1}"}, timeout=60)
    if response.status_code == 416:
        raise HTTPException(416, "Requested range is outside the APK")
    if response.status_code not in (200, 206):
        raise HTTPException(502, f"Blob storage returned HTTP {response.status_code}")
    if response.status_code == 200:  # server ignored the range
        body = response.content
        return body[offset:offset + length], len(body)
    total = int(response.headers.get("content-range", "*/0").rsplit("/", 1)[-1])
    return response.content, total


def delete(url: str) -> None:
    try:
        httpx.post(f"{API_URL}/delete", json={"urls": [url]}, timeout=30,
                   headers={"Authorization": f"Bearer {_token()}", "x-api-version": API_VERSION}).raise_for_status()
    except (httpx.HTTPError, HTTPException):
        pass  # best effort; an orphaned quarantined blob is harmless
