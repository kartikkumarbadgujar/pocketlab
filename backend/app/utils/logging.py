import json
import logging
import time


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload={"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "level": record.levelname, "logger": record.name, "message": record.getMessage()}
        for key in ("path","method","status_code","duration_ms","environment"):
            if hasattr(record,key):payload[key]=getattr(record,key)
        if record.exc_info:payload["exception"]=self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: str = "INFO"):
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
