import hashlib
import os
import uuid
import zipfile
from pathlib import Path
from fastapi import HTTPException, UploadFile
from app.config import settings


async def validate_and_store_apk(upload: UploadFile) -> tuple[str, str, int]:
    if not upload.filename or not upload.filename.lower().endswith(".apk"):
        raise HTTPException(400, "APK filename must end in .apk")
    key = f"quarantine/{uuid.uuid4().hex}.apk"
    path = Path(settings.upload_dir).resolve() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("wb") as target:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_apk_size_bytes:
                    raise HTTPException(413, "APK exceeds configured size limit")
                digest.update(chunk)
                target.write(chunk)
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
        return key, digest.hexdigest(), size
    except Exception:
        path.unlink(missing_ok=True)
        raise
