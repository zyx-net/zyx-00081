import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "power_analytics.db"
EXPORT_DIR = DATA_DIR / "exports"
SAMPLE_DATA_DIR = BASE_DIR / "sample_data"
TEMPLATES_DIR = Path(__file__).parent / "templates"

SUPPORTED_TIMEZONES = {
    "Asia/Shanghai": "中国标准时间",
    "UTC": "协调世界时",
    "America/New_York": "美国东部时间",
    "Europe/London": "英国时间",
}

PEAK_HOURS = {
    "start": 18,
    "end": 22,
}

ANOMALY_THRESHOLDS = {
    "peak_usage_multiplier": 2.0,
    "off_peak_usage_kwh": 5.0,
    "reading_drop_kwh": 0.1,
}

REQUIRED_FIELDS = [
    "store_id",
    "meter_id",
    "reading_date",
    "reading_value",
    "timezone",
]

OPTIONAL_FIELDS = [
    "store_name",
    "reading_time",
    "reading_unit",
    "opening_time",
    "closing_time",
    "operator",
    "device_id",
]

BATCH_STATUS = {
    "IMPORTED": "imported",
    "VALIDATED": "validated",
    "ANALYZED": "analyzed",
    "COMMITTED": "committed",
    "ROLLED_BACK": "rolled_back",
}

ANOMALY_SEVERITY = {
    "ERROR": "error",
    "WARNING": "warning",
    "INFO": "info",
}

ANOMALY_STATUS = {
    "OPEN": "open",
    "RESOLVED": "resolved",
    "IGNORED": "ignored",
}

CORRECTION_STATUS = {
    "APPLIED": "applied",
    "ROLLED_BACK": "rolled_back",
}

CONFLICT_STRATEGIES = {
    "REJECT": "reject",
    "ISOLATE": "isolate",
    "OVERWRITE": "overwrite",
}

CONFLICT_TYPES = {
    "DUPLICATE_BATCH_NAME": "duplicate_batch_name",
    "DUPLICATE_READING": "duplicate_reading",
    "MISSING_DEVICE": "missing_device",
}

SCHEMES_DIR = DATA_DIR / "schemes"
AUDIT_LOGS_DIR = DATA_DIR / "audit_logs"

for directory in [DATA_DIR, EXPORT_DIR, TEMPLATES_DIR, SCHEMES_DIR, AUDIT_LOGS_DIR]:
    directory.mkdir(parents=True, exist_ok=True)
