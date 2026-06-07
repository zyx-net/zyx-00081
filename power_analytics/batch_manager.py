from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from .config import BATCH_STATUS, ANOMALY_STATUS, CORRECTION_STATUS
from .database import get_db, create_db_session
from .models import (
    Batch,
    MeterReading,
    Anomaly,
    Correction,
    RawRow,
)


class BatchManager:
    def __init__(self, db: Session = None):
        self.db = db or create_db_session()

    def close(self):
        if self.db:
            self.db.close()

    def list_batches(self, status: str = None, limit: int = 100) -> List[Batch]:
        query = self.db.query(Batch).order_by(Batch.id.desc())
        if status:
            query = query.filter(Batch.status == status)
        return query.limit(limit).all()

    def get_batch(self, batch_id: int) -> Optional[Batch]:
        return self.db.query(Batch).filter(Batch.id == batch_id).first()

    def get_batch_details(self, batch_id: int) -> dict:
        batch = self.get_batch(batch_id)
        if not batch:
            return None

        readings = self.db.query(MeterReading).filter(
            MeterReading.batch_id == batch_id
        ).all()

        anomalies = self.db.query(Anomaly).filter(
            Anomaly.batch_id == batch_id
        ).all()

        corrections = self.db.query(Correction).join(MeterReading).filter(
            MeterReading.batch_id == batch_id
        ).all()

        anomaly_summary = {}
        for a in anomalies:
            code = a.anomaly_code
            if code not in anomaly_summary:
                anomaly_summary[code] = {"count": 0, "severity": a.severity}
            anomaly_summary[code]["count"] += 1

        return {
            "batch": batch,
            "readings_count": len(readings),
            "anomalies_count": len(anomalies),
            "corrections_count": len(corrections),
            "anomaly_summary": anomaly_summary,
            "anomalies": anomalies,
            "corrections": corrections,
        }

    def commit_batch(self, batch_id: int, committed_by: str = None) -> Optional[Batch]:
        batch = self.get_batch(batch_id)
        if not batch:
            return None

        if batch.status == BATCH_STATUS["COMMITTED"]:
            return batch

        if batch.status == BATCH_STATUS["ROLLED_BACK"]:
            raise ValueError("已回滚的批次不能提交")

        batch.status = BATCH_STATUS["COMMITTED"]
        batch.committed_by = committed_by
        batch.committed_at = datetime.now()

        self.db.commit()
        return batch

    def rollback_batch(self, batch_id: int, reason: str = None,
                      rolled_back_by: str = None) -> Optional[Batch]:
        batch = self.get_batch(batch_id)
        if not batch:
            return None

        if batch.status == BATCH_STATUS["ROLLED_BACK"]:
            return batch

        self._rollback_corrections_for_batch(batch_id, rolled_back_by)

        self.db.query(Anomaly).filter(Anomaly.batch_id == batch_id).delete()

        self.db.query(MeterReading).filter(MeterReading.batch_id == batch_id).delete()

        batch.status = BATCH_STATUS["ROLLED_BACK"]
        batch.rollback_reason = reason
        batch.rolled_back_by = rolled_back_by
        batch.rolled_back_at = datetime.now()

        self.db.commit()
        return batch

    def _rollback_corrections_for_batch(self, batch_id: int, rolled_back_by: str = None):
        correction_ids = [c.id for c in self.db.query(Correction.id).join(MeterReading).filter(
            MeterReading.batch_id == batch_id,
            Correction.status == CORRECTION_STATUS["APPLIED"],
        ).all()]

        for cid in correction_ids:
            correction = self.db.query(Correction).filter(Correction.id == cid).first()
            if not correction:
                continue
            reading = correction.reading
            if reading:
                reading.reading_value = correction.old_value
                reading.is_corrected = False

            correction.status = CORRECTION_STATUS["ROLLED_BACK"]
            correction.rolled_back_by = rolled_back_by
            correction.rolled_back_at = datetime.now()

    def delete_batch(self, batch_id: int) -> bool:
        batch = self.get_batch(batch_id)
        if not batch:
            return False

        self.db.query(Anomaly).filter(Anomaly.batch_id == batch_id).delete()

        correction_ids = [c.id for c in self.db.query(Correction.id).join(MeterReading).filter(
            MeterReading.batch_id == batch_id
        ).all()]
        for cid in correction_ids:
            self.db.query(Correction).filter(Correction.id == cid).delete()

        self.db.query(MeterReading).filter(MeterReading.batch_id == batch_id).delete()
        self.db.query(RawRow).filter(RawRow.batch_id == batch_id).delete()
        self.db.delete(batch)

        self.db.commit()
        return True
