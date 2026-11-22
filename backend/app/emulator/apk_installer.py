"""Controlled installation of the uploaded (approved, quarantined) APK.

Order: SHA-256 against the database record -> ZIP/manifest validation -> manifest parse (package, version,
minSdk) -> package must equal the build's declared package -> minSdk vs device API -> boot confirmed ->
free storage -> device authorization -> `adb install` -> `pm path` + installed versionCode read-back.
Installation success is recorded as such; it is never treated as the app having been tested.
"""
from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import asdict
from pathlib import Path

from app.emulator.adb_manager import AdbManager, validate_package
from app.emulator.apk_manifest import ManifestInfo, manifest_from_apk, parse_aapt2_badging
from app.emulator.exceptions import ApkValidationError, EmulatorError, InstallError
from app.emulator.process import ProcessRunner


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ApkInstaller:
    def __init__(self, runner: ProcessRunner, adb: AdbManager | None = None, *, install_timeout_s: float = 240):
        self.runner = runner
        self.adb = adb
        self.install_timeout_s = install_timeout_s

    def verify_artifact(self, apk: Path, expected_sha256: str) -> dict:
        actual = sha256_file(apk)
        if actual != expected_sha256.lower():
            raise ApkValidationError("APK SHA-256 does not match the uploaded build record", details={"expected": expected_sha256, "actual": actual})
        if not zipfile.is_zipfile(apk):
            raise ApkValidationError("APK is not a ZIP archive")
        with zipfile.ZipFile(apk) as archive:
            names = set(archive.namelist())
            bad = archive.testzip()
        if "AndroidManifest.xml" not in names:
            raise ApkValidationError("APK has no AndroidManifest.xml")
        if bad:
            raise ApkValidationError(f"APK entry failed CRC check: {bad}")
        return {"sha256": actual, "size_bytes": apk.stat().st_size, "entries": len(names), "has_dex": any(n.endswith(".dex") for n in names)}

    def inspect(self, apk: Path) -> ManifestInfo:
        if "aapt2" in self.runner.allowed:
            try:
                result = self.runner.run([str(self.runner.path("aapt2")), "dump", "badging", str(apk)], timeout=60)
                if result.ok:
                    return parse_aapt2_badging(result.stdout)
            except EmulatorError:
                pass
        return manifest_from_apk(apk)

    def verify_signature(self, apk: Path) -> dict:
        if "apksigner" not in self.runner.allowed:
            return {"status": "not_checked", "reason": "apksigner not installed; Android will still reject unsigned APKs at install"}
        result = self.runner.run([str(self.runner.path("apksigner")), "verify", "--print-certs", str(apk)], timeout=120)
        digests = re.findall(r"certificate SHA-256 digest: ([0-9a-f]+)", result.stdout)
        return {"status": "verified" if result.ok else "failed", "signer_sha256": digests[:3], "output_tail": (result.stdout + result.stderr)[-300:]}

    def preinstall_checks(self, info: ManifestInfo, *, expected_package: str, device_api: int, free_bytes: int, apk_size: int) -> list[str]:
        errors = []
        if info.package != expected_package:
            errors.append(f"Manifest package '{info.package}' does not match the build's declared package '{expected_package}'.")
        if info.min_sdk and info.min_sdk > device_api:
            errors.append(f"APK requires minSdk {info.min_sdk}; the device runs API {device_api}.")
        if free_bytes < apk_size * 3 + 64 * 1024 * 1024:
            errors.append(f"Insufficient free storage to install ({free_bytes // (1024 * 1024)} MB free).")
        return errors

    def install(self, apk: Path, info: ManifestInfo, *, grant_permissions: bool) -> dict:
        assert self.adb is not None
        package = validate_package(info.package)
        if self.adb.state() != "device":
            raise InstallError("Device is not online/authorized for installation")
        result = self.adb.install(apk, grant_permissions=grant_permissions, timeout=self.install_timeout_s)
        output = (result.stdout + result.stderr).strip()
        if not result.ok or "Success" not in output:
            failure = re.search(r"(INSTALL_[A-Z_]+)", output)
            raise InstallError(f"adb install failed: {failure.group(1) if failure else output[-300:]}", details=result.evidence())
        if not self.adb.package_installed(package):
            raise InstallError("adb reported success but the package manager does not list the package")
        dump = self.adb.shell("dumpsys", "package", package, timeout=30).stdout
        code = re.search(r"versionCode=(\d+)", dump)
        installed_code = int(code.group(1)) if code else None
        if info.version_code is not None and installed_code is not None and installed_code != info.version_code:
            raise InstallError(f"Installed versionCode {installed_code} differs from the APK's {info.version_code}")
        return {"install_output": output[-300:], "installed_version_code": installed_code, "manifest": asdict(info),
                "runtime_permissions_granted_at_install": grant_permissions, "duration_s": round(result.duration_s, 2)}
