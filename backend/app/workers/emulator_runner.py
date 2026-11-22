"""PocketLab Android execution worker.

Runs on a machine with the Android SDK and hardware acceleration (your laptop for the hackathon, a dedicated
Linux VM/host in production). It connects *outward* to the PocketLab API with RUNNER_TOKEN, claims queued
Android-environment runs, executes them in disposable emulators (or authorized physical devices) and reports
lifecycle events, condition applications, measurements, findings and evidence.

    python -m app.workers.emulator_runner                # serve jobs until interrupted
    python -m app.workers.emulator_runner --capabilities # print this host's capability report and exit
    python -m app.workers.emulator_runner --sweep        # remove orphaned PocketLab emulators/AVDs and exit
    python -m app.workers.emulator_runner --once         # process at most one job, then exit
"""
from __future__ import annotations

import argparse
import itertools
import json
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from app.emulator.capability_detector import CapabilityDetector
from app.emulator.emulator_config import WorkerSettings
from app.emulator.emulator_manager import EmulatorManager, RunJob
from app.emulator.exceptions import (
    ApkValidationError, ConditionApplicationError, EmulatorError, InstallError, PreflightError, ScenarioDefinitionError,
)
from app.emulator.schemas import MEASUREMENT_TO_VALIDITY, ConditionRecord, EnvironmentStatus, EventRecord, MeasurementRecord
from app.utils.logging import configure_logging

logger = logging.getLogger("pocketlab.worker")
NON_RETRYABLE = (PreflightError, ApkValidationError, InstallError, ConditionApplicationError, ScenarioDefinitionError)


class LeaseLostError(EmulatorError):
    code = "lease_lost"


class HttpReporter:
    """Implements emulator_manager.Reporter over the authenticated runner API."""

    def __init__(self, client: httpx.Client, settings: WorkerSettings, run_id: int, attempt: int):
        self.client, self.settings, self.run_id, self.attempt = client, settings, run_id, attempt
        self.identity = {"agent_name": settings.agent_name, "authentication_identity": settings.authentication_identity}
        self._seq = itertools.count(1)

    def _request(self, method: str, path: str, **kwargs) -> dict:
        for attempt in range(3):
            try:
                response = self.client.request(method, f"/runner/jobs/{self.run_id}{path}", **kwargs)
            except httpx.TransportError:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
                continue
            if response.status_code in (404, 409) and "lease" in response.text.lower():
                raise LeaseLostError(f"Runner lease lost: {response.text[:200]}")
            if response.status_code >= 500 and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            response.raise_for_status()
            return response.json() if response.content else {}
        return {}

    def set_status(self, status: EnvironmentStatus, message: str, **fields: Any) -> dict:
        body = {**self.identity, "status": status.value, "message": message[:2000],
                **{k: v for k, v in fields.items() if k in ("emulator_name", "emulator_serial", "android_api_level", "system_image",
                                                            "effective_configuration", "active_run_scenario_id", "cleanup_verified")}}
        return self._request("PUT", "/environment", content=json.dumps(body, default=str), headers={"Content-Type": "application/json"})

    def event(self, event_type: str, message: str, severity: str = "info", data: dict | None = None) -> None:
        record = EventRecord(event_type=event_type[:60], message=message[:4000] or event_type, severity=severity, data=data or {})
        self._request("POST", "/environment/events", content=json.dumps({**self.identity, "events": [record.model_dump(mode="json")]}, default=str),
                      headers={"Content-Type": "application/json"})

    def conditions(self, records: list[ConditionRecord]) -> None:
        if records:
            payload = {**self.identity, "conditions": [r.model_dump(mode="json") for r in records]}
            self._request("POST", "/environment/conditions", content=json.dumps(payload, default=str), headers={"Content-Type": "application/json"})

    def _sample(self, record: MeasurementRecord, device_id: int) -> dict:
        value = record.value
        typed: dict = {"numeric_value": None, "text_value": None, "boolean_value": None}
        if isinstance(value, bool):
            typed["boolean_value"] = value
        elif isinstance(value, (int, float)):
            typed["numeric_value"] = float(value)
        elif value is not None:
            typed["text_value"] = str(value)
        return {**typed, "parameter_key": record.parameter_key, "unit": record.unit, "source": record.source,
                "sample_key": f"a{self.attempt}-{next(self._seq):06d}-{record.parameter_key}"[:128],
                "observed_at": record.observed_at.isoformat(), "validity_status": MEASUREMENT_TO_VALIDITY[record.status],
                "measurement_status": record.status.value, "collection_method": record.collection_method[:160],
                "device_id": device_id, "run_scenario_id": record.run_scenario_id,
                "metadata": {"reason": record.reason, "evidence": record.evidence} if (record.reason or record.evidence) else {}}

    def measurements(self, records: list[MeasurementRecord], device_id: int) -> None:
        samples = [self._sample(r, device_id) for r in records]
        for start in range(0, len(samples), 500):
            chunk = samples[start:start + 500]
            try:
                self._request("POST", "/metrics/batch", content=json.dumps({**self.identity, "samples": chunk}, default=str),
                              headers={"Content-Type": "application/json"})
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 422:
                    raise
                for sample in chunk:   # isolate the rejected sample instead of losing the batch
                    try:
                        self._request("POST", "/metrics/batch", content=json.dumps({**self.identity, "samples": [sample]}, default=str),
                                      headers={"Content-Type": "application/json"})
                    except httpx.HTTPStatusError as single:
                        self.event("measurement_rejected", f"{sample['parameter_key']} rejected: {single.response.text[:300]}", "warning")

    def finding(self, **fields: Any) -> None:
        self._request("POST", "/findings", content=json.dumps({**self.identity, **fields}, default=str), headers={"Content-Type": "application/json"})

    def artifact(self, path: Path, artifact_type: str, content_type: str) -> int | None:
        if not path.exists():
            return None
        if path.stat().st_size > self.settings.artifact_max_bytes:
            self.event("artifact_skipped", f"{path.name} exceeds the artifact size limit and was not uploaded", "warning")
            return None
        with path.open("rb") as handle:
            result = self._request("POST", "/artifacts", data={**self.identity, "artifact_type": artifact_type},
                                   files={"file": (path.name, handle, content_type)})
        return result.get("id")

    def scenario_result(self, run_scenario_id: int, status: str, result: dict) -> None:
        self._request("POST", f"/scenarios/{run_scenario_id}", content=json.dumps({**self.identity, "status": status, "result": result}, default=str),
                      headers={"Content-Type": "application/json"})

    def register_device(self, properties: dict) -> int:
        body = {**self.identity, **{k: v for k, v in properties.items() if v is not None}}
        return int(self._request("POST", "/environment/device", content=json.dumps(body, default=str), headers={"Content-Type": "application/json"})["device_id"])

    def heartbeat(self) -> bool:
        return bool(self._request("POST", "/heartbeat", json=self.identity).get("cancel_requested"))

    def download_apk(self, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            offset = 0
            while True:
                # 200 = whole file (disk storage); 206 = one range of a Blob-stored APK (serverless hosting).
                with self.client.stream("POST", f"/runner/jobs/{self.run_id}/apk", params={"offset": offset}, json=self.identity) as response:
                    if response.status_code not in (200, 206):
                        raise PreflightError(f"APK download failed with HTTP {response.status_code}")
                    received = 0
                    for chunk in response.iter_bytes(1024 * 1024):
                        handle.write(chunk)
                        received += len(chunk)
                    if response.status_code == 200:
                        break
                    total = int(response.headers.get("X-PocketLab-Total-Size", "0"))
                offset += received
                if received == 0 or offset >= total:
                    break
        return destination


class Worker:
    def __init__(self, settings: WorkerSettings):
        self.settings = settings
        self.manager = EmulatorManager(settings)
        self.detector = CapabilityDetector(settings)
        self.stop = False
        self.client = httpx.Client(base_url=settings.api_url, headers={"Authorization": f"Bearer {settings.runner_token}"}, timeout=120)
        self.identity = {"agent_name": settings.agent_name, "authentication_identity": settings.authentication_identity}

    def report_capabilities(self) -> dict:
        report, _, _ = self.detector.detect()
        self.client.post("/runner/heartbeat", json={**self.identity, "status": "online", "capability_report": report.model_dump(mode="json")}).raise_for_status()
        return report.summary()

    def claim(self) -> dict | None:
        types = ["emulator"] + (["physical_device"] if self.settings.physical_devices else [])
        response = self.client.post("/runner/jobs/claim", json={**self.identity, "environment_types": types,
                                                                "physical_device_ids": list(self.settings.physical_devices)})
        response.raise_for_status()
        return response.json().get("job")

    def finish(self, run_id: int, outcome: str, error: Exception | None) -> None:
        if outcome == "completed":
            self.client.post(f"/runner/jobs/{run_id}/complete", json=self.identity)
        elif outcome == "canceled":
            self.client.post(f"/runner/jobs/{run_id}/canceled", json=self.identity)
        else:
            message = getattr(error, "message", None) or str(error or "Worker reported failure")
            self.client.post(f"/runner/jobs/{run_id}/fail", json={**self.identity, "error_message": message[:2000],
                                                                  "retryable": not isinstance(error, NON_RETRYABLE)})

    def serve(self, once: bool = False) -> None:
        swept = self.manager.recover_orphans()
        if swept:
            logger.warning("orphans_recovered", extra={"count": len(swept)})
        last_report = 0.0
        while not self.stop:
            try:
                if time.monotonic() - last_report > 60:
                    summary = self.report_capabilities()
                    last_report = time.monotonic()
                    logger.info("capabilities_reported", extra={"emulator": summary.get("emulator"), "virtualization": summary.get("virtualization")})
                job = self.claim()
            except httpx.HTTPError as exc:
                logger.warning("api_unreachable", extra={"error": str(exc)})
                time.sleep(self.settings.poll_interval_s * 2)
                continue
            if not job:
                if once:
                    return
                time.sleep(self.settings.poll_interval_s)
                continue
            run_id = job["run_id"]
            try:
                run_job = RunJob.from_claim(job)
            except (EmulatorError, ValueError, KeyError) as exc:
                self.finish(run_id, "failed", PreflightError(f"Malformed job: {exc}"))
                continue
            reporter = HttpReporter(self.client, self.settings, run_id, run_job.attempt)
            try:
                outcome = self.manager.execute(run_job, reporter)
            except Exception:   # cleanup already ran inside execute(); keep serving other jobs
                logger.exception("job_execution_crashed", extra={"run_id": run_id})
                outcome = "failed"
            try:
                self.finish(run_id, outcome, self.manager.last_error)
            except httpx.HTTPError as exc:
                logger.error("finish_report_failed", extra={"error": str(exc)})
            last_report = 0.0   # refresh capabilities (host RAM etc.) after every job
            if once:
                return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PocketLab Android execution worker")
    parser.add_argument("--capabilities", action="store_true", help="print the capability report and exit")
    parser.add_argument("--sweep", action="store_true", help="remove orphaned PocketLab emulators/AVDs and exit")
    parser.add_argument("--once", action="store_true", help="process at most one job and exit")
    args = parser.parse_args(argv)
    try:   # reuse backend/.env (RUNNER_TOKEN, ANDROID_SDK_ROOT, ...) without overriding real environment variables
        from dotenv import load_dotenv
        load_dotenv(override=False)
    except ImportError:
        pass
    settings = WorkerSettings.from_env()
    configure_logging("INFO")
    if args.capabilities:
        report, _, _ = CapabilityDetector(settings).detect()
        print(json.dumps(report.model_dump(mode="json"), indent=2))
        return 0
    if args.sweep:
        print(json.dumps(EmulatorManager(settings).recover_orphans(), indent=2))
        return 0
    if len(settings.runner_token) < 32:
        print("RUNNER_TOKEN (>= 32 chars, same value as the API's) is required", file=sys.stderr)
        return 2
    worker = Worker(settings)

    def _stop(*_):
        worker.stop = True
    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)
    worker.serve(once=args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
