from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import ConditionProfile, ConditionProfileVersion, ConditionProfileParameter, MeasurementDefinition, ScenarioStep, ScenarioVersion, TestScenario

PROFILE_DATA = [
 ("NORMAL_BASELINE", "Illustrative baseline conditions", {"device.target_ram_gb":8,"storage.free_target_mb":8192,"network.packet_loss_pct":0,"network.latency_ms":50,"battery.saver_enabled":False}),
 ("ENTRY_LEVEL_CONSTRAINED", "Illustrative constrained conditions", {"device.target_ram_gb":2,"storage.free_target_mb":400,"network.packet_loss_pct":1,"network.latency_ms":180,"battery.level_pct":20,"battery.saver_enabled":True}),
 ("INTERMITTENT_NETWORK", "Illustrative intermittent network conditions", {"device.target_ram_gb":2,"storage.free_target_mb":500,"network.packet_loss_pct":5,"network.latency_ms":500,"network.jitter_ms":150,"network.online_seconds":30,"network.offline_seconds":10}),
]
SCENARIO_DATA = [
 ("Cold application startup","Measure a cold launch and record startup milestones.","performance",["force_stop","launch_app","observe_startup"]),
 ("Product image loading under unstable connectivity","Load product images with constrained and intermittent networking.","network",["apply_network_profile","load_images","record_image_results"]),
 ("Network disconnect and recovery","Observe app behavior through a network outage and recovery.","network",["disconnect_network","wait","restore_network","verify_recovery"]),
 ("API request timeout and retry","Exercise timeout and retry behavior under latency.","reliability",["configure_latency","issue_request","observe_retries"]),
 ("Process death and local-state recovery","Kill and relaunch the app to inspect persisted state.","reliability",["create_local_state","kill_process","relaunch","verify_state"]),
 ("Low-storage write and download handling","Observe graceful behavior with constrained free storage.","storage",["constrain_storage","download_file","record_write_errors"]),
 ("Sustained thermal observation","Observe Android thermal APIs during sustained workload.","thermal",["record_thermal_baseline","sustain_workload","record_thermal_observations"]),
 ("Memory-pressure and background-process recovery","Observe memory use and recovery after background pressure.","memory",["launch_app","apply_memory_pressure","return_to_app","verify_recovery"]),
 ("Battery-saver behaviour","Compare application behavior with battery saver enabled.","battery",["record_baseline","enable_battery_saver","exercise_app"]),
 ("UI scrolling and frame-performance test","Measure frame durations during a defined scroll.","performance",["open_scroll_screen","perform_scroll","record_frame_metrics"]),
 ("Wi-Fi/mobile-data handover","Observe requests across network handover.","network",["connect_wifi","start_request","switch_to_mobile","record_handover"]),
 ("Application crash and ANR observation","Observe process crash and ANR signals during scenario execution.","reliability",["launch_app","exercise_app","observe_crash_anr"]),
]

MEASUREMENT_KEYS = {
 "hardware": "ram.total_mb ram.available_mb cpu.manufacturer cpu.model cpu.core_count cpu.frequency_mhz cpu.architecture gpu.model android.version android.api_level display.width_px display.height_px display.refresh_rate_hz device.manufacturer device.model",
 "storage": "storage.total_bytes storage.available_bytes storage.free_pct app.storage_bytes app.cache_bytes storage.read_mbps storage.write_mbps storage.file_creation_errors storage.database_write_errors storage.write_latency_ms",
 "memory": "app.pss_mb app.rss_mb app.heap_used_mb app.peak_memory_mb app.memory_growth_mb_per_min memory.pressure background.process_count process.kill_count process.recovery_after_death",
 "network": "network.connection_type network.download_mbps network.upload_mbps network.rtt_ms network.jitter_ms network.packet_loss_pct network.burst_loss_mode network.packet_reordering network.packet_duplication network.interruption_seconds network.reconnection_ms network.dns_failures network.request_timeouts network.request_retries network.signal_dbm network.ss_rsrp_dbm network.ss_rsrq_db network.sinr_db network.handover_events network.online_seconds network.offline_seconds",
 "thermal": "battery.temperature_c cpu.temperature_c gpu.temperature_c device.skin_temperature_c thermal.status thermal.headroom thermal.cpu_frequency_reduction_pct thermal.sustained_workload_seconds environment.ambient_temperature_c",
 "battery": "battery.level_pct battery.charging battery.voltage_v battery.saver_enabled battery.drain_pct_per_hour device.doze_mode device.background_restriction device.power_saving_mode device.battery_optimization",
 "performance": "performance.startup_cold_ms performance.startup_warm_ms performance.startup_hot_ms performance.ttid_ms performance.ttfd_ms performance.frame_p50_ms performance.frame_p90_ms performance.frame_p95_ms performance.frame_p99_ms performance.slow_frames performance.frozen_frames performance.cpu_utilization_pct performance.peak_memory_mb app.crashes app.anrs performance.screen_transition_ms",
 "reliability": "http.success_rate_pct http.failure_rate_pct http.request_latency_ms http.timeout_count http.retry_count http.duplicate_request_count action.duplicate_count image.loading_success_rate_pct app.local_state_preserved app.process_death_recovery app.offline_handling data.corruption_incidents",
 "environment": "app.foreground app.screen_on device.uptime_seconds device.background_app_count device.data_saver_enabled environment.screen_brightness_pct environment.orientation environment.font_scale environment.locale",
}
MEASUREMENT_UNITS = {"_pct":"%","_mb":"MB","_mbps":"Mbps","_ms":"ms","_bytes":"bytes","_dbm":"dBm","_db":"dB","_v":"V","_hz":"Hz","_mhz":"MHz","_seconds":"s","_count":"count","_c":"degC"}
SENSITIVE_SOURCES={"thermal.status":["android_api"],"battery.temperature_c":["android_api"],"cpu.temperature_c":["android_api"],"storage.available_bytes":["filesystem"],"memory.available_mb":["android_api"],"network.packet_loss_pct":["network_probe","runner_network_probe"]}


def seed_database(db: Session):
    configurable_keys={"device.target_ram_gb","storage.free_target_mb","network.packet_loss_pct","network.latency_ms","network.jitter_ms","battery.level_pct","battery.saver_enabled","network.online_seconds","network.offline_seconds"}
    for category, keys in MEASUREMENT_KEYS.items():
        for key in keys.split():
            if db.scalar(select(MeasurementDefinition).where(MeasurementDefinition.parameter_key==key)):continue
            unit=next((u for suffix,u in MEASUREMENT_UNITS.items() if key.endswith(suffix)),None)
            lo,hi=(0,100) if key.endswith("_pct") else (None,None)
            if key=="thermal.status":lo,hi=0,6;unit="Android enum (0-6)"
            db.add(MeasurementDefinition(parameter_key=key,category=category,name=key.replace("."," ").replace("_"," ").title(),description="Observed value; source and validity are recorded on each metric sample.",data_type="boolean" if any(term in key for term in ("enabled","charging","preserved","handling","recovery","foreground","screen_on","corruption")) else "text" if any(term in key for term in ("type","mode","manufacturer","model","architecture","version","locale","orientation","pressure")) else "number",unit=unit,minimum_value=lo,maximum_value=hi,supported_sources=SENSITIVE_SOURCES.get(key,["android_api","runner","manual"]),is_configurable=key in configurable_keys,is_observation_only=key not in configurable_keys))
    db.flush()
    for key, category, unit, lo, hi, cfg in [
        ("device.target_ram_gb","hardware","GB",0,None,True),("storage.free_target_mb","storage","MB",0,None,True),
        ("network.packet_loss_pct","network","%",0,100,True),("network.latency_ms","network","ms",0,None,True),
        ("network.jitter_ms","network","ms",0,None,True),("battery.level_pct","battery","%",0,100,True),
        ("thermal.status","thermal","enum",0,6,False),("thermal.headroom","thermal","ratio",0,None,False),
        ("battery.temperature_c","battery","degC",None,None,False),("cpu.temperature_c","thermal","degC",None,None,False),
        ("memory.available_mb","memory","MB",0,None,False),("storage.available_bytes","storage","bytes",0,None,False),
        ("performance.ttid_ms","performance","ms",0,None,False),("performance.frame_p95_ms","performance","ms",0,None,False),
        ("network.rtt_ms","network","ms",0,None,False),("network.signal_dbm","network","dBm",None,None,False),
    ]:
        if not db.scalar(select(MeasurementDefinition).where(MeasurementDefinition.parameter_key==key)):
            db.add(MeasurementDefinition(parameter_key=key,category=category,name=key.replace("."," ").title(),data_type="number",unit=unit,minimum_value=lo,maximum_value=hi,supported_sources=["android_api","runner","manual"],is_configurable=cfg,is_observation_only=not cfg))
    db.flush()
    for name, desc, config in PROFILE_DATA:
        profile = db.scalar(select(ConditionProfile).where(ConditionProfile.name==name))
        if profile: continue
        profile=ConditionProfile(owner_user_id=None,name=name,description=desc,profile_type="illustrative",is_public_template=True); db.add(profile); db.flush()
        version=ConditionProfileVersion(profile_id=profile.id,version=1,configuration_json=config); db.add(version); db.flush()
        for key,value in config.items():
            definition=db.scalar(select(MeasurementDefinition).where(MeasurementDefinition.parameter_key==key))
            if definition:
                fields={"numeric_value":value} if isinstance(value,(int,float)) and not isinstance(value,bool) else {"boolean_value":value} if isinstance(value,bool) else {"text_value":str(value)}
                db.add(ConditionProfileParameter(profile_version_id=version.id,parameter_definition_id=definition.id,**fields))
    for name, desc, category, steps in SCENARIO_DATA:
        if db.scalar(select(TestScenario).where(TestScenario.name==name)): continue
        scenario=TestScenario(name=name,description=desc,scenario_category=category); db.add(scenario); db.flush()
        ver=ScenarioVersion(scenario_id=scenario.id,version=1); db.add(ver); db.flush()
        for order,action in enumerate(steps): db.add(ScenarioStep(scenario_version_id=ver.id,step_order=order,action_type=action,action_configuration_json={},expected_result_json={},timeout_seconds=120))
    db.commit()
