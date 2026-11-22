"""emulator execution environments, condition applications and lifecycle events

Revision ID: b7e41c2a9d10
Revises: 68a35c52625d
Create Date: 2026-11-22 06:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = "b7e41c2a9d10"
down_revision = "68a35c52625d"
branch_labels = None
depends_on = None

ENVIRONMENT_STATUSES = (
    "PREPARED", "QUEUED", "PREFLIGHT", "PROVISIONING", "BOOTING", "READY", "INSTALLING",
    "APPLYING_CONDITIONS", "VERIFYING_CONDITIONS", "EXECUTING", "COLLECTING_METRICS", "ANALYZING",
    "COMPLETED", "FAILED", "CANCEL_REQUESTED", "CLEANING", "CLEANED",
)
CONDITION_APPLICATION_STATUSES = (
    "applied_verified", "applied_unverified", "emulated", "configured", "unsupported",
    "requires_physical_device", "requires_linux_gateway", "failed", "reverted",
)


def _in(column, values):
    return f"{column} IN ({','.join(repr(v) for v in values)})"


def upgrade():
    op.create_table(
        "execution_environments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("test_run_id", sa.Integer(), nullable=False),
        sa.Column("environment_type", sa.String(length=24), nullable=False),
        sa.Column("emulator_name", sa.String(length=120), nullable=True),
        sa.Column("emulator_serial", sa.String(length=64), nullable=True),
        sa.Column("android_api_level", sa.Integer(), nullable=True),
        sa.Column("system_image", sa.String(length=160), nullable=True),
        sa.Column("device_id", sa.Integer(), nullable=True),
        sa.Column("requested_configuration_json", sa.JSON(), nullable=False),
        sa.Column("preflight_json", sa.JSON(), nullable=True),
        sa.Column("effective_configuration_json", sa.JSON(), nullable=True),
        sa.Column("capability_report_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("status_message", sa.Text(), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.Column("active_run_scenario_id", sa.Integer(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleanup_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleanup_verified", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("environment_type IN ('emulator','physical_device')", name="ck_execution_environments_type"),
        sa.CheckConstraint(_in("status", ENVIRONMENT_STATUSES), name="ck_execution_environments_status"),
        sa.CheckConstraint("outcome IS NULL OR outcome IN ('completed','failed','canceled')", name="ck_execution_environments_outcome"),
        sa.ForeignKeyConstraint(["test_run_id"], ["test_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("test_run_id"),
    )
    op.create_index("ix_execution_environments_status", "execution_environments", ["status"], unique=False)

    op.create_table(
        "condition_applications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("environment_id", sa.Integer(), nullable=False),
        sa.Column("parameter_key", sa.String(length=120), nullable=False),
        sa.Column("requested_value", sa.JSON(), nullable=True),
        sa.Column("applied_value", sa.JSON(), nullable=True),
        sa.Column("observed_value", sa.JSON(), nullable=True),
        sa.Column("unit", sa.String(length=40), nullable=True),
        sa.Column("application_status", sa.String(length=32), nullable=False),
        sa.Column("mechanism", sa.String(length=200), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_in("application_status", CONDITION_APPLICATION_STATUSES), name="ck_condition_applications_status"),
        sa.ForeignKeyConstraint(["environment_id"], ["execution_environments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_condition_applications_environment_id", "condition_applications", ["environment_id"], unique=False)
    op.create_index("ix_condition_env_parameter", "condition_applications", ["environment_id", "parameter_key"], unique=False)

    op.create_table(
        "environment_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("environment_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("data_json", sa.JSON(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("severity IN ('debug','info','warning','error')", name="ck_environment_events_severity"),
        sa.ForeignKeyConstraint(["environment_id"], ["execution_environments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_environment_events_environment_id", "environment_events", ["environment_id"], unique=False)
    op.create_index("ix_environment_events_env_id", "environment_events", ["environment_id", "id"], unique=False)

    # Additive, nullable columns only: existing rows and endpoints keep working unchanged.
    op.add_column("run_scenarios", sa.Column("result_json", sa.JSON(), nullable=True))
    op.add_column("metric_samples", sa.Column("measurement_status", sa.String(length=16), nullable=True))
    op.add_column("metric_samples", sa.Column("collection_method", sa.String(length=160), nullable=True))
    op.add_column("runner_agents", sa.Column("capability_report_json", sa.JSON(), nullable=True))
    op.add_column("runner_agents", sa.Column("capability_reported_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    with op.batch_alter_table("runner_agents") as batch:
        batch.drop_column("capability_reported_at")
        batch.drop_column("capability_report_json")
    with op.batch_alter_table("metric_samples") as batch:
        batch.drop_column("collection_method")
        batch.drop_column("measurement_status")
    with op.batch_alter_table("run_scenarios") as batch:
        batch.drop_column("result_json")
    op.drop_index("ix_environment_events_env_id", table_name="environment_events")
    op.drop_index("ix_environment_events_environment_id", table_name="environment_events")
    op.drop_table("environment_events")
    op.drop_index("ix_condition_env_parameter", table_name="condition_applications")
    op.drop_index("ix_condition_applications_environment_id", table_name="condition_applications")
    op.drop_table("condition_applications")
    op.drop_index("ix_execution_environments_status", table_name="execution_environments")
    op.drop_table("execution_environments")
