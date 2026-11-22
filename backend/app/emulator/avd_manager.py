"""Disposable AVD management, confined to PocketLab's own ANDROID_AVD_HOME.

The "base template" is the read-only system image plus PocketLab's AVD configuration template
(emulator_config.build_avd_spec). Every run gets a freshly created AVD with wiped userdata and no
snapshots, so no state can leak between runs. User AVDs in ~/.android/avd are never listed or touched.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from app.emulator.emulator_config import AvdSpec, is_pocketlab_avd, validate_package_id
from app.emulator.exceptions import CommandFailedError, EmulatorError
from app.emulator.process import ProcessRunner

logger = logging.getLogger("pocketlab.emulator.avd")


class AvdManager:
    def __init__(self, runner: ProcessRunner, avd_home: Path, sdk_root: Path):
        self.runner = runner
        self.avd_home = avd_home.resolve()
        self.sdk_root = sdk_root
        self.avd_home.mkdir(parents=True, exist_ok=True)

    def _paths(self, name: str) -> tuple[Path, Path]:
        if not is_pocketlab_avd(name):
            raise EmulatorError(f"Refusing to manage non-PocketLab AVD '{name}'")
        ini, folder = self.avd_home / f"{name}.ini", self.avd_home / f"{name}.avd"
        for p in (ini, folder):
            if self.avd_home not in p.resolve().parents:
                raise EmulatorError("AVD path escapes the PocketLab AVD home")
        return ini, folder

    def create(self, spec: AvdSpec) -> dict:
        ini, folder = self._paths(spec.name)
        validate_package_id(spec.image.package)
        if ini.exists() or folder.exists():
            raise EmulatorError(f"AVD {spec.name} already exists; names are unique per run attempt")
        image_dir = self.sdk_root / spec.config["image.sysdir.1"]
        if not image_dir.is_dir():
            raise EmulatorError(f"System image directory missing: {spec.image.package}")
        method = "direct_config"
        evidence: dict = {}
        if "avdmanager" in self.runner.allowed:
            args = [str(self.runner.path("avdmanager")), "create", "avd", "-n", spec.name, "-k", spec.image.package, "-p", str(folder), "--force"]
            if spec.config.get("sdcard.size"):
                args += ["-c", spec.config["sdcard.size"]]
            try:
                result = self.runner.run(args, input=b"no\n", timeout=180)
                evidence = result.evidence()
                if result.ok and folder.is_dir():
                    method = "avdmanager"
                else:
                    logger.warning("avdmanager_failed_falling_back", extra={"evidence": evidence})
            except EmulatorError as exc:
                evidence = {"avdmanager_error": exc.message}
        folder.mkdir(parents=True, exist_ok=True)
        config_path = folder / "config.ini"
        merged = _read_ini(config_path) if config_path.exists() else {}
        merged.update(spec.config)   # PocketLab's requested values always win over tool defaults
        config_path.write_text("".join(f"{k}={v}\n" for k, v in sorted(merged.items())), encoding="utf-8")
        ini.write_text(f"avd.ini.encoding=UTF-8\npath={folder}\ntarget=android-{spec.image.api_level}\n", encoding="utf-8")
        return {"method": method, "config": merged, "avd_path_name": folder.name, "tool_evidence": evidence}

    def read_config(self, name: str) -> dict[str, str]:
        _, folder = self._paths(name)
        return _read_ini(folder / "config.ini")

    def delete(self, name: str) -> dict:
        ini, folder = self._paths(name)
        evidence: dict = {}
        if "avdmanager" in self.runner.allowed and ini.exists():
            try:
                evidence = self.runner.run([str(self.runner.path("avdmanager")), "delete", "avd", "-n", name], timeout=120).evidence()
            except EmulatorError as exc:
                evidence = {"avdmanager_error": exc.message}
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
        ini.unlink(missing_ok=True)
        removed = not ini.exists() and not folder.exists()
        if not removed:
            raise CommandFailedError(f"AVD {name} could not be fully removed", details=evidence)
        return {"removed": True, **({"tool_evidence": evidence} if evidence else {})}

    def list_pocketlab_avds(self) -> list[str]:
        return sorted(p.stem for p in self.avd_home.glob("*.ini") if is_pocketlab_avd(p.stem))


def _read_ini(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
    return values
