"""Worker configuration, Android SDK discovery and AVD/launch configuration generation."""
from __future__ import annotations

import json
import os
import platform
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.emulator.exceptions import SdkNotFoundError
from app.emulator.schemas import EnvironmentRequest, NetworkCondition, SystemImageInfo

EXE = ".exe" if sys.platform == "win32" else ""
BAT = ".bat" if sys.platform == "win32" else ""
AVD_PREFIX = "pocketlab_run_"
_AVD_NAME = re.compile(r"^pocketlab_run_[a-z0-9_]{1,60}$")
_PACKAGE_ID = re.compile(r"^system-images;android-\d{2};[a-z_]+;(x86_64|x86|arm64-v8a)$")


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class WorkerSettings:
    api_url: str = "http://127.0.0.1:8000/api/v1"
    runner_token: str = ""
    agent_name: str = "pocketlab-android-worker"
    authentication_identity: str = "local-worker"
    sdk_root: Path | None = None
    work_dir: Path = Path("./worker_data")
    boot_timeout_s: int = 420
    adb_timeout_s: int = 60
    install_timeout_s: int = 240
    heartbeat_interval_s: int = 30
    poll_interval_s: int = 5
    max_parallel_emulators: int = 1
    storage_reserve_mb: int = 200
    memory_floor_mb: int = 200
    host_ram_overhead_mb: int = 1536
    gpu_mode: str = "auto"
    headless: bool = True
    probe_host: str = "1.1.1.1"
    artifact_max_bytes: int = 50 * 1024 * 1024
    #: device_id (PocketLab registered device) -> local ADB serial. Serials never leave the worker.
    physical_devices: dict[int, str] = field(default_factory=dict)
    netem_enabled: bool = False
    netem_command_prefix: list[str] = field(default_factory=lambda: ["sudo", "-n"])
    keep_failed_avd: bool = False

    @property
    def avd_home(self) -> Path:
        """Dedicated AVD home: PocketLab never lists, modifies or deletes the user's Android Studio AVDs."""
        return self.work_dir / "avd"

    @property
    def runs_dir(self) -> Path:
        return self.work_dir / "runs"

    @classmethod
    def from_env(cls) -> "WorkerSettings":
        devices: dict[int, str] = {}
        raw = os.environ.get("POCKETLAB_PHYSICAL_DEVICES", "").strip()
        if raw:
            devices = {int(k): str(v) for k, v in json.loads(raw).items()}
        sdk = os.environ.get("POCKETLAB_ANDROID_SDK") or os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME")
        return cls(
            api_url=os.environ.get("POCKETLAB_API_URL", cls.api_url).rstrip("/"),
            runner_token=os.environ.get("RUNNER_TOKEN", ""),
            agent_name=os.environ.get("POCKETLAB_AGENT_NAME", cls.agent_name),
            authentication_identity=os.environ.get("POCKETLAB_AGENT_IDENTITY", platform.node() or cls.authentication_identity),
            sdk_root=Path(sdk) if sdk else None,
            work_dir=Path(os.environ.get("POCKETLAB_WORK_DIR", "./worker_data")).resolve(),
            boot_timeout_s=int(os.environ.get("POCKETLAB_BOOT_TIMEOUT_S", 420)),
            storage_reserve_mb=int(os.environ.get("POCKETLAB_STORAGE_RESERVE_MB", 200)),
            memory_floor_mb=int(os.environ.get("POCKETLAB_MEMORY_FLOOR_MB", 200)),
            gpu_mode=os.environ.get("POCKETLAB_EMULATOR_GPU", "auto"),
            headless=_env_bool("POCKETLAB_EMULATOR_HEADLESS", True),
            probe_host=os.environ.get("POCKETLAB_PROBE_HOST", "1.1.1.1"),
            physical_devices=devices,
            netem_enabled=_env_bool("POCKETLAB_NETEM_ENABLED", False),
            keep_failed_avd=_env_bool("POCKETLAB_KEEP_FAILED_AVD", False),
        )


@dataclass
class AndroidSdk:
    root: Path
    adb: Path | None = None
    emulator: Path | None = None
    avdmanager: Path | None = None
    sdkmanager: Path | None = None
    aapt2: Path | None = None
    apksigner: Path | None = None

    @classmethod
    def locate(cls, explicit: Path | None) -> "AndroidSdk":
        """Find the SDK only from explicit configuration, ANDROID_SDK_ROOT or ANDROID_HOME."""
        candidates = [p for p in (explicit, os.environ.get("ANDROID_SDK_ROOT"), os.environ.get("ANDROID_HOME")) if p]
        if not candidates:
            raise SdkNotFoundError("Android SDK not configured. Set ANDROID_SDK_ROOT (or POCKETLAB_ANDROID_SDK) to your SDK directory.")
        root = Path(candidates[0]).expanduser().resolve()
        if not root.is_dir():
            raise SdkNotFoundError(f"Configured Android SDK directory does not exist: {root}")
        sdk = cls(root=root)
        sdk.adb = _existing(root / "platform-tools" / f"adb{EXE}")
        sdk.emulator = _existing(root / "emulator" / f"emulator{EXE}")
        for tools_bin in [root / "cmdline-tools" / "latest" / "bin", *sorted((root / "cmdline-tools").glob("*/bin"), reverse=True), root / "tools" / "bin"]:
            sdk.avdmanager = sdk.avdmanager or _existing(tools_bin / f"avdmanager{BAT}")
            sdk.sdkmanager = sdk.sdkmanager or _existing(tools_bin / f"sdkmanager{BAT}")
        build_tools = sorted((root / "build-tools").glob("*"), key=_version_key, reverse=True) if (root / "build-tools").is_dir() else []
        for bt in build_tools:
            sdk.aapt2 = sdk.aapt2 or _existing(bt / f"aapt2{EXE}")
            sdk.apksigner = sdk.apksigner or _existing(bt / f"apksigner{BAT}")
        return sdk

    def system_images(self) -> list[SystemImageInfo]:
        """Installed system images, read from each image's source.properties (not from names alone)."""
        images = []
        base = self.root / "system-images"
        if not base.is_dir():
            return images
        for props in base.glob("android-*/*/*/source.properties"):
            values = _read_properties(props)
            abi_dir = props.parent
            api_value = values.get("AndroidVersion.ApiLevel", abi_dir.parent.parent.name.split("-")[-1])
            try:
                # Recent SDK packages encode this as e.g. "37.0". The API level is still
                # an integer; extension levels are reported separately in source.properties.
                if not re.fullmatch(r"\d+(?:\.0+)?", api_value):
                    continue
                api = int(float(api_value))
            except ValueError:
                continue
            tag = values.get("SystemImage.TagId", abi_dir.parent.name)
            abi = values.get("SystemImage.Abi", abi_dir.name)
            images.append(SystemImageInfo(package=f"system-images;android-{api};{tag};{abi}", api_level=api, tag=tag, abi=abi,
                                          rootable=tag in ("google_apis", "default", "aosp_atd", "google_atd"),
                                          system_dir=abi_dir.relative_to(self.root).as_posix().rstrip("/") + "/"))
        return sorted(images, key=lambda i: (i.api_level, i.tag, i.abi))


def _existing(path: Path) -> Path | None:
    return path if path.is_file() else None


def _version_key(path: Path):
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", path.name))


def _read_properties(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def avd_name_for_run(run_id: int, attempt: int, nonce: str) -> str:
    name = f"{AVD_PREFIX}{int(run_id)}_a{int(attempt)}_{re.sub(r'[^a-z0-9]', '', nonce.lower())[:8]}"
    if not _AVD_NAME.fullmatch(name):
        raise ValueError(f"Invalid AVD name {name}")
    return name


def is_pocketlab_avd(name: str) -> bool:
    return bool(_AVD_NAME.fullmatch(name))


def validate_package_id(package: str) -> str:
    if not _PACKAGE_ID.fullmatch(package):
        raise ValueError(f"Invalid system image package id: {package}")
    return package


@dataclass
class AvdSpec:
    name: str
    image: SystemImageInfo
    config: dict[str, str]
    launch_args: list[str]
    requested: dict

    def config_ini(self) -> str:
        return "".join(f"{k}={v}\n" for k, v in sorted(self.config.items()))


def emulator_netspeed(condition: NetworkCondition) -> str | None:
    """Emulator speed syntax '<up>:<down>' in kbps."""
    if not (condition.download_kbps or condition.upload_kbps):
        return None
    down = condition.download_kbps or condition.upload_kbps
    up = condition.upload_kbps or condition.download_kbps
    return f"{int(up)}:{int(down)}"


def emulator_netdelay(condition: NetworkCondition) -> str | None:
    """Emulator delay syntax '<min>:<max>' in ms; jitter becomes a range around the latency."""
    if not (condition.latency_ms or condition.jitter_ms):
        return None
    latency, jitter = condition.latency_ms or 0, condition.jitter_ms or 0
    return f"{max(0, latency - jitter)}:{latency + jitter}"


def build_avd_spec(req: EnvironmentRequest, image: SystemImageInfo, name: str, *, gpu_mode: str, headless: bool, port: int) -> AvdSpec:
    d = req.device
    config = {
        "AvdId": name,
        "avd.ini.displayname": name,
        "avd.ini.encoding": "UTF-8",
        "abi.type": image.abi,
        "hw.cpu.arch": {"x86_64": "x86_64", "x86": "x86", "arm64-v8a": "arm64"}[image.abi],
        "image.sysdir.1": image.system_dir or f"system-images/android-{image.api_level}/{image.tag}/{image.abi}/",
        "tag.id": image.tag,
        "target": f"android-{image.api_level}",
        "hw.ramSize": f"{d.ram_mb}",
        "hw.cpu.ncore": str(d.cpu_cores),
        "disk.dataPartition.size": f"{d.data_partition_mb}M",
        "hw.lcd.width": str(d.screen.width_px),
        "hw.lcd.height": str(d.screen.height_px),
        "hw.lcd.density": str(d.screen.density_dpi),
        "hw.keyboard": "yes",
        "hw.gpu.enabled": "yes",
        "hw.gpu.mode": gpu_mode,
        "hw.battery": "yes",
        "hw.audioInput": "no",
        "hw.camera.back": "none",
        "hw.camera.front": "none",
        "showDeviceFrame": "no",
        "fastboot.forceColdBoot": "yes",
        "fastboot.forceFastBoot": "no",
        "snapshot.present": "false",
        "PlayStore.enabled": "true" if image.tag == "google_apis_playstore" else "false",
    }
    if d.vm_heap_mb:
        config["vm.heapSize"] = str(d.vm_heap_mb)
    if d.sdcard_mb:
        config["sdcard.size"] = f"{d.sdcard_mb}M"
    # Every attempt gets a new AVD name and a newly-created userdata image. It is already
    # pristine, so asking the emulator to wipe it again only adds work to the first boot.
    args = ["-avd", name, "-port", str(port),
            "-memory", str(d.ram_mb), "-cores", str(d.cpu_cores),
            "-no-snapshot", "-no-boot-anim", "-no-audio",
            "-gpu", gpu_mode, "-accel", "auto"]
    if headless:
        args.append("-no-window")
    # Initial shaping from the base network condition; runtime changes use the console.
    if (speed := emulator_netspeed(req.network)):
        args += ["-netspeed", speed]
    if (delay := emulator_netdelay(req.network)):
        args += ["-netdelay", delay]
    return AvdSpec(name=name, image=image, config=config, launch_args=args, requested=req.model_dump(mode="json"))
