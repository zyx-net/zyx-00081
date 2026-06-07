import csv
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from .config import EXPORT_DIR, TEMPLATES_DIR
from .database import get_db, create_db_session
from .models import (
    Batch,
    MeterReading,
    Anomaly,
    Correction,
    ExportSummary,
    Store,
)


class ReportExporter:
    def __init__(self, db: Session = None):
        self.db = db or create_db_session()
        self.env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        self.env.filters["from_json"] = lambda s: json.loads(s) if s else {}

    def close(self):
        if self.db:
            self.db.close()

    def _generate_file_name(self, prefix: str, batch_name: str, ext: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in batch_name)
        return f"{safe_name}_{prefix}_{timestamp}.{ext}"

    def _get_batch_data(self, batch_id: int) -> Dict[str, Any]:
        batch = self.db.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            return None

        readings = self.db.query(MeterReading).filter(
            MeterReading.batch_id == batch_id
        ).order_by(
            MeterReading.store_id,
            MeterReading.meter_id,
            MeterReading.reading_date,
            MeterReading.reading_time,
        ).all()

        anomalies = self.db.query(Anomaly).filter(
            Anomaly.batch_id == batch_id
        ).order_by(Anomaly.severity, Anomaly.created_at).all()

        corrections = self.db.query(Correction).join(MeterReading).filter(
            MeterReading.batch_id == batch_id
        ).order_by(Correction.created_at.desc()).all()

        anomaly_summary = {}
        for a in anomalies:
            code = a.anomaly_code
            if code not in anomaly_summary:
                anomaly_summary[code] = {"count": 0, "severity": a.severity, "name": a.anomaly_type.name if a.anomaly_type else code}
            anomaly_summary[code]["count"] += 1

        store_stats = {}
        for r in readings:
            store_id = r.store.store_id if r.store else "Unknown"
            if store_id not in store_stats:
                store_stats[store_id] = {"readings": 0, "anomalies": 0}
            store_stats[store_id]["readings"] += 1

        for a in anomalies:
            if a.reading and a.reading.store:
                store_id = a.reading.store.store_id
                if store_id in store_stats:
                    store_stats[store_id]["anomalies"] += 1

        return {
            "batch": batch,
            "readings": readings,
            "anomalies": anomalies,
            "corrections": corrections,
            "anomaly_summary": anomaly_summary,
            "store_stats": store_stats,
            "export_time": datetime.now(),
        }

    def export_html(self, batch_id: int, exported_by: str = None) -> Path:
        data = self._get_batch_data(batch_id)
        if not data:
            raise ValueError(f"批次不存在: {batch_id}")

        template = self.env.get_template("report.html")
        html_content = template.render(**data)

        file_name = self._generate_file_name("report", data["batch"].name, "html")
        file_path = EXPORT_DIR / file_name
        file_path.write_text(html_content, encoding="utf-8")

        self._record_export(
            batch_id=batch_id,
            export_type="html",
            file_path=str(file_path),
            file_name=file_name,
            record_count=len(data["readings"]),
            anomaly_count=len(data["anomalies"]),
            exported_by=exported_by,
        )

        return file_path

    def export_csv(self, batch_id: int, exported_by: str = None) -> Path:
        data = self._get_batch_data(batch_id)
        if not data:
            raise ValueError(f"批次不存在: {batch_id}")

        file_name = self._generate_file_name("readings", data["batch"].name, "csv")
        file_path = EXPORT_DIR / file_name

        with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow([
                "门店编号", "门店名称", "电表编号", "抄表日期", "抄表时间",
                "读数", "单位", "时区", "开门时间", "关门时间",
                "抄表员", "是否修正", "原值"
            ])

            for r in data["readings"]:
                writer.writerow([
                    r.store.store_id if r.store else "",
                    r.store.store_name if r.store else "",
                    r.meter.meter_id if r.meter else "",
                    r.reading_date,
                    r.reading_time,
                    r.reading_value,
                    r.reading_unit,
                    r.timezone,
                    r.store.opening_time if r.store else "",
                    r.store.closing_time if r.store else "",
                    r.operator or "",
                    "是" if r.is_corrected else "否",
                    r.original_value if r.is_corrected else "",
                ])

        self._record_export(
            batch_id=batch_id,
            export_type="csv",
            file_path=str(file_path),
            file_name=file_name,
            record_count=len(data["readings"]),
            anomaly_count=len(data["anomalies"]),
            exported_by=exported_by,
        )

        return file_path

    def export_anomalies_csv(self, batch_id: int = None, anomaly_type: str = None,
                            unresolved: bool = False, exported_by: str = None) -> Path:
        from .anomaly_detector import AnomalyDetector
        detector = AnomalyDetector(self.db)
        anomalies = detector.get_anomalies(batch_id, anomaly_type, unresolved)

        batch_name = "all"
        if batch_id:
            batch = self.db.query(Batch).filter(Batch.id == batch_id).first()
            if batch:
                batch_name = batch.name

        file_name = self._generate_file_name("anomalies", batch_name, "csv")
        file_path = EXPORT_DIR / file_name

        with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow([
                "异常ID", "批次ID", "批次名称", "异常类型", "异常代码", "严重程度",
                "描述", "状态", "门店编号", "电表编号", "读数", "发现时间", "解决时间"
            ])

            for a in anomalies:
                writer.writerow([
                    a.id,
                    a.batch_id,
                    a.batch.name if a.batch else "",
                    a.anomaly_type.name if a.anomaly_type else "",
                    a.anomaly_code,
                    a.severity,
                    a.description,
                    a.status,
                    a.reading.store.store_id if a.reading and a.reading.store else "",
                    a.reading.meter.meter_id if a.reading and a.reading.meter else "",
                    a.reading.reading_value if a.reading else "",
                    a.created_at,
                    a.resolved_at or "",
                ])

        self._record_export(
            batch_id=batch_id,
            export_type="anomalies_csv",
            file_path=str(file_path),
            file_name=file_name,
            record_count=len(anomalies),
            anomaly_count=len(anomalies),
            exported_by=exported_by,
        )

        return file_path

    def _record_export(self, batch_id: int, export_type: str, file_path: str,
                         file_name: str, record_count: int, anomaly_count: int,
                         exported_by: str = None):
        summary = ExportSummary(
            batch_id=batch_id,
            export_type=export_type,
            file_path=file_path,
            file_name=file_name,
            record_count=record_count,
            anomaly_count=anomaly_count,
            exported_by=exported_by,
        )
        self.db.add(summary)
        self.db.commit()

    def get_export_history(self, limit: int = 50) -> List[ExportSummary]:
        return self.db.query(ExportSummary).order_by(
            ExportSummary.created_at.desc()
        ).limit(limit).all()
