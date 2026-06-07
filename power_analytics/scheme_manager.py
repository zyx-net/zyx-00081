import json
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime

from sqlalchemy.orm import Session

from .config import SCHEMES_DIR, CONFLICT_STRATEGIES, CONFLICT_TYPES
from .database import get_db, create_db_session
from .models import ImportScheme, ImportAuditLog, IsolatedRecord


class SchemeManager:
    def __init__(self, db: Session = None):
        self.db = db or create_db_session()

    def close(self):
        if self.db:
            self.db.close()

    def create_scheme(
        self,
        name: str,
        field_mappings: Dict[str, str],
        default_timezone: str = "Asia/Shanghai",
        device_config_path: str = None,
        conflict_strategies: Dict[str, str] = None,
        description: str = None,
        created_by: str = None,
    ) -> ImportScheme:
        existing = self.db.query(ImportScheme).filter(ImportScheme.name == name).first()
        if existing:
            raise ValueError(f"导入方案已存在: {name}")

        default_strategies = {
            CONFLICT_TYPES["DUPLICATE_BATCH_NAME"]: CONFLICT_STRATEGIES["REJECT"],
            CONFLICT_TYPES["DUPLICATE_READING"]: CONFLICT_STRATEGIES["REJECT"],
            CONFLICT_TYPES["MISSING_DEVICE"]: CONFLICT_STRATEGIES["REJECT"],
        }
        if conflict_strategies:
            default_strategies.update(conflict_strategies)

        scheme = ImportScheme(
            name=name,
            description=description,
            field_mappings=json.dumps(field_mappings, ensure_ascii=False),
            default_timezone=default_timezone,
            device_config_path=device_config_path,
            conflict_strategies=json.dumps(default_strategies, ensure_ascii=False),
            created_by=created_by,
        )
        self.db.add(scheme)
        self.db.commit()
        return scheme

    def update_scheme(
        self,
        scheme_id: int,
        name: str = None,
        field_mappings: Dict[str, str] = None,
        default_timezone: str = None,
        device_config_path: str = None,
        conflict_strategies: Dict[str, str] = None,
        description: str = None,
    ) -> Optional[ImportScheme]:
        scheme = self.get_scheme(scheme_id)
        if not scheme:
            return None

        if name:
            existing = self.db.query(ImportScheme).filter(
                ImportScheme.name == name,
                ImportScheme.id != scheme_id,
            ).first()
            if existing:
                raise ValueError(f"方案名称已存在: {name}")
            scheme.name = name

        if field_mappings is not None:
            scheme.field_mappings = json.dumps(field_mappings, ensure_ascii=False)

        if default_timezone:
            scheme.default_timezone = default_timezone

        if device_config_path is not None:
            scheme.device_config_path = device_config_path

        if conflict_strategies is not None:
            scheme.conflict_strategies = json.dumps(conflict_strategies, ensure_ascii=False)

        if description is not None:
            scheme.description = description

        self.db.commit()
        return scheme

    def get_scheme(self, scheme_id: int) -> Optional[ImportScheme]:
        return self.db.query(ImportScheme).filter(ImportScheme.id == scheme_id).first()

    def get_scheme_by_name(self, name: str) -> Optional[ImportScheme]:
        return self.db.query(ImportScheme).filter(ImportScheme.name == name).first()

    def list_schemes(self, active_only: bool = True) -> List[ImportScheme]:
        query = self.db.query(ImportScheme).order_by(ImportScheme.name)
        if active_only:
            query = query.filter(ImportScheme.is_active == True)
        return query.all()

    def delete_scheme(self, scheme_id: int) -> bool:
        scheme = self.get_scheme(scheme_id)
        if not scheme:
            return False
        self.db.delete(scheme)
        self.db.commit()
        return True

    def deactivate_scheme(self, scheme_id: int) -> Optional[ImportScheme]:
        scheme = self.get_scheme(scheme_id)
        if scheme:
            scheme.is_active = False
            self.db.commit()
        return scheme

    def export_scheme_to_json(self, scheme_id: int, output_path: str = None) -> Path:
        scheme = self.get_scheme(scheme_id)
        if not scheme:
            raise ValueError(f"方案不存在: {scheme_id}")

        scheme_data = {
            "name": scheme.name,
            "description": scheme.description,
            "version": "1.0",
            "created_at": scheme.created_at.isoformat() if scheme.created_at else None,
            "created_by": scheme.created_by,
            "config": {
                "field_mappings": json.loads(scheme.field_mappings),
                "default_timezone": scheme.default_timezone,
                "device_config_path": scheme.device_config_path,
                "conflict_strategies": json.loads(scheme.conflict_strategies),
            }
        }

        if output_path:
            output_file = Path(output_path)
        else:
            output_file = SCHEMES_DIR / f"{scheme.name}.json"

        output_file.write_text(json.dumps(scheme_data, ensure_ascii=False, indent=2), encoding="utf-8")
        return output_file

    def import_scheme_from_json(self, file_path: str, created_by: str = None) -> ImportScheme:
        path = Path(file_path)
        if not path.exists():
            raise ValueError(f"文件不存在: {file_path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        name = data.get("name")
        if not name:
            raise ValueError("JSON文件缺少name字段")

        config = data.get("config", {})
        field_mappings = config.get("field_mappings", {})
        default_timezone = config.get("default_timezone", "Asia/Shanghai")
        device_config_path = config.get("device_config_path")
        conflict_strategies = config.get("conflict_strategies", {})
        description = data.get("description")

        return self.create_scheme(
            name=name,
            field_mappings=field_mappings,
            default_timezone=default_timezone,
            device_config_path=device_config_path,
            conflict_strategies=conflict_strategies,
            description=description,
            created_by=created_by,
        )

    def get_scheme_config(self, scheme: ImportScheme) -> Dict[str, Any]:
        return {
            "field_mappings": json.loads(scheme.field_mappings),
            "default_timezone": scheme.default_timezone,
            "device_config_path": scheme.device_config_path,
            "conflict_strategies": json.loads(scheme.conflict_strategies),
        }

    def log_audit(
        self,
        action: str,
        batch_id: int = None,
        scheme_id: int = None,
        conflict_type: str = None,
        conflict_strategy: str = None,
        details: Dict[str, Any] = None,
        row_number: int = None,
        handled_by: str = None,
    ) -> ImportAuditLog:
        log = ImportAuditLog(
            batch_id=batch_id,
            scheme_id=scheme_id,
            action=action,
            conflict_type=conflict_type,
            conflict_strategy=conflict_strategy,
            details=json.dumps(details, ensure_ascii=False) if details else None,
            row_number=row_number,
            handled_by=handled_by,
        )
        self.db.add(log)
        self.db.commit()
        return log

    def isolate_record(
        self,
        batch_id: int,
        raw_data: Dict[str, Any],
        row_number: int,
        conflict_type: str,
        reason: str,
    ) -> IsolatedRecord:
        record = IsolatedRecord(
            batch_id=batch_id,
            raw_data=json.dumps(raw_data, ensure_ascii=False),
            row_number=row_number,
            conflict_type=conflict_type,
            reason=reason,
        )
        self.db.add(record)
        self.db.commit()
        return record

    def get_audit_logs(
        self,
        batch_id: int = None,
        scheme_id: int = None,
        limit: int = 100,
    ) -> List[ImportAuditLog]:
        query = self.db.query(ImportAuditLog).order_by(ImportAuditLog.id.desc())
        if batch_id:
            query = query.filter(ImportAuditLog.batch_id == batch_id)
        if scheme_id:
            query = query.filter(ImportAuditLog.scheme_id == scheme_id)
        return query.limit(limit).all()

    def get_isolated_records(
        self,
        batch_id: int = None,
        resolution: str = None,
    ) -> List[IsolatedRecord]:
        query = self.db.query(IsolatedRecord).order_by(IsolatedRecord.id.desc())
        if batch_id:
            query = query.filter(IsolatedRecord.batch_id == batch_id)
        if resolution:
            query = query.filter(IsolatedRecord.resolution == resolution)
        return query.all()

    def load_device_config(self, scheme: ImportScheme) -> List[Dict[str, Any]]:
        if not scheme.device_config_path:
            return []

        path = Path(scheme.device_config_path)
        if not path.exists():
            return []

        import csv
        devices = []
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                devices.append(dict(row))
        return devices

    def apply_scheme_to_import(self, scheme_id: int, import_service) -> Dict[str, Any]:
        scheme = self.get_scheme(scheme_id)
        if not scheme:
            raise ValueError(f"方案不存在: {scheme_id}")

        if not scheme.is_active:
            raise ValueError(f"方案未激活: {scheme.name}")

        config = self.get_scheme_config(scheme)

        field_mappings = config["field_mappings"]
        for source, target in field_mappings.items():
            if source != target:
                try:
                    import_service.update_field_mapping(
                        mapping_name=source,
                        source_field=source,
                        target_field=target,
                    )
                except Exception:
                    pass

        return config
