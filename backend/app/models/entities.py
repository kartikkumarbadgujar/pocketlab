from datetime import datetime, timezone
from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    application_package: Mapped[str | None] = mapped_column(String(255))
    __table_args__ = (UniqueConstraint("owner_user_id", "name"),)


class AppBuild(Base):
    __tablename__ = "app_builds"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version_name: Mapped[str | None] = mapped_column(String(100))
    version_code: Mapped[int | None] = mapped_column(Integer)
    package_name: Mapped[str] = mapped_column(String(255), nullable=False)
    apk_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    apk_storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    scan_status: Mapped[str] = mapped_column(String(32), default="quarantined", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint("scan_status IN ('quarantined','scanning','approved','rejected')"),)


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    device_name: Mapped[str] = mapped_column(String(120), nullable=False)
    manufacturer: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(120))
    android_version: Mapped[str | None] = mapped_column(String(64))
    api_level: Mapped[int | None] = mapped_column(Integer)
    cpu_architecture: Mapped[str | None] = mapped_column(String(64))
    cpu_model: Mapped[str | None] = mapped_column(String(160))
    cpu_core_count: Mapped[int | None] = mapped_column(Integer)
    gpu_model: Mapped[str | None] = mapped_column(String(160))
    total_ram_mb: Mapped[int | None] = mapped_column(Integer)
    total_storage_bytes: Mapped[int | None] = mapped_column(BigInteger)
    display_width: Mapped[int | None] = mapped_column(Integer)
    display_height: Mapped[int | None] = mapped_column(Integer)
    refresh_rate_hz: Mapped[float | None] = mapped_column(Float)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConditionProfile(Base):
    __tablename__ = "condition_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    profile_type: Mapped[str] = mapped_column(String(40), nullable=False)
    is_public_template: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConditionProfileVersion(Base):
    __tablename__ = "condition_profile_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("condition_profiles.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    configuration_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("profile_id", "version"), CheckConstraint("version > 0"))


class MeasurementDefinition(Base):
    __tablename__ = "measurement_definitions"
    id: Mapped[int] = mapped_column(primary_key=True)
    parameter_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    data_type: Mapped[str] = mapped_column(String(20), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(40))
    minimum_value: Mapped[float | None] = mapped_column(Float)
    maximum_value: Mapped[float | None] = mapped_column(Float)
    supported_sources: Mapped[list] = mapped_column(JSON, default=list)
    is_configurable: Mapped[bool] = mapped_column(Boolean, default=False)
    is_observation_only: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConditionProfileParameter(Base):
    __tablename__ = "condition_profile_parameters"
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_version_id: Mapped[int] = mapped_column(ForeignKey("condition_profile_versions.id", ondelete="CASCADE"), index=True)
    parameter_definition_id: Mapped[int] = mapped_column(ForeignKey("measurement_definitions.id", ondelete="RESTRICT"))
    numeric_value: Mapped[float | None] = mapped_column(Float)
    text_value: Mapped[str | None] = mapped_column(Text)
    boolean_value: Mapped[bool | None] = mapped_column(Boolean)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("profile_version_id", "parameter_definition_id"), CheckConstraint("(numeric_value IS NOT NULL AND text_value IS NULL AND boolean_value IS NULL) OR (numeric_value IS NULL AND text_value IS NOT NULL AND boolean_value IS NULL) OR (numeric_value IS NULL AND text_value IS NULL AND boolean_value IS NOT NULL)"))


class TestScenario(Base):
    __tablename__ = "test_scenarios"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(180), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    scenario_category: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScenarioVersion(Base):
    __tablename__ = "scenario_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("test_scenarios.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("scenario_id", "version"), CheckConstraint("version > 0"))


class ScenarioStep(Base):
    __tablename__ = "scenario_steps"
    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_version_id: Mapped[int] = mapped_column(ForeignKey("scenario_versions.id", ondelete="CASCADE"), index=True)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    action_type: Mapped[str] = mapped_column(String(80), nullable=False)
    action_configuration_json: Mapped[dict] = mapped_column(JSON, default=dict)
    expected_result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60)
    __table_args__ = (UniqueConstraint("scenario_version_id", "step_order"), CheckConstraint("step_order >= 0 AND timeout_seconds > 0"))


class TestRun(TimestampMixin, Base):
    __tablename__ = "test_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    app_build_id: Mapped[int | None] = mapped_column(ForeignKey("app_builds.id", ondelete="SET NULL"))
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False, index=True)
    execution_mode: Mapped[str] = mapped_column(String(20), default="runner", nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    requested_repetitions: Mapped[int] = mapped_column(Integer, default=1)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("created_by_user_id", "idempotency_key"), CheckConstraint("requested_repetitions BETWEEN 1 AND 100"), CheckConstraint("retry_count >= 0"), CheckConstraint("execution_mode IN ('runner','actual_device')"), CheckConstraint("status IN ('queued','claimed','running','completed','failed','canceled')"), Index("ix_test_runs_project_created", "project_id", "created_at"))


class RunProfile(Base):
    __tablename__ = "run_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    test_run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    profile_version_id: Mapped[int] = mapped_column(ForeignKey("condition_profile_versions.id", ondelete="RESTRICT"))
    actual_device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    __table_args__ = (UniqueConstraint("test_run_id", "profile_version_id"),)


class RunScenario(Base):
    __tablename__ = "run_scenarios"
    id: Mapped[int] = mapped_column(primary_key=True)
    test_run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    scenario_version_id: Mapped[int] = mapped_column(ForeignKey("scenario_versions.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("test_run_id", "scenario_version_id"),)


class MetricSample(Base):
    __tablename__ = "metric_samples"
    id: Mapped[int] = mapped_column(primary_key=True)
    test_run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    run_scenario_id: Mapped[int | None] = mapped_column(ForeignKey("run_scenarios.id", ondelete="SET NULL"))
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    parameter_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    numeric_value: Mapped[float | None] = mapped_column(Float)
    text_value: Mapped[str | None] = mapped_column(Text)
    boolean_value: Mapped[bool | None] = mapped_column(Boolean)
    unit: Mapped[str | None] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    sample_key: Mapped[str] = mapped_column(String(128), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    validity_status: Mapped[str] = mapped_column(String(24), default="valid", nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("test_run_id", "sample_key"), CheckConstraint("validity_status IN ('valid','unsupported','unavailable','invalid')"), Index("ix_metric_run_parameter_observed", "test_run_id", "parameter_key", "observed_at"))


class Finding(Base):
    __tablename__ = "findings"
    id: Mapped[int] = mapped_column(primary_key=True)
    test_run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    run_scenario_id: Mapped[int | None] = mapped_column(ForeignKey("run_scenarios.id", ondelete="SET NULL"))
    finding_type: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20), default="info")
    confidence: Mapped[float | None] = mapped_column(Float)
    expected_behavior: Mapped[str | None] = mapped_column(Text)
    actual_behavior: Mapped[str | None] = mapped_column(Text)
    reproduction_steps_json: Mapped[list] = mapped_column(JSON, default=list)
    evidence_reference: Mapped[str | None] = mapped_column(String(512))
    estimated_affected_users: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvidenceArtifact(Base):
    __tablename__ = "evidence_artifacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    test_run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    artifact_type: Mapped[str] = mapped_column(String(40), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RunnerAgent(Base):
    __tablename__ = "runner_agents"
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    authentication_identity: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="offline", nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RunnerLease(Base):
    __tablename__ = "runner_leases"
    id: Mapped[int] = mapped_column(primary_key=True)
    test_run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), unique=True)
    runner_agent_id: Mapped[int] = mapped_column(ForeignKey("runner_agents.id", ondelete="CASCADE"), index=True)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(120))
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("user_id", "key"),)


class ImpactEstimate(Base):
    __tablename__ = "impact_estimates"
    id: Mapped[int] = mapped_column(primary_key=True)
    test_run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    monthly_active_users: Mapped[int] = mapped_column(Integer, nullable=False)
    condition_group: Mapped[str] = mapped_column(String(120), nullable=False)
    population_share: Mapped[float] = mapped_column(Float, nullable=False)
    observed_failure_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_test_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_failure_rate: Mapped[float] = mapped_column(Float, nullable=False)
    estimated_affected_users: Mapped[float] = mapped_column(Float, nullable=False)
    uncertainty_json: Mapped[dict] = mapped_column(JSON, default=dict)
    data_source: Mapped[str] = mapped_column(String(255), nullable=False)
    assumptions: Mapped[list] = mapped_column(JSON, default=list)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("test_run_id", "condition_group", name="uq_impact_run_condition"), CheckConstraint("population_share >= 0 AND population_share <= 1"), CheckConstraint("observed_failure_count >= 0 AND total_test_attempts > 0 AND observed_failure_count <= total_test_attempts"))
