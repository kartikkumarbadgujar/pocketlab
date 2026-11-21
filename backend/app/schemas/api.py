from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)


class LoginInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshInput(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=256)


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    application_package: str | None = Field(default=None, max_length=255)


class ProjectOut(ProjectInput):
    model_config = ConfigDict(from_attributes=True)
    id: int
    owner_user_id: int
    created_at: datetime


class DeviceInput(BaseModel):
    device_name: str = Field(min_length=1, max_length=120)
    manufacturer: str | None = None
    model: str | None = None
    android_version: str | None = None
    api_level: int | None = Field(default=None, ge=1, le=1000)
    cpu_architecture: str | None = None
    cpu_model: str | None = None
    cpu_core_count: int | None = Field(default=None, ge=1, le=256)
    gpu_model: str | None = None
    total_ram_mb: int | None = Field(default=None, ge=1, le=2_000_000)
    total_storage_bytes: int | None = Field(default=None, ge=1)
    display_width: int | None = Field(default=None, ge=1, le=20000)
    display_height: int | None = Field(default=None, ge=1, le=20000)
    refresh_rate_hz: float | None = Field(default=None, gt=0, le=1000)


class ProfileInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    profile_type: str = "custom"
    configuration: dict[str, Any] = Field(default_factory=dict)

    @field_validator("configuration")
    @classmethod
    def validate_configuration(cls, value):
        loss = value.get("network.packet_loss_pct")
        if loss is not None and not 0 <= loss <= 100:
            raise ValueError("packet loss must be between 0 and 100")
        thermal = value.get("thermal.status")
        if thermal is not None and thermal not in range(7):
            raise ValueError("thermal status must be an Android thermal status from 0 through 6")
        for key in ("device.target_ram_gb", "storage.free_target_mb", "network.latency_ms", "network.jitter_ms"):
            if key in value and value[key] < 0:
                raise ValueError(f"{key} must be non-negative")
        return value


class MeasurementInput(BaseModel):
    parameter_key: str = Field(min_length=1, max_length=120)
    numeric_value: float | None = None
    text_value: str | None = None
    boolean_value: bool | None = None
    unit: str | None = None
    source: str = Field(min_length=1, max_length=80)
    sample_key: str = Field(min_length=1, max_length=128)
    observed_at: datetime
    validity_status: Literal["valid", "unsupported", "unavailable", "invalid"] = "valid"
    device_id: int | None = None
    run_scenario_id: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("observed_at")
    @classmethod
    def utc_timestamp_required(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        from datetime import timezone
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_measurement(self):
        values = [self.numeric_value is not None, self.text_value is not None, self.boolean_value is not None]
        if sum(values) > 1:
            raise ValueError("provide at most one typed measurement value")
        if self.validity_status == "valid" and not any(values):
            raise ValueError("valid measurements must contain a typed value")
        if self.parameter_key in {"network.packet_loss_pct", "network.packet_loss"} and self.numeric_value is not None and not 0 <= self.numeric_value <= 100:
            raise ValueError("packet loss must be between 0 and 100 percent")
        trusted_source={
            "thermal.status":{"android_api"},
            "battery.temperature_c":{"android_api"},
            "cpu.temperature_c":{"android_api"},
            "storage.available_bytes":{"filesystem"},
            "memory.available_mb":{"android_api"},
            "network.packet_loss_pct":{"network_probe","runner_network_probe"},
        }
        if self.parameter_key in trusted_source and self.source not in trusted_source[self.parameter_key]:
            raise ValueError(f"{self.parameter_key} requires a supported measurement source")
        if self.parameter_key == "thermal.status" and self.numeric_value is not None and (self.numeric_value != int(self.numeric_value) or self.numeric_value not in range(7)):
            raise ValueError("Android thermal status must be an integer from 0 through 6")
        if self.validity_status in {"unsupported", "unavailable"} and any(values):
            raise ValueError("unsupported or unavailable measurements must not contain a value")
        return self


class RunInput(BaseModel):
    project_id: int
    app_build_id: int | None = None
    profile_version_ids: list[int] = Field(default_factory=list, max_length=20)
    scenario_version_ids: list[int] = Field(default_factory=list, max_length=50)
    requested_repetitions: int = Field(default=1, ge=1, le=100)
    execution_mode: Literal["runner", "actual_device"] = "runner"
    actual_device_id: int | None = None

    @model_validator(mode="after")
    def actual_device_required(self):
        if self.execution_mode == "actual_device" and self.actual_device_id is None:
            raise ValueError("actual_device_id is required for actual_device runs")
        if self.execution_mode == "actual_device" and not self.profile_version_ids:
            raise ValueError("actual_device runs must select at least one condition profile version")
        if self.execution_mode != "actual_device" and self.actual_device_id is not None:
            raise ValueError("actual_device_id is only valid for actual_device runs")
        return self


class FindingInput(BaseModel):
    agent_name: str = Field(min_length=1, max_length=160)
    authentication_identity: str = Field(min_length=1, max_length=255)
    run_scenario_id: int | None = None
    finding_type: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=240)
    description: str | None = None
    severity: Literal["info", "low", "medium", "high", "critical"] = "info"
    confidence: float | None = Field(default=None, ge=0, le=1)
    expected_behavior: str | None = None
    actual_behavior: str | None = None
    reproduction_steps: list[Any] = Field(default_factory=list)
    evidence_reference: str | None = None


class ClaimInput(BaseModel):
    agent_name: str = Field(min_length=1, max_length=160)
    authentication_identity: str = Field(min_length=1, max_length=255)


class HeartbeatInput(ClaimInput):
    status: Literal["online", "busy"] = "online"


class BatchMetrics(BaseModel):
    agent_name: str = Field(min_length=1, max_length=160)
    authentication_identity: str = Field(min_length=1, max_length=255)
    samples: list[MeasurementInput] = Field(min_length=1, max_length=1000)


class ImpactInput(BaseModel):
    monthly_active_users: int = Field(ge=0)
    condition_group: str = Field(min_length=1, max_length=120)
    population_share: float = Field(ge=0, le=1)
    observed_failure_count: int = Field(ge=0)
    total_test_attempts: int = Field(gt=0)
    data_source: str = Field(min_length=1, max_length=255)
    assumptions: list[str] = Field(default_factory=list)
    is_synthetic: bool = False
    mutually_exclusive: bool = False

    @model_validator(mode="after")
    def count_valid(self):
        if self.observed_failure_count > self.total_test_attempts:
            raise ValueError("failure count cannot exceed total attempts")
        return self
