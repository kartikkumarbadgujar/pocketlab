"""Typed failures for the Android execution environment.

Each exception carries a stable ``code`` so the worker can report a precise reason to the
API and the dashboard can explain why a run stopped, rather than showing a generic error.
"""


class EmulatorError(Exception):
    code = "emulator_error"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class PreflightError(EmulatorError):
    """Actual execution cannot be performed on this host (SDK missing, no image, no acceleration...)."""
    code = "preflight_failed"


class SdkNotFoundError(PreflightError):
    code = "android_sdk_not_found"


class CommandNotAllowedError(EmulatorError):
    """A subprocess invocation was rejected by the allowlist. Never retried."""
    code = "command_not_allowed"


class CommandFailedError(EmulatorError):
    code = "command_failed"


class CommandTimeoutError(EmulatorError):
    code = "command_timeout"


class ResourceUnavailableError(EmulatorError):
    """Host RAM, disk, CPU or emulator ports are insufficient, or another emulator job holds the lock."""
    code = "host_resources_unavailable"


class BootTimeoutError(EmulatorError):
    code = "emulator_boot_timeout"


class DeviceNotReadyError(EmulatorError):
    code = "device_not_ready"


class ConditionApplicationError(EmulatorError):
    """A requested condition could not be applied or verified safely."""
    code = "condition_application_failed"


class UnsafeConditionError(ConditionApplicationError):
    """The requested condition would destabilise Android (e.g. filling storage below the reserve)."""
    code = "unsafe_condition"


class ApkValidationError(EmulatorError):
    code = "apk_validation_failed"


class InstallError(EmulatorError):
    code = "apk_install_failed"


class ScenarioDefinitionError(EmulatorError):
    code = "invalid_scenario_definition"


class CancelledError(EmulatorError):
    """The user requested cancellation; the worker must clean up and acknowledge."""
    code = "canceled"
