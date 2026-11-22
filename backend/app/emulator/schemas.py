"""Shared, side-effect-free schemas for the Android execution environment.

Imported by both the FastAPI process (validation, preflight planning) and the execution worker.
Nothing in this module touches subprocesses, the filesystem or the network.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# Lifecycle state machine
class EnvironmentStatus(str, Enum):
    PREPARED = "PREPARED"                    # user-side preflight passed, awaiting explicit execute
    QUEUED = "QUEUED"                        # claimable by a worker
    PREFLIGHT = "PREFLIGHT"                  # worker re-checking host capabilities
    PROVISIONING = "PROVISIONING"            # creating the disposable AVD
    BOOTING = "BOOTING"
    READY = "READY"
    INSTALLING = "INSTALLING"
    APPLYING_CONDITIONS = "APPLYING_CONDITIONS"
    VERIFYING_CONDITIONS = "VERIFYING_CONDITIONS"
    EXECUTING = "EXECUTING"
    COLLECTING_METRICS = "COLLECTING_METRICS"
    ANALYZING = "ANALYZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CLEANING = "CLEANING"
    CLEANED = "CLEANED"


S = EnvironmentStatus
_FORWARD = [S.PREFLIGHT, S.PROVISIONING, S.BOOTING, S.READY, S.INSTALLING, S.APPLYING_CONDITIONS,
            S.VERIFYING_CONDITIONS, S.EXECUTING, S.COLLECTING_METRICS, S.ANALYZING, S.COMPLETED]
#: Allowed transitions reported by a worker. Server-side requeue/cancel paths bypass this on purpose.
ALLOWED_TRANSITIONS: dict[EnvironmentStatus, set[EnvironmentStatus]] = {
    S.PREPARED: {S.QUEUED, S.CANCEL_REQUESTED},
    S.QUEUED: {S.PREFLIGHT, S.FAILED, S.CANCEL_REQUESTED},
    S.COMPLETED: {S.CLEANING},
    S.FAILED: {S.CLEANING},
    S.CANCEL_REQUESTED: {S.CLEANING, S.CLEANED},
    S.CLEANING: {S.CLEANED},
    S.CLEANED: set(),
}
for _i, _state in enumerate(_FORWARD[:-1]):
    # Physical devices skip PROVISIONING/BOOTING, so any later forward state is allowed.
    ALLOWED_TRANSITIONS[_state] = set(_FORWARD[_i + 1:]) | {S.FAILED, S.CANCEL_REQUESTED}

ACTIVE_WORKER_STATES = {s for s in _FORWARD if s is not S.COMPLETED} | {S.CLEANING}
TERMINAL_STATES = {S.CLEANED}


def transition_allowed(current: str, new: str) -> bool:
    if current == new:
        return True
    try:
        return EnvironmentStatus(new) in ALLOWED_TRANSITIONS[EnvironmentStatus(current)]
    except (ValueError, KeyError):
        return False


# Status vocabularies
class MeasurementStatus(str, Enum):
    ACTUAL = "ACTUAL"            # measured on the device from a real OS/kernel/tool source
    EMULATED = "EMULATED"        # value reported by a virtual HAL / emulator control, not physics
    CONFIGURED = "CONFIGURED"    # a requested setting; never stored as a metric sample
    UNSUPPORTED = "UNSUPPORTED"  # this environment cannot provide it
    UNAVAILABLE = "UNAVAILABLE"  # supported in principle, but not obtainable this run
    INVALID = "INVALID"          # collected but failed validation


MEASUREMENT_TO_VALIDITY = {
    MeasurementStatus.ACTUAL: "valid",
    MeasurementStatus.EMULATED: "valid",
    MeasurementStatus.UNSUPPORTED: "unsupported",
    MeasurementStatus.UNAVAILABLE: "unavailable",
    MeasurementStatus.INVALID: "invalid",
}


class ApplicationStatus(str, Enum):
    APPLIED_VERIFIED = "applied_verified"        # applied and confirmed by an independent read-back
    APPLIED_UNVERIFIED = "applied_unverified"    # command succeeded, no read-back available
    EMULATED = "emulated"                        # virtual control set (e.g. emulator battery) and read back
    CONFIGURED = "configured"                    # recorded only; nothing on the device enforces it
    UNSUPPORTED = "unsupported"
    REQUIRES_PHYSICAL_DEVICE = "requires_physical_device"
    REQUIRES_LINUX_GATEWAY = "requires_linux_gateway"
    FAILED = "failed"
    REVERTED = "reverted"


class CapabilityStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    REQUIRES_PHYSICAL_DEVICE = "requires_physical_device"
    REQUIRES_LINUX_GATEWAY = "requires_linux_gateway"
    REQUIRES_ROOTABLE_IMAGE = "requires_rootable_image"


# Requested environment (what the user asks for)
class ScreenSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width_px: int = Field(720, ge=320, le=3840)
    height_px: int = Field(1600, ge=320, le=3840)
    density_dpi: int = Field(320, ge=120, le=640)


class DeviceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_level: int = Field(30, ge=24, le=100)
    system_image_tag: Literal["google_apis", "default", "google_apis_playstore", "aosp_atd", "google_atd"] | None = None
    abi: Literal["x86_64", "x86", "arm64-v8a"] | None = None
    ram_mb: int = Field(2048, ge=1024, le=8192)
    vm_heap_mb: int | None = Field(None, ge=64, le=1024)
    cpu_cores: int = Field(2, ge=1, le=8)
    data_partition_mb: int = Field(4096, ge=1024, le=32768)
    sdcard_mb: int = Field(0, ge=0, le=8192)
    screen: ScreenSpec = Field(default_factory=ScreenSpec)


class StorageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    free_target_mb: int | None = Field(None, ge=1, le=65536)
    free_target_pct: float | None = Field(None, gt=0, lt=100)
    cache_limit_mb: int | None = Field(None, ge=0)
    app_storage_limit_mb: int | None = Field(None, ge=0)

    @model_validator(mode="after")
    def one_target(self):
        if self.free_target_mb is not None and self.free_target_pct is not None:
            raise ValueError("specify either free_target_mb or free_target_pct, not both")
        return self


class MemorySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pressure_enabled: bool = False
    target_available_mb: int | None = Field(None, ge=64, le=8192)

    @model_validator(mode="after")
    def target_required(self):
        if self.pressure_enabled and self.target_available_mb is None:
            raise ValueError("memory pressure requires target_available_mb")
        return self


class NetworkCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    online: bool = True
    download_kbps: int | None = Field(None, ge=8, le=1_000_000)
    upload_kbps: int | None = Field(None, ge=8, le=1_000_000)
    latency_ms: int | None = Field(None, ge=0, le=10_000)
    jitter_ms: int | None = Field(None, ge=0, le=5_000)
    packet_loss_pct: float | None = Field(None, ge=0, le=100)
    burst_loss_enter_pct: float | None = Field(None, gt=0, le=100)
    burst_loss_exit_pct: float | None = Field(None, gt=0, le=100)
    duplication_pct: float | None = Field(None, ge=0, le=100)
    reorder_pct: float | None = Field(None, ge=0, le=100)
    dns_failure: bool = False

    @model_validator(mode="after")
    def burst_pair(self):
        if (self.burst_loss_enter_pct is None) != (self.burst_loss_exit_pct is None):
            raise ValueError("burst loss needs both burst_loss_enter_pct and burst_loss_exit_pct")
        if self.reorder_pct and not self.latency_ms:
            raise ValueError("packet reordering requires a non-zero latency_ms (netem reorders delayed packets)")
        return self

    def needs_gateway(self) -> list[str]:
        """Parameters the Android Emulator itself cannot apply."""
        keys = []
        if self.packet_loss_pct:
            keys.append("network.packet_loss_pct")
        if self.burst_loss_enter_pct:
            keys.append("network.burst_loss")
        if self.duplication_pct:
            keys.append("network.packet_duplication_pct")
        if self.reorder_pct:
            keys.append("network.packet_reordering_pct")
        if self.dns_failure:
            keys.append("network.dns_failure")
        return keys


class TimelineStage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_s: float = Field(ge=0, le=1800)
    end_s: float = Field(gt=0, le=1800)
    label: str = Field(min_length=1, max_length=80)
    condition: NetworkCondition

    @model_validator(mode="after")
    def ordered(self):
        if self.end_s <= self.start_s:
            raise ValueError("timeline stage end_s must be after start_s")
        return self


class NetworkSpec(NetworkCondition):
    timeline: list[TimelineStage] = Field(default_factory=list, max_length=50)
    seed: int | None = Field(None, ge=0, le=2**31 - 1)

    @field_validator("timeline")
    @classmethod
    def non_overlapping(cls, stages: list[TimelineStage]):
        ordered = sorted(stages, key=lambda s: s.start_s)
        for a, b in zip(ordered, ordered[1:]):
            if b.start_s < a.end_s:
                raise ValueError(f"timeline stages '{a.label}' and '{b.label}' overlap")
        return ordered


class BatterySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level_pct: int | None = Field(None, ge=0, le=100)
    charging: bool | None = None
    ac_connected: bool | None = None
    health: Literal["good", "overheat", "dead", "overvoltage", "failure", "unknown"] | None = None
    present: bool | None = None
    saver_enabled: bool | None = None

    @model_validator(mode="after")
    def saver_needs_unplugged(self):
        if self.saver_enabled and (self.charging or self.ac_connected):
            raise ValueError("Android refuses battery saver while charging; set charging=false and ac_connected=false")
        return self


class ThermalSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observe: bool = True
    require_physical_validation: bool = False
    sustained_workload_seconds: int = Field(0, ge=0, le=1800)


class SelectorSpec(BaseModel):
    """App-specific UI selector, matched host-side against a UIAutomator hierarchy dump."""
    model_config = ConfigDict(extra="forbid")
    resource_id: str | None = Field(None, max_length=200)
    text: str | None = Field(None, max_length=200)
    text_contains: str | None = Field(None, max_length=200)
    content_desc: str | None = Field(None, max_length=200)

    @model_validator(mode="after")
    def one_field(self):
        if not any([self.resource_id, self.text, self.text_contains, self.content_desc]):
            raise ValueError("selector needs resource_id, text, text_contains or content_desc")
        return self


class EnvironmentRequest(BaseModel):
    """Everything a user can request for one run. Values are *requests*; observed values are reported separately."""
    model_config = ConfigDict(extra="forbid")
    environment_type: Literal["emulator", "physical_device"] = "emulator"
    device: DeviceSpec = Field(default_factory=DeviceSpec)
    storage: StorageSpec = Field(default_factory=StorageSpec)
    memory: MemorySpec = Field(default_factory=MemorySpec)
    network: NetworkSpec = Field(default_factory=NetworkSpec)
    battery: BatterySpec = Field(default_factory=BatterySpec)
    thermal: ThermalSpec = Field(default_factory=ThermalSpec)
    scenario_bindings: dict[str, SelectorSpec] = Field(default_factory=dict, max_length=40)
    grant_runtime_permissions: bool = True
    #: When true, a supported condition that fails verification aborts the run before scenarios execute.
    strict_conditions: bool = True

    @field_validator("scenario_bindings")
    @classmethod
    def binding_names(cls, value: dict[str, SelectorSpec]):
        import re
        for key in value:
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
                raise ValueError(f"binding name '{key}' must be lower_snake_case")
        return value


def request_from_profile(configuration: dict[str, Any]) -> EnvironmentRequest:
    """Translate a saved condition-profile version into an environment request (prefill only)."""
    data: dict[str, Any] = {"device": {}, "storage": {}, "network": {}, "battery": {}}
    if (ram := configuration.get("device.target_ram_gb")) is not None:
        data["device"]["ram_mb"] = int(float(ram) * 1024)
    if (free := configuration.get("storage.free_target_mb")) is not None:
        data["storage"]["free_target_mb"] = int(free)
    for src, dst in (("network.latency_ms", "latency_ms"), ("network.jitter_ms", "jitter_ms"), ("network.packet_loss_pct", "packet_loss_pct")):
        if configuration.get(src) not in (None, 0):
            data["network"][dst] = configuration[src]
    if (level := configuration.get("battery.level_pct")) is not None:
        data["battery"].update(level_pct=int(level), charging=False, ac_connected=False)
    if configuration.get("battery.saver_enabled"):
        data["battery"].update(saver_enabled=True, charging=False, ac_connected=False)
    online, offline = configuration.get("network.online_seconds"), configuration.get("network.offline_seconds")
    if online and offline:
        stages, t = [], 0.0
        for cycle in range(3):
            stages.append({"start_s": t, "end_s": t + online, "label": f"online {cycle + 1}", "condition": {"online": True}})
            t += online
            stages.append({"start_s": t, "end_s": t + offline, "label": f"offline {cycle + 1}", "condition": {"online": False}})
            t += offline
        data["network"]["timeline"] = stages
    return EnvironmentRequest.model_validate(data)


# Capability report & preflight plan
class CapabilityEntry(BaseModel):
    status: CapabilityStatus
    reason: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class SystemImageInfo(BaseModel):
    package: str                 # e.g. "system-images;android-30;google_apis;x86_64"
    api_level: int
    tag: str
    abi: str
    rootable: bool
    # Relative directory actually installed in this SDK (some SDK packages use android-37.0).
    # Keep it local to the worker; it is deliberately omitted from API/capability JSON.
    system_dir: str | None = Field(default=None, exclude=True, repr=False)


class CapabilityReport(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    host: dict[str, Any] = Field(default_factory=dict)
    tools: dict[str, CapabilityEntry] = Field(default_factory=dict)
    capabilities: dict[str, CapabilityEntry] = Field(default_factory=dict)
    system_images: list[SystemImageInfo] = Field(default_factory=list)
    physical_devices: list[dict[str, Any]] = Field(default_factory=list)
    worker_version: str = "1"

    def cap(self, key: str) -> CapabilityStatus:
        entry = self.capabilities.get(key) or self.tools.get(key)
        return entry.status if entry else CapabilityStatus.UNAVAILABLE

    def summary(self) -> dict[str, str]:
        return {**{k: v.status.value for k, v in self.tools.items()}, **{k: v.status.value for k, v in self.capabilities.items()}}


PlanOutcome = Literal["will_apply", "emulated", "configured_only", "unsupported", "requires_physical_device", "requires_linux_gateway", "requires_rootable_image", "not_requested"]


class PlanItem(BaseModel):
    parameter_key: str
    requested_value: Any = None
    unit: str | None = None
    outcome: PlanOutcome
    mechanism: str | None = None
    reason: str | None = None


class PreflightResult(BaseModel):
    ok: bool
    environment_type: str
    blocking_errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    items: list[PlanItem] = Field(default_factory=list)
    selected_system_image: SystemImageInfo | None = None
    capability_summary: dict[str, str] = Field(default_factory=dict)
    capability_report_at: datetime | None = None


# Worker -> API records
class ConditionRecord(BaseModel):
    parameter_key: str = Field(min_length=1, max_length=120)
    requested_value: Any = None
    applied_value: Any = None
    observed_value: Any = None
    unit: str | None = Field(None, max_length=40)
    application_status: ApplicationStatus
    mechanism: str | None = Field(None, max_length=200)
    evidence: dict[str, Any] = Field(default_factory=dict)
    applied_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    verified_at: datetime | None = None


class MeasurementRecord(BaseModel):
    """One measurement with full provenance. ``value`` is None unless status is ACTUAL or EMULATED."""
    parameter_key: str
    value: float | int | str | bool | None = None
    unit: str | None = None
    source: str
    status: MeasurementStatus
    collection_method: str
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    run_scenario_id: int | None = None
    reason: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def value_matches_status(self):
        if self.status is MeasurementStatus.CONFIGURED:
            raise ValueError("configured values are condition applications, not measurements")
        has_value = self.value is not None
        if self.status in (MeasurementStatus.ACTUAL, MeasurementStatus.EMULATED) and not has_value:
            raise ValueError(f"{self.parameter_key}: {self.status.value} measurement requires a value")
        if self.status in (MeasurementStatus.UNSUPPORTED, MeasurementStatus.UNAVAILABLE) and has_value:
            raise ValueError(f"{self.parameter_key}: {self.status.value} measurement must not carry a value")
        return self


class EventRecord(BaseModel):
    event_type: str = Field(min_length=1, max_length=60)
    message: str = Field(min_length=1, max_length=4000)
    severity: Literal["debug", "info", "warning", "error"] = "info"
    status: EnvironmentStatus | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    event_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
