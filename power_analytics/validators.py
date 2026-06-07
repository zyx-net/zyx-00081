import re
from datetime import datetime, time
from typing import Tuple, List, Dict, Any, Optional

from dateutil import parser as date_parser
import pytz

from .config import SUPPORTED_TIMEZONES, REQUIRED_FIELDS


class ValidationError(Exception):
    def __init__(self, message: str, error_code: str = None, details: Dict = None):
        super().__init__(message)
        self.error_code = error_code
        self.details = details or {}


class DataValidator:
    @staticmethod
    def validate_required_fields(columns: List[str]) -> Tuple[bool, List[str]]:
        missing = []
        for field in REQUIRED_FIELDS:
            if field not in columns:
                missing.append(field)
        return len(missing) == 0, missing

    @staticmethod
    def validate_timezone(timezone_str: str) -> bool:
        return timezone_str in SUPPORTED_TIMEZONES

    @staticmethod
    def parse_date(date_str: str) -> Optional[datetime.date]:
        if not date_str or not str(date_str).strip():
            return None
        try:
            return date_parser.parse(str(date_str)).date()
        except (ValueError, TypeError):
            return None

    @staticmethod
    def parse_time(time_str: str, default: time = time(0, 0)) -> time:
        if not time_str or not str(time_str).strip():
            return default
        time_str = str(time_str).strip()
        patterns = [
            r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$",
            r"^(\d{1,2})(\d{2})$",
        ]
        for pattern in patterns:
            match = re.match(pattern, time_str)
            if match:
                groups = match.groups()
                hour = int(groups[0])
                minute = int(groups[1])
                second = int(groups[2]) if groups[2] else 0
                if 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59:
                    return time(hour, minute, second)
        try:
            parsed = date_parser.parse(time_str)
            return parsed.time()
        except (ValueError, TypeError):
            return default

    @staticmethod
    def parse_float(value_str: str) -> Optional[float]:
        if value_str is None or str(value_str).strip() == "":
            return None
        try:
            return float(str(value_str).replace(",", "").strip())
        except (ValueError, TypeError):
            return None

    @staticmethod
    def parse_int(value_str: str) -> Optional[int]:
        if value_str is None or str(value_str).strip() == "":
            return None
        try:
            return int(float(str(value_str).replace(",", "").strip()))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def validate_row(row_data: Dict[str, Any], row_number: int,
                  store_devices: Dict[str, List[str]] = None) -> Tuple[bool, List[Dict[str, Any]]]:
        errors = []

        for field in REQUIRED_FIELDS:
            value = row_data.get(field)
            if value is None or str(value).strip() == "":
                errors.append({
                    "code": "MISSING_REQUIRED_FIELD",
                    "message": f"缺少必填字段: {field}",
                    "field": field,
                    "row": row_number,
                })

        timezone = str(row_data.get("timezone", "")).strip()
        if timezone and not DataValidator.validate_timezone(timezone):
            errors.append({
                "code": "INVALID_TIMEZONE",
                "message": f"无效时区: {timezone}",
                "row": row_number,
            })

        reading_value = DataValidator.parse_float(row_data.get("reading_value"))
        if reading_value is None:
            errors.append({
                "code": "INVALID_READING_VALUE",
                "message": f"无效的读数值: {row_data.get('reading_value')}",
                "row": row_number,
            })
        elif reading_value < 0:
            errors.append({
                "code": "NEGATIVE_READING",
                "message": f"读数值不能为负: {reading_value}",
                "row": row_number,
            })

        reading_date = DataValidator.parse_date(row_data.get("reading_date"))
        if reading_date is None:
            errors.append({
                "code": "INVALID_DATE",
                "message": f"无效的日期格式: {row_data.get('reading_date')}",
                "row": row_number,
            })

        device_id = row_data.get("device_id")
        store_id = row_data.get("store_id")
        if device_id and str(device_id).strip():
            device_id = str(device_id).strip()
            if store_devices:
                store_device_list = store_devices.get(store_id, [])
                if device_id not in store_device_list:
                    errors.append({
                        "code": "INVALID_DEVICE",
                        "message": f"设备不存在: 门店{store_id}的设备{device_id}",
                        "row": row_number,
                        "store_id": store_id,
                        "device_id": device_id,
                    })
            else:
                errors.append({
                    "code": "INVALID_DEVICE",
                    "message": f"设备不存在: 门店{store_id}的设备{device_id}（系统中未配置任何设备）",
                    "row": row_number,
                    "store_id": store_id,
                    "device_id": device_id,
                })

        return len(errors) == 0, errors

    @staticmethod
    def validate_duplicates(readings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = {}
        duplicates = []

        for reading in readings:
            key = (
                reading.get("store_id"),
                reading.get("meter_id"),
                str(reading.get("reading_date")),
                str(reading.get("reading_time")),
            )

            if key in seen:
                duplicates.append({
                    "code": "DUPLICATE_REPORT",
                    "message": f"同一门店同一时段重复记录: {reading.get('store_id')} {reading.get('meter_id')} {reading.get('reading_date')} {reading.get('reading_time')}",
                    "row": reading.get("row_number"),
                    "existing_row": seen[key],
                    "store_id": reading.get("store_id"),
                    "meter_id": reading.get("meter_id"),
                    "reading_date": reading.get("reading_date"),
                    "reading_time": reading.get("reading_time"),
                })
            else:
                seen[key] = reading.get("row_number")

        return duplicates
