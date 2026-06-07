import json
from datetime import time, timedelta
from typing import List, Dict, Any, Tuple
from collections import defaultdict

from sqlalchemy.orm import Session

from .config import (
    BATCH_STATUS,
    ANOMALY_SEVERITY,
    ANOMALY_STATUS,
    PEAK_HOURS,
    ANOMALY_THRESHOLDS,
)
from .database import get_db, create_db_session
from .models import (
    Batch,
    MeterReading,
    Anomaly,
    AnomalyType,
    Store,
    Meter,
)


class AnomalyDetector:
    def __init__(self, db: Session = None):
        self.db = db or create_db_session()
        self._load_anomaly_types()

    def _load_anomaly_types(self):
        self.anomaly_types = {
            at.code: at for at in self.db.query(AnomalyType).filter(
                AnomalyType.is_enabled == True
            ).all()
        }

    def analyze_batch(self, batch_id: int) -> Tuple[int, List[Anomaly]]:
        batch = self.db.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            raise ValueError(f"批次不存在: {batch_id}")

        self.db.query(Anomaly).filter(
            Anomaly.batch_id == batch_id,
            Anomaly.status == ANOMALY_STATUS["OPEN"],
        ).delete()

        readings = self.db.query(MeterReading).filter(
            MeterReading.batch_id == batch_id
        ).order_by(
            MeterReading.meter_id,
            MeterReading.reading_date,
            MeterReading.reading_time,
        ).all()

        if not readings:
            return 0, []

        anomaly_data_list = []

        anomaly_data_list.extend(self._detect_reading_drop(readings))
        anomaly_data_list.extend(self._detect_peak_usage(readings))
        anomaly_data_list.extend(self._detect_off_peak_usage(readings))
        anomaly_data_list.extend(self._detect_meter_missing(batch_id, readings))

        anomaly_objects = []
        for anomaly_data in anomaly_data_list:
            anomaly = Anomaly(
                batch_id=batch_id,
                reading_id=anomaly_data.get("reading_id"),
                anomaly_type_id=anomaly_data["anomaly_type_id"],
                anomaly_code=anomaly_data["code"],
                severity=anomaly_data["severity"],
                description=anomaly_data["description"],
                details=json.dumps(anomaly_data.get("details", {}), ensure_ascii=False),
                status=ANOMALY_STATUS["OPEN"],
            )
            self.db.add(anomaly)
            anomaly_objects.append(anomaly)

        batch.status = BATCH_STATUS["ANALYZED"]
        self.db.commit()

        return len(anomaly_objects), anomaly_objects

    def _detect_reading_drop(self, readings: List[MeterReading]) -> List[Dict[str, Any]]:
        anomalies = []
        atype = self.anomaly_types.get("READING_DROP")
        if not atype:
            return anomalies

        meter_readings = defaultdict(list)
        for r in readings:
            meter_readings[r.meter_id].append(r)

        threshold = ANOMALY_THRESHOLDS["reading_drop_kwh"]

        for meter_id, m_readings in meter_readings.items():
            m_readings.sort(key=lambda x: (x.reading_date, x.reading_time))
            for i in range(1, len(m_readings)):
                prev = m_readings[i - 1]
                curr = m_readings[i]
                drop = prev.reading_value - curr.reading_value
                if drop > threshold:
                    anomalies.append({
                        "reading_id": curr.id,
                        "anomaly_type_id": atype.id,
                        "code": "READING_DROP",
                        "severity": ANOMALY_SEVERITY["ERROR"],
                        "description": f"电表读数倒退: 从{prev.reading_value}降到{curr.reading_value}，下降{drop:.2f}kWh",
                        "details": {
                            "meter_id": curr.meter.meter_id,
                            "store_id": curr.store.store_id,
                            "previous_value": prev.reading_value,
                            "current_value": curr.reading_value,
                            "drop_amount": drop,
                            "previous_time": f"{prev.reading_date} {prev.reading_time}",
                            "current_time": f"{curr.reading_date} {curr.reading_time}",
                        },
                    })

        return anomalies

    def _detect_peak_usage(self, readings: List[MeterReading]) -> List[Dict[str, Any]]:
        anomalies = []
        atype = self.anomaly_types.get("PEAK_USAGE")
        if not atype:
            return anomalies

        meter_usage = defaultdict(list)
        for r in readings:
            meter_usage[r.meter_id].append(r)

        threshold_multiplier = ANOMALY_THRESHOLDS["peak_usage_multiplier"]
        peak_start = PEAK_HOURS["start"]
        peak_end = PEAK_HOURS["end"]

        for meter_id, m_readings in meter_usage.items():
            usages = []
            m_readings.sort(key=lambda x: (x.reading_date, x.reading_time))

            for i in range(1, len(m_readings)):
                prev = m_readings[i - 1]
                curr = m_readings[i]
                usage = curr.reading_value - prev.reading_value
                if usage > 0:
                    usages.append((curr, usage))

            if not usages:
                continue

            avg_usage = sum(u[1] for u in usages) / len(usages)
            peak_threshold = avg_usage * threshold_multiplier

            for curr, usage in usages:
                hour = curr.reading_time.hour
                if peak_start <= hour < peak_end and usage > peak_threshold:
                    anomalies.append({
                        "reading_id": curr.id,
                        "anomaly_type_id": atype.id,
                        "code": "PEAK_USAGE",
                        "severity": ANOMALY_SEVERITY["WARNING"],
                        "description": f"尖峰用电异常: {peak_start}:00-{peak_end}:00时段用电量{usage:.2f}kWh，超过平均值{avg_usage:.2f}kWh的{threshold_multiplier}倍",
                        "details": {
                            "meter_id": curr.meter.meter_id,
                            "store_id": curr.store.store_id,
                            "usage": usage,
                            "avg_usage": avg_usage,
                            "threshold": peak_threshold,
                            "hour": hour,
                            "reading_time": f"{curr.reading_date} {curr.reading_time}",
                        },
                    })

        return anomalies

    def _detect_off_peak_usage(self, readings: List[MeterReading]) -> List[Dict[str, Any]]:
        anomalies = []
        atype = self.anomaly_types.get("OFF_PEAK_USAGE")
        if not atype:
            return anomalies

        threshold = ANOMALY_THRESHOLDS["off_peak_usage_kwh"]

        for r in readings:
            store = r.store
            if not store:
                continue

            closing_time = store.closing_time
            reading_time = r.reading_time

            if reading_time > closing_time:
                prev_reading = self._get_previous_reading(r)
                if prev_reading:
                    usage = r.reading_value - prev_reading.reading_value
                    if usage > threshold:
                        anomalies.append({
                            "reading_id": r.id,
                            "anomaly_type_id": atype.id,
                            "code": "OFF_PEAK_USAGE",
                            "severity": ANOMALY_SEVERITY["WARNING"],
                            "description": f"闭店后耗电异常: 门店{store.store_id}闭店时间{closing_time}后仍有{usage:.2f}kWh用电（超过阈值{threshold}kWh）",
                            "details": {
                                "meter_id": r.meter.meter_id,
                                "store_id": store.store_id,
                                "closing_time": str(closing_time),
                                "reading_time": f"{r.reading_date} {r.reading_time}",
                                "usage": usage,
                                "threshold": threshold,
                            },
                        })

        return anomalies

    def _detect_meter_missing(self, batch_id: int, readings: List[MeterReading]) -> List[Dict[str, Any]]:
        anomalies = []
        atype = self.anomaly_types.get("METER_MISSING")
        if not atype:
            return anomalies

        batch = self.db.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            return anomalies

        reading_dates = set(r.reading_date for r in readings)
        active_meters = self.db.query(Meter).join(Store).all()

        batch_meters = set(r.meter_id for r in readings)

        for meter in active_meters:
            if meter.id in batch_meters:
                continue

            store = meter.store
            if not store:
                continue

            anomalies.append({
                "reading_id": None,
                "anomaly_type_id": atype.id,
                "code": "METER_MISSING",
                "severity": ANOMALY_SEVERITY["ERROR"],
                "description": f"缺表异常: 门店{store.store_id}的电表{meter.meter_id}在本批次中无记录（涉及日期: {sorted(reading_dates)}）",
                "details": {
                    "meter_id": meter.meter_id,
                    "store_id": store.store_id,
                    "batch_dates": sorted(str(d) for d in reading_dates),
                },
            })

        return anomalies

    def _get_previous_reading(self, reading: MeterReading) -> MeterReading:
        return self.db.query(MeterReading).filter(
            MeterReading.meter_id == reading.meter_id,
            MeterReading.id < reading.id,
        ).order_by(MeterReading.id.desc()).first()

    def get_anomalies(self, batch_id: int = None, anomaly_type: str = None,
                     unresolved: bool = False) -> List[Anomaly]:
        query = self.db.query(Anomaly).order_by(Anomaly.created_at.desc())

        if batch_id:
            query = query.filter(Anomaly.batch_id == batch_id)

        if anomaly_type:
            query = query.filter(Anomaly.anomaly_code == anomaly_type)

        if unresolved:
            query = query.filter(Anomaly.status == ANOMALY_STATUS["OPEN"])

        return query.all()

    def resolve_anomaly(self, anomaly_id: int, note: str = None,
                       resolved_by: str = None) -> Anomaly:
        anomaly = self.db.query(Anomaly).filter(Anomaly.id == anomaly_id).first()
        if not anomaly:
            raise ValueError(f"异常记录不存在: {anomaly_id}")

        anomaly.status = ANOMALY_STATUS["RESOLVED"]
        anomaly.resolution_note = note
        anomaly.resolved_by = resolved_by
        anomaly.resolved_at = __import__("datetime").datetime.now()

        self.db.commit()
        return anomaly
