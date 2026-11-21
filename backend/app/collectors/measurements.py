from datetime import datetime, timezone


def unavailable(parameter_key: str, source: str, reason: str = "Not exposed by this device or runner") -> dict:
    """Build an explicit unavailable observation; never guess device telemetry."""
    return {"parameter_key":parameter_key,"numeric_value":None,"text_value":None,"boolean_value":None,"source":source,"validity_status":"unsupported","metadata":{"reason":reason},"observed_at":datetime.now(timezone.utc)}
