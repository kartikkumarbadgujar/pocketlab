from typing import Any, Literal

from pydantic import BaseModel, Field

from app.emulator.schemas import CapabilityReport, ConditionRecord, EnvironmentRequest, EnvironmentStatus, EventRecord


class PreflightInput(BaseModel):
    configuration: EnvironmentRequest
    actual_device_id: int | None = None


class RunnerIdentity(BaseModel):
    agent_name: str = Field(min_length=1, max_length=160)
    authentication_identity: str = Field(min_length=1, max_length=255)


class EnvironmentUpdate(RunnerIdentity):
    status: EnvironmentStatus
    message: str = Field(min_length=1, max_length=2000)
    emulator_name: str | None = Field(None, max_length=120)
    emulator_serial: str | None = Field(None, max_length=64)
    android_api_level: int | None = Field(None, ge=1, le=100)
    system_image: str | None = Field(None, max_length=160)
    effective_configuration: dict[str, Any] | None = None
    active_run_scenario_id: int | None = None
    cleanup_verified: bool | None = None


class DeviceRegistration(RunnerIdentity):
    device_name: str = Field(min_length=1, max_length=120)
    manufacturer: str | None = Field(None, max_length=100)
    model: str | None = Field(None, max_length=120)
    android_version: str | None = Field(None, max_length=64)
    api_level: int | None = Field(None, ge=1, le=1000)
    cpu_architecture: str | None = Field(None, max_length=64)
    cpu_core_count: int | None = Field(None, ge=1, le=256)
    total_ram_mb: int | None = Field(None, ge=1, le=2_000_000)
    total_storage_bytes: int | None = Field(None, ge=1)
    display_width: int | None = Field(None, ge=1, le=20000)
    display_height: int | None = Field(None, ge=1, le=20000)


class EventBatch(RunnerIdentity):
    events: list[EventRecord] = Field(min_length=1, max_length=200)


class ConditionBatch(RunnerIdentity):
    conditions: list[ConditionRecord] = Field(min_length=1, max_length=200)


class ScenarioResultInput(RunnerIdentity):
    status: Literal["running", "passed", "failed", "blocked", "error", "inconclusive"]
    result: dict[str, Any] = Field(default_factory=dict)


class FailInput(RunnerIdentity):
    error_message: str | None = Field(None, max_length=2000)
    retryable: bool = True


class CapabilityHeartbeat(BaseModel):
    """Validated shape of the worker's capability report (stored as JSON on the runner agent)."""
    report: CapabilityReport
