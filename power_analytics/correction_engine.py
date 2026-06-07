import re
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

from sqlalchemy.orm import Session

from .config import CORRECTION_STATUS, ANOMALY_STATUS
from .database import get_db, create_db_session
from .models import (
    CorrectionRule,
    Correction,
    MeterReading,
    Anomaly,
)
from .anomaly_detector import AnomalyDetector


class RuleEngine:
    SAFE_FUNCTIONS = {
        "abs": abs,
        "min": min,
        "max": max,
        "round": round,
        "float": float,
        "int": int,
    }

    def __init__(self, db: Session = None):
        self.db = db or create_db_session()

    def close(self):
        if self.db:
            self.db.close()

    def create_rule(
        self,
        code: str,
        name: str,
        version: str,
        condition: str,
        action: str,
        description: str = None,
        created_by: str = None,
    ) -> CorrectionRule:
        existing = self.db.query(CorrectionRule).filter(
            CorrectionRule.code == code,
            CorrectionRule.version == version,
        ).first()

        if existing:
            raise ValueError(f"规则已存在: {code} v{version}")

        rule = CorrectionRule(
            code=code,
            name=name,
            version=version,
            description=description,
            condition=condition,
            action=action,
            created_by=created_by,
        )
        self.db.add(rule)
        self.db.commit()
        return rule

    def list_rules(self, active_only: bool = True) -> List[CorrectionRule]:
        query = self.db.query(CorrectionRule).order_by(
            CorrectionRule.code, CorrectionRule.version
        )
        if active_only:
            query = query.filter(CorrectionRule.is_active == True)
        return query.all()

    def get_rule(self, rule_id: int) -> Optional[CorrectionRule]:
        return self.db.query(CorrectionRule).filter(CorrectionRule.id == rule_id).first()

    def deactivate_rule(self, rule_id: int) -> Optional[CorrectionRule]:
        rule = self.get_rule(rule_id)
        if rule:
            rule.is_active = False
            self.db.commit()
        return rule

    def _evaluate_condition(self, condition: str, context: Dict[str, Any]) -> bool:
        allowed_vars = set(context.keys()) | set(self.SAFE_FUNCTIONS.keys())
        tree = ast.parse(condition, mode="eval")

        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id not in allowed_vars:
                raise ValueError(f"条件中包含不允许的变量: {node.id}")
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id not in self.SAFE_FUNCTIONS:
                    raise ValueError(f"条件中包含不允许的函数调用: {node.func.id}")

        return eval(compile(tree, "<condition>", "eval"), {"__builtins__": {}}, {**context, **self.SAFE_FUNCTIONS})

    def _evaluate_action(self, action: str, context: Dict[str, Any]) -> Any:
        allowed_vars = set(context.keys()) | set(self.SAFE_FUNCTIONS.keys())
        tree = ast.parse(action, mode="eval")

        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id not in allowed_vars:
                raise ValueError(f"动作中包含不允许的变量: {node.id}")
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id not in self.SAFE_FUNCTIONS:
                    raise ValueError(f"动作中包含不允许的函数调用: {node.func.id}")

        return eval(compile(tree, "<action>", "eval"), {"__builtins__": {}}, {**context, **self.SAFE_FUNCTIONS})

    def apply_rule(self, rule_id: int, batch_id: int = None) -> Tuple[int, List[Correction]]:
        rule = self.get_rule(rule_id)
        if not rule or not rule.is_active:
            raise ValueError(f"规则不存在或未激活: {rule_id}")

        query = self.db.query(MeterReading).filter(MeterReading.is_corrected == False)
        if batch_id:
            query = query.filter(MeterReading.batch_id == batch_id)

        readings = query.all()
        corrections = []
        applied_count = 0

        for reading in readings:
            context = self._build_context(reading)

            try:
                if self._evaluate_condition(rule.condition, context):
                    new_value = self._evaluate_action(rule.action, context)
                    if isinstance(new_value, (int, float)) and new_value >= 0:
                        correction = self._apply_correction(
                            reading=reading,
                            old_value=reading.reading_value,
                            new_value=float(new_value),
                            rule_id=rule.id,
                            note=f"规则自动修正: {rule.name} ({rule.code} v{rule.version})",
                        )
                        corrections.append(correction)
                        applied_count += 1
            except Exception as e:
                continue

        self.db.commit()

        if batch_id and applied_count > 0:
            detector = AnomalyDetector(self.db)
            detector.analyze_batch(batch_id)

        return applied_count, corrections

    def _build_context(self, reading: MeterReading) -> Dict[str, Any]:
        prev_reading = self.db.query(MeterReading).filter(
            MeterReading.meter_id == reading.meter_id,
            MeterReading.id < reading.id,
        ).order_by(MeterReading.id.desc()).first()

        context = {
            "reading_value": reading.reading_value,
            "reading_date": reading.reading_date,
            "reading_time": reading.reading_time,
            "old_value": prev_reading.reading_value if prev_reading else reading.reading_value,
            "original_value": reading.original_value,
            "store_id": reading.store.store_id if reading.store else "",
            "meter_id": reading.meter.meter_id if reading.meter else "",
        }
        return context

    def _apply_correction(
        self,
        reading: MeterReading,
        old_value: float,
        new_value: float,
        rule_id: int = None,
        note: str = None,
        applied_by: str = None,
    ) -> Correction:
        reading.reading_value = new_value
        reading.is_corrected = True

        correction = Correction(
            reading_id=reading.id,
            rule_id=rule_id,
            old_value=old_value,
            new_value=new_value,
            note=note,
            status=CORRECTION_STATUS["APPLIED"],
            applied_by=applied_by,
        )
        self.db.add(correction)

        self.db.query(Anomaly).filter(
            Anomaly.reading_id == reading.id,
            Anomaly.status == ANOMALY_STATUS["OPEN"],
        ).update({
            Anomaly.status: ANOMALY_STATUS["RESOLVED"],
            Anomaly.resolved_by: applied_by or "system",
            Anomaly.resolved_at: datetime.now(),
            Anomaly.resolution_note: note or "自动修正",
        })

        return correction

    def manual_correct(
        self,
        reading_id: int,
        new_value: float,
        note: str = None,
        applied_by: str = None,
    ) -> Correction:
        reading = self.db.query(MeterReading).filter(MeterReading.id == reading_id).first()
        if not reading:
            raise ValueError(f"读数记录不存在: {reading_id}")

        if new_value < 0:
            raise ValueError("新值不能为负数")

        correction = self._apply_correction(
            reading=reading,
            old_value=reading.reading_value,
            new_value=new_value,
            note=note,
            applied_by=applied_by,
        )

        self.db.commit()

        if reading.batch_id:
            detector = AnomalyDetector(self.db)
            detector.analyze_batch(reading.batch_id)

        return correction

    def rollback_correction(self, correction_id: int, rolled_back_by: str = None) -> Correction:
        correction = self.db.query(Correction).filter(
            Correction.id == correction_id
        ).first()

        if not correction:
            raise ValueError(f"修正记录不存在: {correction_id}")

        if correction.status == CORRECTION_STATUS["ROLLED_BACK"]:
            return correction

        reading = correction.reading
        if reading:
            reading.reading_value = correction.old_value
            reading.is_corrected = False

        correction.status = CORRECTION_STATUS["ROLLED_BACK"]
        correction.rolled_back_by = rolled_back_by
        correction.rolled_back_at = datetime.now()

        self.db.commit()

        if reading and reading.batch_id:
            detector = AnomalyDetector(self.db)
            detector.analyze_batch(reading.batch_id)

        return correction

    def get_corrections(self, batch_id: int = None, reading_id: int = None) -> List[Correction]:
        query = self.db.query(Correction).order_by(Correction.created_at.desc())

        if batch_id:
            query = query.join(MeterReading).filter(MeterReading.batch_id == batch_id)

        if reading_id:
            query = query.filter(Correction.reading_id == reading_id)

        return query.all()


import ast
