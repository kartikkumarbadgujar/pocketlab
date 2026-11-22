import hashlib
import tempfile
import uuid
import zipfile
from pathlib import Path
from fastapi import HTTPException, UploadFile
from app.config import settings
from app.emulator.apk_manifest import ManifestInfo, manifest_from_apk
from app.emulator.exceptions import ApkValidationError
from app.services import blob


def _validate_apk_archive(path: Path, size: int) -> ManifestInfo:
    if size < 22 or not zipfile.is_zipfile(path):
        raise HTTPException(400, "Uploaded APK is not a valid ZIP archive")
    with zipfile.ZipFile(path) as archive:
        entries=archive.infolist()
        names = {entry.filename for entry in entries}
        if "AndroidManifest.xml" not in names:
            raise HTTPException(400, "APK is missing AndroidManifest.xml")
        if len(entries)>100_000:
            raise HTTPException(400,"APK archive contains too many entries")
        manifest=archive.getinfo("AndroidManifest.xml")
        if manifest.file_size>10*1024*1024:
            raise HTTPException(400,"APK manifest exceeds safe validation size")
        expanded=sum(entry.file_size for entry in entries)
        if expanded>2*1024*1024*1024:
            raise HTTPException(400,"APK expands beyond the accepted archive limit")
    try:
        return manifest_from_apk(path)
    except ApkValidationError as exc:
        raise HTTPException(400, f"APK Android manifest is invalid: {exc.message}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


async def validate_and_store_apk(upload: UploadFile) -> tuple[str, str, int, ManifestInfo]:
    if not upload.filename or not upload.filename.lower().endswith(".apk"):
        raise HTTPException(400, "APK filename must end in .apk")
    key = f"quarantine/{uuid.uuid4().hex}.apk"
    path = Path(settings.upload_dir).resolve() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        with path.open("wb") as target:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_apk_size_bytes:
                    raise HTTPException(413, "APK exceeds configured size limit")
                target.write(chunk)
        manifest = _validate_apk_archive(path, size)
        return key, _sha256(path), size, manifest
    except Exception:
        path.unlink(missing_ok=True)
        raise


def validate_blob_apk(url: str) -> tuple[str, int, ManifestInfo]:
    """Download a directly-uploaded APK, validate its archive/manifest, and return its parsed metadata."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "upload.apk"
        with path.open("wb") as target:
            size = blob.download_to(url, target, settings.max_apk_size_bytes)
        manifest = _validate_apk_archive(path, size)
        return _sha256(path), size, manifest
