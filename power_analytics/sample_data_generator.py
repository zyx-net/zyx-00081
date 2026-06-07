import csv
from datetime import datetime, timedelta, time
from pathlib import Path
from typing import List, Dict, Any

import pandas as pd

from .config import SAMPLE_DATA_DIR


class SampleDataGenerator:
    STORES = [
        {"store_id": "S001", "store_name": "中关村店", "opening_time": "08:00", "closing_time": "22:00"},
        {"store_id": "S002", "store_name": "国贸店", "opening_time": "09:00", "closing_time": "23:00"},
        {"store_id": "S003", "store_name": "望京店", "opening_time": "08:30", "closing_time": "21:30"},
    ]

    METERS = {
        "S001": ["M001", "M002"],
        "S002": ["M003", "M004"],
        "S003": ["M005", "M006"],
    }

    BASE_READINGS = {
        "M001": 10000,
        "M002": 5000,
        "M003": 8000,
        "M004": 4000,
        "M005": 12000,
        "M006": 6000,
    }

    TIMES = ["08:00", "12:00", "18:00", "22:00"]

    def __init__(self, output_dir: Path = None):
        self.output_dir = output_dir or SAMPLE_DATA_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _generate_normal_data(self, days: int = 30) -> List[Dict[str, Any]]:
        rows = []
        start_date = datetime(2024, 6, 1).date()

        for day_offset in range(days):
            reading_date = start_date + timedelta(days=day_offset)

            for store in self.STORES:
                for meter_id in self.METERS[store["store_id"]]:
                    for reading_time in self.TIMES:
                        hour = int(reading_time.split(":")[0])
                        time_idx = self.TIMES.index(reading_time)
                        day_increment = day_offset * 4 * 3

                        base = self.BASE_READINGS[meter_id]
                        increment = 12 + day_increment + time_idx * 3

                        if hour >= 18 and hour < 22:
                            increment += 5

                        reading_value = base + increment

                        rows.append({
                            "store_id": store["store_id"],
                            "store_name": store["store_name"],
                            "meter_id": meter_id,
                            "reading_date": reading_date.strftime("%Y-%m-%d"),
                            "reading_time": reading_time,
                            "reading_value": reading_value,
                            "reading_unit": "kWh",
                            "timezone": "Asia/Shanghai",
                            "opening_time": store["opening_time"],
                            "closing_time": store["closing_time"],
                            "operator": "张三",
                        })

        return rows

    def _generate_with_anomalies(self) -> List[Dict[str, Any]]:
        base_date = datetime(2024, 6, 16).date()

        rows = []

        rows.append({
            "store_id": "S001",
            "store_name": "中关村店",
            "meter_id": "M00101",
            "reading_date": "2024-06-20",
            "reading_time": "18:00",
            "reading_value": 11250,
            "reading_unit": "kWh",
            "timezone": "Asia/Shanghai",
            "opening_time": "08:00",
            "closing_time": "22:00",
            "operator": "王五",
        })

        rows.append({
            "store_id": "S002",
            "store_name": "国贸店",
            "meter_id": "M00203",
            "reading_date": "2024-06-20",
            "reading_time": "18:00",
            "reading_value": 11250,
            "reading_unit": "kWh",
            "timezone": "Asia/Shanghai",
            "opening_time": "09:00",
            "closing_time": "23:00",
            "operator": "王五",
        })

        for i in range(3):
            for meter in ["M00101", "M00203"]:
                rows.append({
                    "store_id": "S001" if meter == "M00101" else "S002",
                    "store_name": "中关村店" if meter == "M00101" else "国贸店",
                    "meter_id": meter,
                    "reading_date": (datetime(2024, 6, 21) + timedelta(days=i)).strftime("%Y-%m-%d"),
                    "reading_time": "18:00",
                    "reading_value": 11300 + i * 50,
                    "reading_unit": "kWh",
                    "timezone": "Asia/Shanghai",
                    "opening_time": "08:00" if meter == "M00101" else "09:00",
                    "closing_time": "22:00" if meter == "M00101" else "23:00",
                    "operator": "王五",
                })

        rows.append({
            "store_id": "S001",
            "store_name": "中关村店",
            "meter_id": "M001",
            "reading_date": "2024-06-16",
            "reading_time": "09:30",
            "reading_value": 10250,
            "reading_unit": "kWh",
            "timezone": "Asia/Shanghai",
            "opening_time": "08:00",
            "closing_time": "22:00",
            "operator": "张三",
        })

        rows.append({
            "store_id": "S001",
            "store_name": "中关村店",
            "meter_id": "M001",
            "reading_date": "2024-06-16",
            "reading_time": "12:00",
            "reading_value": 9800,
            "reading_unit": "kWh",
            "timezone": "Asia/Shanghai",
            "opening_time": "08:00",
            "closing_time": "22:00",
            "operator": "张三",
        })

        rows.append({
            "store_id": "S001",
            "store_name": "中关村店",
            "meter_id": "M001",
            "reading_date": "2024-06-16",
            "reading_time": "23:30",
            "reading_value": 10800,
            "reading_unit": "kWh",
            "timezone": "Asia/Shanghai",
            "opening_time": "08:00",
            "closing_time": "22:00",
            "operator": "张三",
        })

        rows.append({
            "store_id": "S002",
            "store_name": "国贸店",
            "meter_id": "M003",
            "reading_date": "2024-06-17",
            "reading_time": "09:00",
            "reading_value": 8500,
            "reading_unit": "kWh",
            "timezone": "Asia/Shanghai",
            "opening_time": "09:00",
            "closing_time": "23:00",
            "operator": "李四",
        })

        rows.append({
            "store_id": "S002",
            "store_name": "国贸店",
            "meter_id": "M003",
            "reading_date": "2024-06-17",
            "reading_time": "09:00",
            "reading_value": 8500,
            "reading_unit": "kWh",
            "timezone": "Asia/Shanghai",
            "opening_time": "09:00",
            "closing_time": "23:00",
            "operator": "李四",
        })

        rows.append({
            "store_id": "S002",
            "store_name": "国贸店",
            "meter_id": "M003",
            "reading_date": "2024-06-17",
            "reading_time": "19:00",
            "reading_value": 8850,
            "reading_unit": "kWh",
            "timezone": "Asia/Shanghai",
            "opening_time": "09:00",
            "closing_time": "23:00",
            "operator": "李四",
        })

        return rows

    def _generate_missing_columns(self) -> List[Dict[str, Any]]:
        return [
            {
                "store_id": "S001",
                "store_name": "中关村店",
                "reading_date": "2024-06-01",
                "reading_time": "08:00",
                "reading_value": 10000,
                "reading_unit": "kWh",
                "opening_time": "08:00",
                "closing_time": "22:00",
            },
        ]

    def _generate_invalid_timezone(self) -> List[Dict[str, Any]]:
        return [
            {
                "store_id": "S001",
                "store_name": "中关村店",
                "meter_id": "M001",
                "reading_date": "2024-06-01",
                "reading_time": "08:00",
                "reading_value": 10000,
                "reading_unit": "kWh",
                "timezone": "Invalid/Timezone",
                "opening_time": "08:00",
                "closing_time": "22:00",
                "operator": "张三",
            },
            {
                "store_id": "S001",
                "store_name": "中关村店",
                "meter_id": "M001",
                "reading_date": "2024-06-02",
                "reading_time": "08:00",
                "reading_value": 10012,
                "reading_unit": "kWh",
                "timezone": "Asia/Beijing",
                "opening_time": "08:00",
                "closing_time": "22:00",
                "operator": "张三",
            },
        ]

    def _generate_invalid_device(self) -> List[Dict[str, Any]]:
        return [
            {
                "store_id": "S001",
                "store_name": "中关村店",
                "meter_id": "M001",
                "reading_date": "2024-06-01",
                "reading_time": "08:00",
                "reading_value": 10000,
                "reading_unit": "kWh",
                "timezone": "Asia/Shanghai",
                "opening_time": "08:00",
                "closing_time": "22:00",
                "operator": "张三",
                "device_id": "DEV999",
            },
        ]

    def _generate_devices_config(self) -> List[Dict[str, Any]]:
        return [
            {"device_id": "DEV001", "store_id": "S001", "device_name": "中央空调1", "power_kw": 15.0},
            {"device_id": "DEV002", "store_id": "S001", "device_name": "照明系统", "power_kw": 8.0},
            {"device_id": "DEV003", "store_id": "S002", "device_name": "中央空调1", "power_kw": 18.0},
            {"device_id": "DEV004", "store_id": "S002", "device_name": "冷藏柜", "power_kw": 5.0},
            {"device_id": "DEV005", "store_id": "S003", "device_name": "中央空调1", "power_kw": 12.0},
        ]

    def _write_csv(self, filename: str, rows: List[Dict[str, Any]]) -> Path:
        file_path = self.output_dir / filename
        if not rows:
            return file_path

        with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

        return file_path

    def _write_excel(self, filename: str, rows: List[Dict[str, Any]]) -> Path:
        file_path = self.output_dir / filename
        df = pd.DataFrame(rows)
        df.to_excel(file_path, index=False)
        return file_path

    def generate_all(self) -> List[Path]:
        files = []

        normal_rows = self._generate_normal_data(days=15)
        files.append(self._write_csv("normal_readings.csv", normal_rows))
        files.append(self._write_excel("normal_readings.xlsx", normal_rows))

        anomaly_rows = self._generate_with_anomalies()
        files.append(self._write_csv("with_anomalies.csv", anomaly_rows))

        missing_cols_rows = self._generate_missing_columns()
        files.append(self._write_csv("error_missing_columns.csv", missing_cols_rows))

        invalid_tz_rows = self._generate_invalid_timezone()
        files.append(self._write_csv("error_invalid_timezone.csv", invalid_tz_rows))

        invalid_device_rows = self._generate_invalid_device()
        files.append(self._write_csv("error_invalid_device.csv", invalid_device_rows))

        devices_rows = self._generate_devices_config()
        files.append(self._write_csv("devices_config.csv", devices_rows))

        return files
