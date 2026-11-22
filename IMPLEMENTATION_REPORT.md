# PocketLab real Android execution: implementation report

**Status:** the real execution path is implemented end to end in code. It has **not yet been run against a real
Android Emulator**: the machine it was built on has no Android SDK, adb, Java, Node.js or PostgreSQL. The acceptance
journey therefore still has to be demonstrated on a host with the SDK (see "How to prove it" below). Nothing in the
product falls back to simulated results when the SDK is missing; preflight blocks the run and says why.

## What was verified, and how

| Check | Result | Proves |
|---|---|---|
| Original backend test suite | 20/20 pass (one count updated: 12 → 14 scenarios) | Existing behaviour preserved |
| `tests/test_environment_api.py` (real FastAPI + SQLAlchemy) | 15/15 pass | Capability reporting, preflight blocking, prepare/execute gating, legacy runners never get Android runs, state-machine enforcement, requested/applied/observed records, `CONFIGURED`-as-measurement rejection, device/provenance checks, artifact upload + PNG sniffing + authenticated download, cancel → worker cleanup → acknowledge, lease-expiry requeue, non-retryable failures |
| `tests/test_emulator_mocked.py` (**mocked**: scripted adb output) | 27/27 pass | Parsers, safety rules (allowlist, quoting, console grammar, AVD confinement, storage reserve), AVD/launch config, binary manifest parser, storage fill/release logic, honest thermal statuses, netem arguments, scenario pass/fail/blocked logic, lifecycle cleanup on failure and cancel, host lock. **Not evidence that a real emulator works.** |
| `tests/integration/test_real_emulator.py` | Skipped (no SDK) | Full real lifecycle; run it on an SDK host |
| Alembic `upgrade head` → `downgrade` → `upgrade` (SQLite), `alembic check` | Pass, "No new upgrade operations detected" | Migration matches ORM. **Not run on PostgreSQL here.** |
| Seed twice | Idempotent; 14 scenarios, 26 versions | Catalog upgrade path |
| `python -m app.workers.emulator_runner --capabilities` on this host | Runs; correctly reports SDK/adb/emulator/acceleration unavailable | Capability detection does not invent support |
| Frontend type check (VS Code's bundled TypeScript, project strict flags, third-party packages shimmed) | No errors against PocketLab's own types | Internal type consistency only. **`npm run build`, ESLint and a browser run were not possible (no Node.js).** |

## Condition-by-condition status

"Applied" means the worker changes the environment *and* reads the effect back from inside Android.

| Condition | Emulator | Physical device | Mechanism / evidence |
|---|---|---|---|
| Android version | Applied & verified | Observed only | System image; `getprop ro.build.version.sdk` |
| Guest RAM (e.g. 2 GB) | Applied & verified | Observed only | `hw.ramSize` + `-memory`; `/proc/meminfo` MemTotal within 75–102 % |
| CPU cores, screen, VM heap | Applied & verified | Observed only | AVD config + launch flags; `/sys/.../cpu/online`, `wm size/density`, `getprop dalvik.vm.heapsize` |
| Data partition size | Applied & verified | Observed only | `disk.dataPartition.size` on wiped userdata; `statfs /data` |
| Free storage target (e.g. 400 MB) | Applied & verified | Applied & verified | Bounded allocation files, statfs after each chunk, safety reserve, removal verified |
| App storage quota / cache limit | Unsupported | Unsupported | Android has no per-app quota control over ADB |
| Memory pressure | Applied & verified (rootable image) | Unsupported | Size-capped tmpfs + `/dev/urandom` as root, MemAvailable watchdog |
| Bandwidth, latency | Applied & verified | Unsupported | Emulator console `network speed/delay`, read back with `network status` |
| Jitter | **Emulated** (delay range) | Unsupported | Measured RTT spread reported separately |
| Packet loss, burst loss, duplication, reordering, DNS failure | **Requires Linux gateway** | Unsupported | `tc netem` in a per-run namespace (implemented, untested on real Linux) |
| Offline / online | Applied & verified | Applied & verified | `svc wifi/data` (+ emulator `gsm data`); `dumpsys connectivity` |
| Network timeline replay | Applied, each transition verified and timestamped | Partial (online/offline only) | Background replay during "Product image loading" |
| Battery level / charging / health | **Emulated** | **Emulated** (OS override, reset) | Console `power …` / `dumpsys battery set`; read back from `dumpsys battery` |
| Battery saver | Applied & verified (real OS mode) | Applied & verified | `cmd power set-mode` / `settings`; `dumpsys power` |
| Thermal status | **Emulated** (virtual HAL) | Actual | `dumpsys thermalservice` |
| CPU / skin / battery temperature | Unsupported (never faked) | Actual if the HAL exposes it | Battery temperature is never reused as CPU temperature |
| Thermal headroom | Unavailable | Unavailable | Needs an on-device helper app |
| Thermal throttling validation | **Requires physical device** | Sustained workload with sampling | — |

## Measurements

Every sample stores parameter, value, unit, source, UTC timestamp, device id, run id, validity, `measurement_status`
(`ACTUAL`/`EMULATED`/`UNSUPPORTED`/`UNAVAILABLE`/`INVALID`) and collection method. `CONFIGURED` values are stored as
condition applications, never as measurements. Collected: device model/API/RAM/storage, startup (`am start -W`), TTID
(`Displayed`), TTFD (only if the app calls `reportFullyDrawn`), PSS/RSS/heap, CPU % (procfs deltas), frame percentiles,
slow and frozen frames (gfxinfo histogram), a Perfetto trace (API 28+), crashes/ANRs/native crashes (logcat),
process restarts/kills, ICMP RTT/loss/jitter (`UNAVAILABLE` when the emulator drops ICMP), DNS-failure and timeout
counts from app logs (a lower bound), battery and thermal readings. Throughput is not measured (no in-guest HTTP
client).

## Deliberate deviations from the brief

* **UI automation** uses the on-device UIAutomator framework through adb (hierarchy dump + input), not an Appium
  server. This means fewer moving parts for the hackathon; Appium can be added behind the same driver interface.
* **Macrobenchmark** is reported unsupported: it needs a benchmark module built with the app, not an uploaded APK.
* **`POST/GET /test-runs/{id}/measurements` were not added.** They would duplicate the existing runner-only
  `/runner/jobs/{id}/metrics/batch` and `GET /test-runs/{id}/metrics` (now including status and method). Users cannot
  post measurements, which keeps fabricated results out.
* `execution_mode` keeps its values; the new `execution_environments.environment_type` (`emulator`/`physical_device`)
  carries the distinction, which avoids rewriting an unnamed CHECK constraint.
* Android-environment runs execute each scenario once (`requested_repetitions` must be 1); queue separate runs to
  repeat, each on a fresh device.
* Build approval is an operator CLI (`scripts/approve_build.py`), not an endpoint, so users cannot approve their own
  uploads.

## Assumptions to confirm on the first real run

These are the points where output formats vary by emulator or Android version. In each case the code fails
toward "not verified", never toward a false "verified".

1. Emulator console `network status` output format (otherwise shaping is recorded as `applied_unverified`).
2. `dumpsys connectivity` "Active default network" line (API 29+; otherwise offline is `applied_unverified`).
3. `cmd power set-mode` availability (falls back to `settings put global low_power`).
4. Whether the emulator forwards ICMP (otherwise network probes are `UNAVAILABLE`).
5. `fallocate` in toybox (falls back to `dd`).
6. The perfetto short-form command line on the chosen API level.

## How to prove it (acceptance)

On a host with the SDK and acceleration (see `backend/docs/ANDROID_WORKER.md`):

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.workers.emulator_runner --capabilities   # emulator/adb/virtualization: available
$env:POCKETLAB_REAL_EMULATOR="1"; $env:POCKETLAB_TEST_APK="C:\path\team-demo.apk"; $env:POCKETLAB_TEST_PACKAGE="com.team.demo"
.\.venv\Scripts\python.exe -m pytest -m real_emulator -s tests/integration   # real AVD lifecycle
```

Then run the dashboard journey (upload → approve → `ENTRY_LEVEL_CONSTRAINED` → preflight → confirm → start), with
PostgreSQL as the database and the frontend built with `npm run build`. Only after that journey succeeds should the
acceptance criteria be called complete.

## Files

* `backend/app/emulator/`: orchestrator package (schemas, planner, capability detector, SDK/AVD/ADB/console managers,
  storage/memory/network/battery/thermal managers, APK manifest parser and installer, scenario executor + 14 JSON
  scenarios, measurement collector, monitor, cleanup, lifecycle manager).
* `backend/app/workers/emulator_runner.py`: the worker process.
* `backend/app/routers/environment.py`, `app/services/environment.py`, `app/schemas/environment.py`: user API.
* `backend/app/routers/runner.py`: environment-aware claim plus worker reporting endpoints (existing behaviour kept).
* `backend/migrations/versions/b7e41c2a9d10_*.py`: 3 tables + 5 nullable columns.
* `backend/scripts/approve_build.py`, `backend/docs/ANDROID_WORKER.md`.
* `frontend/src`: `environment.ts`, `components/EnvironmentConfigurator.tsx`, `EnvironmentPanels.tsx`,
  `EnvironmentReport.tsx`, `Environment.css`, updated `ThoughtLine.tsx`, `TestLabPage.tsx`, `LiveRunPage.tsx`,
  `RunDetailsPage.tsx`, `types.ts`, `services/api.ts`.
