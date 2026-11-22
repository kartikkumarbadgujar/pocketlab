"""PocketLab Android execution environment.

Runs only in the separately deployed execution worker (``python -m app.workers.emulator_runner``), never inside
the FastAPI process. The API imports only the side-effect-free modules ``schemas`` and ``planner``.

Module map
----------
schemas               request model, lifecycle state machine, status vocabularies (shared with the API)
planner               preflight: request x capability report -> per-condition plan and blocking errors
capability_detector   real host checks (SDK, adb, emulator, acceleration, images, RAM/disk/CPU, netem)
emulator_config       worker settings, SDK discovery, AVD config + launch-argument generation
process               allowlisted subprocess execution (argument arrays, no shell, scrubbed env)
avd_manager           disposable AVDs confined to PocketLab's own ANDROID_AVD_HOME
adb_manager           device access over ADB
console_manager       emulator console (allowlisted command grammar)
environment_manager   apply / verify / revert every condition
storage_manager       free-space constraints with safety reserve
memory_manager        guest RAM verification, bounded memory pressure, app memory
network_manager       pluggable shaping (emulator console | Linux netem namespace), offline, probe, timeline
battery_manager       emulated battery state and the real OS battery saver
thermal_manager       honest thermal observation (emulated vs physical)
apk_manifest          binary AndroidManifest parser
apk_installer         verified APK installation
scenario_executor     versioned JSON scenarios with expectation validation
measurement_collector measurements with provenance
emulator_monitor      logcat capture, process watch, crash/ANR detection, heartbeat
emulator_cleanup      emulator shutdown, AVD/run-dir removal, orphan recovery
emulator_manager      lifecycle orchestration
"""
