export type User = { id: number; email: string }
export type TokenPair = { access_token: string; refresh_token: string; token_type: 'bearer' }
export type Project = { id: number; owner_user_id: number; name: string; description: string | null; application_package: string | null; created_at: string; updated_at: string }
export type Device = { id: number; owner_user_id: number; device_name: string; manufacturer: string | null; model: string | null; android_version: string | null; api_level: number | null; cpu_architecture: string | null; cpu_model: string | null; cpu_core_count: number | null; gpu_model: string | null; total_ram_mb: number | null; total_storage_bytes: number | null; display_width: number | null; display_height: number | null; refresh_rate_hz: number | null; last_seen_at: string | null; created_at: string }
export type Profile = { id: number; owner_user_id: number | null; name: string; description: string | null; profile_type: string; is_public_template: boolean; created_at: string }
export type ProfileVersion = { id: number; profile_id: number; version: number; configuration_json: Record<string, unknown>; created_at: string }
export type Scenario = { id: number; name: string; description: string | null; scenario_category: string; created_at: string }
export type ScenarioStep = { step_order: number; action_type: string; configuration: Record<string, unknown>; expected: Record<string, unknown>; timeout_seconds: number }
export type ScenarioVersion = { id: number; scenario_id: number; version: number; created_at: string }
export type ScenarioDetail = { scenario: Scenario; versions: ScenarioVersion[] }
export type BuildStatus = 'quarantined' | 'scanning' | 'approved' | 'rejected'
export type Build = { id: number; project_id: number; version_name: string | null; version_code: number | null; package_name: string; apk_sha256: string; apk_storage_key: string; scan_status: BuildStatus; created_at: string }
export type BuildUpload = { id: number; project_id: number; version_name: string | null; version_code: number | null; package_name: string; apk_sha256: string; size_bytes: number; scan_status: BuildStatus }
export type TestRunStatus = 'queued' | 'claimed' | 'running' | 'completed' | 'failed' | 'canceled'
export type TestRun = { id: number; project_id: number; app_build_id: number | null; created_by_user_id: number; status: TestRunStatus; execution_mode: 'runner' | 'actual_device'; idempotency_key: string | null; requested_repetitions: number; retry_count: number; started_at: string | null; completed_at: string | null; created_at: string; updated_at: string; error_message: string | null }
export type MeasurementValidity = 'valid' | 'unsupported' | 'unavailable' | 'invalid'
export type MeasurementStatus = 'ACTUAL' | 'EMULATED' | 'UNSUPPORTED' | 'UNAVAILABLE' | 'INVALID'
export type Metric = { id: number; test_run_id: number; run_scenario_id: number | null; device_id: number | null; parameter_key: string; numeric_value: number | null; text_value: string | null; boolean_value: boolean | null; unit: string | null; source: string; sample_key: string; observed_at: string; validity_status: MeasurementValidity; measurement_status?: MeasurementStatus | null; collection_method?: string | null; metadata_json: Record<string, unknown> }
export type FindingSeverity = 'info' | 'low' | 'medium' | 'high' | 'critical'
export type Finding = { id: number; test_run_id: number; run_scenario_id: number | null; finding_type: string; title: string; description: string | null; severity: FindingSeverity; confidence: number | null; expected_behavior: string | null; actual_behavior: string | null; reproduction_steps_json: unknown[]; evidence_reference: string | null; estimated_affected_users: number | null; created_at: string }
export type Artifact = { id: number; test_run_id: number; artifact_type: string; storage_key: string; filename: string; sha256: string; size_bytes: number; content_type: string; created_at: string }
export type ImpactEstimate = { id: number; test_run_id: number; monthly_active_users: number; condition_group: string; population_share: number; observed_failure_count: number; total_test_attempts: number; estimated_failure_rate: number; estimated_affected_users: number; uncertainty_json: { method: string; failure_rate_95ci: [number, number]; affected_users_95ci: [number, number] }; data_source: string; assumptions: string[]; is_synthetic: boolean; created_at: string }

// Android execution environment types
export type EnvironmentType = 'emulator' | 'physical_device'
export type EnvironmentStatus = 'PREPARED' | 'QUEUED' | 'PREFLIGHT' | 'PROVISIONING' | 'BOOTING' | 'READY' | 'INSTALLING' | 'APPLYING_CONDITIONS' | 'VERIFYING_CONDITIONS' | 'EXECUTING' | 'COLLECTING_METRICS' | 'ANALYZING' | 'COMPLETED' | 'FAILED' | 'CANCEL_REQUESTED' | 'CLEANING' | 'CLEANED'
export type ApplicationStatus = 'applied_verified' | 'applied_unverified' | 'emulated' | 'configured' | 'unsupported' | 'requires_physical_device' | 'requires_linux_gateway' | 'failed' | 'reverted'
export type PlanOutcome = 'will_apply' | 'emulated' | 'configured_only' | 'unsupported' | 'requires_physical_device' | 'requires_linux_gateway' | 'requires_rootable_image' | 'not_requested'
export type Selector = { resource_id?: string; text?: string; text_contains?: string; content_desc?: string }
export type NetworkCondition = { online: boolean; download_kbps: number | null; upload_kbps: number | null; latency_ms: number | null; jitter_ms: number | null; packet_loss_pct: number | null; duplication_pct: number | null; reorder_pct: number | null; dns_failure: boolean }
export type TimelineStage = { start_s: number; end_s: number; label: string; condition: Partial<NetworkCondition> }
export type EnvironmentRequest = {
  environment_type: EnvironmentType
  device: { api_level: number; ram_mb: number; cpu_cores: number; data_partition_mb: number; sdcard_mb: number; vm_heap_mb: number | null; screen: { width_px: number; height_px: number; density_dpi: number } }
  storage: { free_target_mb: number | null }
  memory: { pressure_enabled: boolean; target_available_mb: number | null }
  network: NetworkCondition & { timeline: TimelineStage[]; seed: number | null }
  battery: { level_pct: number | null; charging: boolean | null; ac_connected: boolean | null; saver_enabled: boolean | null }
  thermal: { observe: boolean; require_physical_validation: boolean; sustained_workload_seconds: number }
  scenario_bindings: Record<string, Selector>
  grant_runtime_permissions: boolean
  strict_conditions: boolean
}
export type SystemImage = { package: string; api_level: number; tag: string; abi: string; rootable: boolean }
export type CapabilityDetail = { status: string; reason: string | null; details: Record<string, unknown> }
export type CapabilityWorker = { agent_name: string; last_seen_at: string | null; reported_at: string; summary: Record<string, string>; host: Record<string, string | number | null>; system_images: SystemImage[]; physical_devices: { device_id: number; state: string }[]; details: Record<string, CapabilityDetail> }
export type CapabilitiesResponse = { status: 'available' | 'no_worker_online'; workers: CapabilityWorker[]; message?: string }
export type PlanItem = { parameter_key: string; requested_value: unknown; unit: string | null; outcome: PlanOutcome; mechanism: string | null; reason: string | null }
export type PreflightResult = { ok: boolean; environment_type: EnvironmentType; blocking_errors: string[]; warnings: string[]; items: PlanItem[]; selected_system_image: SystemImage | null; capability_summary: Record<string, string>; capability_report_at: string | null }
export type ConditionApplication = { id: number; parameter_key: string; requested_value: unknown; applied_value: unknown; observed_value: unknown; unit: string | null; application_status: ApplicationStatus; mechanism: string | null; evidence: Record<string, unknown>; attempt: number; applied_at: string; verified_at: string | null }
export type StepExpectation = { name: string; expected: unknown; actual: unknown; passed: boolean }
export type StepResult = { index: number; action: string; status: string; started_at: string; duration_ms: number; expectations: StepExpectation[]; evidence: Record<string, unknown>; error: string | null }
export type ScenarioRunStatus = 'queued' | 'running' | 'passed' | 'failed' | 'blocked' | 'error' | 'inconclusive'
export type ScenarioRun = { run_scenario_id: number; scenario_version_id: number; version: number; name: string; category: string; status: ScenarioRunStatus; started_at: string | null; completed_at: string | null; result: { status?: string; reason?: string | null; steps?: StepResult[]; failures?: Record<string, unknown[]> } | null }
export type ExecutionEnvironment = {
  id: number; test_run_id: number; environment_type: EnvironmentType; status: EnvironmentStatus; status_message: string | null; outcome: 'completed' | 'failed' | 'canceled' | null; attempt: number
  emulator_name: string | null; emulator_serial: string | null; android_api_level: number | null; system_image: string | null; device_id: number | null
  requested_configuration: EnvironmentRequest; preflight: PreflightResult | null; effective_configuration: Record<string, unknown> | null; capability_report: Record<string, string> | null
  active_run_scenario_id: number | null; cancel_requested_at: string | null; cleanup_requested_at: string | null; cleanup_verified: boolean | null
  created_at: string; started_at: string | null; stopped_at: string | null; updated_at: string
  conditions: ConditionApplication[]; scenarios: ScenarioRun[]
}
export type EnvironmentEvent = { id: number; event_type: string; status: EnvironmentStatus | null; message: string; severity: 'debug' | 'info' | 'warning' | 'error'; data: Record<string, unknown>; attempt: number; event_timestamp: string }
