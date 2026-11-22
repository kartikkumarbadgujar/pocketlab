"""Applies, verifies and reverts every requested condition on one device.

Each condition produces a ConditionRecord (requested / applied / observed / status / evidence). Conditions
the plan already marked unsupported are recorded as such - they are never silently dropped or faked.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from app.emulator.adb_manager import AdbManager
from app.emulator.battery_manager import BatteryManager
from app.emulator.console_manager import EmulatorConsole
from app.emulator.emulator_config import WorkerSettings
from app.emulator.exceptions import ConditionApplicationError, EmulatorError
from app.emulator.memory_manager import MemoryManager
from app.emulator.network_manager import Connectivity, EmulatorConsoleShaper, NetemGateway, NetworkManager, NetworkProbe
from app.emulator.schemas import ApplicationStatus as A, ConditionRecord, EnvironmentRequest, PreflightResult
from app.emulator.storage_manager import MB, StorageManager
from app.emulator.thermal_manager import ThermalManager

PLAN_TO_STATUS = {"unsupported": A.UNSUPPORTED, "requires_physical_device": A.REQUIRES_PHYSICAL_DEVICE,
                  "requires_linux_gateway": A.REQUIRES_LINUX_GATEWAY, "requires_rootable_image": A.UNSUPPORTED}


class EnvironmentManager:
    def __init__(self, request: EnvironmentRequest, adb: AdbManager, console: EmulatorConsole | None, settings: WorkerSettings, *,
                 emulator: bool, rooted: bool, gateway: NetemGateway | None, record: Callable[[ConditionRecord], None]):
        self.request, self.adb, self.console, self.emulator, self.record = request, adb, console, emulator, record
        self.storage = StorageManager(adb, reserve_mb=settings.storage_reserve_mb)
        self.memory = MemoryManager(adb, floor_mb=settings.memory_floor_mb, rooted=rooted)
        self.battery = BatteryManager(adb, console, emulator=emulator)
        self.thermal = ThermalManager(adb, emulator=emulator)
        shaper = gateway or (EmulatorConsoleShaper(console) if console else None)
        try:
            probe = NetworkProbe(adb, settings.probe_host)
        except ValueError:
            probe = None
        self.network = NetworkManager(shaper, Connectivity(adb, console), probe, base=request.network, seed=request.network.seed)

    def _emit(self, records: list[ConditionRecord]) -> list[ConditionRecord]:
        for r in records:
            self.record(r)
        return records

    # ---------------------------------------------------------------- device verification
    def verify_device(self, props: dict) -> list[ConditionRecord]:
        d = self.request.device
        now = datetime.now(timezone.utc)
        if not self.emulator:
            observed = {"device.api_level": props.get("api_level"), "device.ram_mb": props.get("mem_total_mb"), "device.cpu_cores": props.get("cpu_cores")}
            return self._emit([ConditionRecord(parameter_key=k, requested_value=getattr(d, k.split(".")[1], None), observed_value=v,
                                               application_status=A.UNSUPPORTED, mechanism="physical hardware (observed only)",
                                               evidence={"reason": "Physical device hardware cannot be reconfigured; actual value observed."})
                               for k, v in observed.items()])
        records = [self.memory.verify_guest_ram(d.ram_mb), self.storage.verify_partition(d.data_partition_mb)]

        def check(key, requested, observed, mechanism, unit=None):
            records.append(ConditionRecord(parameter_key=key, requested_value=requested, applied_value=requested, observed_value=observed, unit=unit,
                                           application_status=A.APPLIED_VERIFIED if observed == requested else A.FAILED,
                                           mechanism=mechanism, verified_at=now))

        check("device.api_level", d.api_level, int(props["api_level"]) if str(props.get("api_level", "")).isdigit() else props.get("api_level"),
              "system image; read back with getprop ro.build.version.sdk")
        check("device.cpu_cores", d.cpu_cores, props.get("cpu_cores"), "hw.cpu.ncore + -cores; read back from /sys/devices/system/cpu/online")
        check("device.screen", d.screen.model_dump(),
              {"width_px": props.get("display_width"), "height_px": props.get("display_height"), "density_dpi": props.get("display_density")},
              "hw.lcd.*; read back with wm size / wm density")
        if d.vm_heap_mb:
            heap = props.get("heap_size", "")
            check("device.vm_heap_mb", d.vm_heap_mb, int(heap[:-1]) if heap.endswith("m") and heap[:-1].isdigit() else heap,
                  "vm.heapSize; read back with getprop dalvik.vm.heapsize", "MB")
        return self._emit(records)

    # ---------------------------------------------------------------- apply / verify
    def record_plan_limitations(self, plan: PreflightResult) -> list[ConditionRecord]:
        return self._emit([ConditionRecord(parameter_key=item.parameter_key, requested_value=item.requested_value, unit=item.unit,
                                           application_status=PLAN_TO_STATUS[item.outcome], mechanism=item.mechanism,
                                           evidence={"reason": item.reason, "decided_at": "preflight"})
                           for item in plan.items if item.outcome in PLAN_TO_STATUS and not item.parameter_key.startswith("device.")])

    def apply_all(self) -> list[ConditionRecord]:
        req = self.request
        records: list[ConditionRecord] = []
        st = req.storage
        if st.free_target_mb is not None or st.free_target_pct is not None:
            if st.free_target_mb is not None:
                target, label = st.free_target_mb * MB, ("storage.free_target_mb", st.free_target_mb, "MB")
            else:
                total = self.storage.snapshot().total_bytes
                target, label = int(total * st.free_target_pct / 100), ("storage.free_target_pct", st.free_target_pct, "%")
            records.append(self.storage.apply_free_target(target, label))
        if req.battery.model_dump(exclude_none=True, exclude={"saver_enabled"}):
            records += self.battery.apply(req.battery)
        if req.battery.saver_enabled is not None:
            records.append(self.battery.set_saver(req.battery.saver_enabled))
        records += self.network.apply_base()   # shaping + connectivity; timeline stages run during scenarios
        return self._emit(records)

    def strict_failures(self, records: list[ConditionRecord]) -> list[str]:
        return [r.parameter_key for r in records if r.application_status is A.FAILED]

    # ---------------------------------------------------------------- revert
    def revert_all(self) -> list[ConditionRecord]:
        records: list[ConditionRecord] = []
        for step in (self.memory.release, self.storage.release, self.battery.revert, self.network.reset):
            try:
                out = step()
            except EmulatorError as exc:
                out = ConditionRecord(parameter_key=f"cleanup.{step.__name__}", application_status=A.FAILED, evidence={"error": exc.message})
            if out is None:
                continue
            records += out if isinstance(out, list) else [out]
        return self._emit(records)


def ensure_strict(records: list[ConditionRecord], strict: bool) -> None:
    failed = [r.parameter_key for r in records if r.application_status is A.FAILED]
    if strict and failed:
        raise ConditionApplicationError(
            f"Requested conditions failed verification: {', '.join(failed)}. Scenarios were not executed because results would not reflect "
            "the requested environment (disable strict_conditions to run anyway).", details={"failed": failed})
