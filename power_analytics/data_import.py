import hashlib
import json
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional

import pandas as pd
from sqlalchemy.orm import Session

from .config import BATCH_STATUS, OPTIONAL_FIELDS, CONFLICT_STRATEGIES, CONFLICT_TYPES
from .database import get_db, create_db_session
from .models import (
    Batch,
    RawRow,
    Store,
    Meter,
    Device,
    MeterReading,
    FieldMapping,
    ImportScheme,
    ImportAuditLog,
    IsolatedRecord,
)
from .validators import DataValidator, ValidationError
from .scheme_manager import SchemeManager


class DataImportService:
    def __init__(self, db: Session = None, scheme_id: int = None):
        self.db = db or create_db_session()
        self.validator = DataValidator()
        self.scheme_manager = SchemeManager(self.db)
        self.scheme = None
        self.scheme_config = None
        if scheme_id:
            self.scheme = self.scheme_manager.get_scheme(scheme_id)
            if self.scheme:
                self.scheme_config = self.scheme_manager.get_scheme_config(self.scheme)
                self._apply_scheme_field_mappings()

    def _apply_scheme_field_mappings(self):
        if not self.scheme_config:
            return
        field_mappings = self.scheme_config.get("field_mappings", {})
        for source, target in field_mappings.items():
            if source != target:
                try:
                    self.update_field_mapping(
                        mapping_name=source,
                        source_field=source,
                        target_field=target,
                    )
                except Exception:
                    pass

    def close(self):
        if self.db:
            self.db.close()

    def _load_file(self, file_path: str) -> pd.DataFrame:
        path = Path(file_path)
        if not path.exists():
            raise ValidationError(f"文件不存在: {file_path}", "FILE_NOT_FOUND")

        suffix = path.suffix.lower()
        if suffix in [".csv"]:
            return pd.read_csv(path, dtype=str, keep_default_na=False)
        elif suffix in [".xlsx", ".xls"]:
            return pd.read_excel(path, dtype=str, keep_default_na=False)
        else:
            raise ValidationError(f"不支持的文件格式: {suffix}", "UNSUPPORTED_FORMAT")

    def _get_field_mappings(self) -> Dict[str, str]:
        mappings = self.db.query(FieldMapping).all()
        return {m.source_field: m.target_field for m in mappings}

    def _apply_field_mappings(self, df: pd.DataFrame) -> pd.DataFrame:
        mappings = self._get_field_mappings()
        df = df.copy()
        rename_map = {}
        for col in df.columns:
            if col in mappings and mappings[col] != col:
                rename_map[col] = mappings[col]
        if rename_map:
            df = df.rename(columns=rename_map)
        return df

    def _get_store_devices(self) -> Dict[str, List[str]]:
        devices = self.db.query(Device).all()
        result = {}
        for d in devices:
            store_id = d.store.store_id if d.store else None
            if store_id:
                if store_id not in result:
                    result[store_id] = []
                result[store_id].append(d.device_id)
        return result

    def _get_or_create_store(self, store_id: str, store_name: str = None,
                           opening_time=None, closing_time=None,
                           timezone: str = None) -> Store:
        store = self.db.query(Store).filter(Store.store_id == store_id).first()
        if not store:
            store = Store(
                store_id=store_id,
                store_name=store_name,
                opening_time=opening_time,
                closing_time=closing_time,
                timezone=timezone or "Asia/Shanghai",
            )
            self.db.add(store)
            self.db.flush()
        else:
            if store_name and not store.store_name:
                store.store_name = store_name
            if opening_time:
                store.opening_time = opening_time
            if closing_time:
                store.closing_time = closing_time
            if timezone:
                store.timezone = timezone
        return store

    def _get_or_create_meter(self, meter_id: str, store: Store) -> Meter:
        meter = self.db.query(Meter).filter(Meter.meter_id == meter_id).first()
        if not meter:
            meter = Meter(meter_id=meter_id, store_id=store.id)
            self.db.add(meter)
            self.db.flush()
        return meter

    def _get_device(self, device_id: str, store: Store) -> Optional[Device]:
        return self.db.query(Device).filter(
            Device.device_id == device_id,
            Device.store_id == store.id,
        ).first()

    def _get_conflict_strategy(self, conflict_type: str) -> str:
        if self.scheme_config and "conflict_strategies" in self.scheme_config:
            return self.scheme_config["conflict_strategies"].get(
                conflict_type, CONFLICT_STRATEGIES["REJECT"]
            )
        return CONFLICT_STRATEGIES["REJECT"]

    def _apply_default_timezone(self, row_data: Dict[str, Any]) -> Dict[str, Any]:
        if self.scheme_config and not row_data.get("timezone"):
            default_tz = self.scheme_config.get("default_timezone")
            if default_tz:
                row_data["timezone"] = default_tz
        return row_data

    def _check_duplicate_batch_name(self, batch_name: str) -> Optional[Batch]:
        return self.db.query(Batch).filter(Batch.name == batch_name).first()

    def _handle_conflict(
        self,
        conflict_type: str,
        batch: Batch = None,
        row_data: Dict[str, Any] = None,
        row_number: int = None,
        details: Dict[str, Any] = None,
        handled_by: str = None,
    ) -> Tuple[str, Optional[Any]]:
        strategy = self._get_conflict_strategy(conflict_type)

        self.scheme_manager.log_audit(
            action="conflict_detected",
            batch_id=batch.id if batch else None,
            scheme_id=self.scheme.id if self.scheme else None,
            conflict_type=conflict_type,
            conflict_strategy=strategy,
            details=details,
            row_number=row_number,
            handled_by=handled_by,
        )

        if strategy == CONFLICT_STRATEGIES["REJECT"]:
            return "reject", None

        elif strategy == CONFLICT_STRATEGIES["ISOLATE"]:
            if batch and row_data and row_number:
                reason = details.get("message", str(details)) if details else "冲突隔离"
                isolated = self.scheme_manager.isolate_record(
                    batch_id=batch.id,
                    raw_data=row_data,
                    row_number=row_number,
                    conflict_type=conflict_type,
                    reason=reason,
                )
                return "isolate", isolated
            return "isolate", None

        elif strategy == CONFLICT_STRATEGIES["OVERWRITE"]:
            return "overwrite", None

        return "reject", None

    def _find_existing_reading(
        self,
        store_id: str,
        meter_id: str,
        reading_date,
        reading_time,
    ) -> Optional[MeterReading]:
        return self.db.query(MeterReading).join(Meter).join(Store).filter(
            Store.store_id == store_id,
            Meter.meter_id == meter_id,
            MeterReading.reading_date == reading_date,
            MeterReading.reading_time == reading_time,
        ).first()

    def import_file(
        self,
        file_path: str,
        batch_name: str = None,
        description: str = None,
        imported_by: str = None,
    ) -> Tuple[Batch, List[Dict[str, Any]]]:
        path = Path(file_path)
        df = self._load_file(file_path)
        df = self._apply_field_mappings(df)

        columns = list(df.columns)
        is_valid, missing_fields = self.validator.validate_required_fields(columns)
        if not is_valid:
            raise ValidationError(
                f"缺少必填列: {', '.join(missing_fields)}",
                "MISSING_REQUIRED_FIELD",
                {"missing_fields": missing_fields, "available_columns": columns},
            )

        final_batch_name = batch_name or path.stem

        if self.scheme:
            existing_batch = self._check_duplicate_batch_name(final_batch_name)
            if existing_batch:
                action, _ = self._handle_conflict(
                    conflict_type=CONFLICT_TYPES["DUPLICATE_BATCH_NAME"],
                    details={
                        "message": f"批次名称已存在: {final_batch_name} (批次ID: {existing_batch.id})",
                        "existing_batch_id": existing_batch.id,
                        "batch_name": final_batch_name,
                    },
                    handled_by=imported_by,
                )
                if action == "reject":
                    raise ValidationError(
                        f"批次名称已存在: {final_batch_name}",
                        "DUPLICATE_BATCH_NAME",
                        {"existing_batch_id": existing_batch.id, "batch_name": final_batch_name},
                    )
                elif action == "overwrite":
                    manager = __import__('power_analytics.batch_manager', fromlist=['BatchManager']).BatchManager(self.db)
                    manager.delete_batch(existing_batch.id)
                    self.scheme_manager.log_audit(
                        action="batch_overwritten",
                        batch_id=existing_batch.id,
                        scheme_id=self.scheme.id if self.scheme else None,
                        conflict_type=CONFLICT_TYPES["DUPLICATE_BATCH_NAME"],
                        conflict_strategy=CONFLICT_STRATEGIES["OVERWRITE"],
                        details={"old_batch_id": existing_batch.id, "new_batch_name": final_batch_name},
                        handled_by=imported_by,
                    )

        file_hash = self._calculate_file_hash(path)

        batch = Batch(
            name=final_batch_name,
            description=description,
            file_name=path.name,
            file_hash=file_hash,
            total_rows=len(df),
            status=BATCH_STATUS["IMPORTED"],
            imported_by=imported_by,
        )
        self.db.add(batch)
        self.db.flush()

        if self.scheme:
            self.scheme_manager.log_audit(
                action="batch_created",
                batch_id=batch.id,
                scheme_id=self.scheme.id if self.scheme else None,
                details={
                    "file_name": path.name,
                    "total_rows": len(df),
                    "batch_name": final_batch_name,
                },
                handled_by=imported_by,
            )

        store_devices = self._get_store_devices()
        if self.scheme_config and self.scheme_config.get("device_config_path"):
            devices_from_config = self.scheme_manager.load_device_config(self.scheme)
            for d in devices_from_config:
                store_id = d.get("store_id")
                device_id = d.get("device_id")
                if store_id and device_id:
                    if store_id not in store_devices:
                        store_devices[store_id] = []
                    if device_id not in store_devices[store_id]:
                        store_devices[store_id].append(device_id)

        all_rows_data = []
        valid_rows = []
        invalid_rows_count = 0
        isolated_count = 0
        overwritten_count = 0

        for idx, row in df.iterrows():
            row_number = idx + 1
            row_data = row.to_dict()
            row_data = self._apply_default_timezone(row_data)
            raw_data = json.dumps(row_data, ensure_ascii=False)

            is_row_valid, errors = self.validator.validate_row(
                row_data, row_number, store_devices
            )

            missing_device_error = None
            for e in errors:
                if e["code"] == "INVALID_DEVICE":
                    missing_device_error = e
                    break

            if missing_device_error:
                if self.scheme:
                    action, isolated = self._handle_conflict(
                        conflict_type=CONFLICT_TYPES["MISSING_DEVICE"],
                        batch=batch,
                        row_data=row_data,
                        row_number=row_number,
                        details=missing_device_error,
                        handled_by=imported_by,
                    )
                    if action == "reject":
                        self.db.rollback()
                        raise ValidationError(
                            f"第{row_number}行: {missing_device_error['message']}",
                            "INVALID_DEVICE",
                            {"row": row_number, "error": missing_device_error},
                        )
                    elif action == "isolate":
                        isolated_count += 1
                        raw_row = RawRow(
                            batch_id=batch.id,
                            row_number=row_number,
                            raw_data=raw_data,
                            is_valid=False,
                            validation_errors=json.dumps(errors, ensure_ascii=False),
                        )
                        self.db.add(raw_row)
                        self.db.flush()
                        invalid_rows_count += 1
                        continue
                    elif action == "overwrite":
                        is_row_valid = True
                        errors = [e for e in errors if e["code"] != "INVALID_DEVICE"]
                else:
                    self.db.rollback()
                    raise ValidationError(
                        f"第{row_number}行: {missing_device_error['message']}",
                        "INVALID_DEVICE",
                        {"row": row_number, "error": missing_device_error},
                    )

            raw_row = RawRow(
                batch_id=batch.id,
                row_number=row_number,
                raw_data=raw_data,
                is_valid=is_row_valid,
                validation_errors=json.dumps(errors, ensure_ascii=False) if errors else None,
            )
            self.db.add(raw_row)
            self.db.flush()

            if not is_row_valid:
                invalid_rows_count += 1
                critical_codes = ["MISSING_REQUIRED_FIELD", "INVALID_TIMEZONE", "DUPLICATE_REPORT"]
                if not self.scheme:
                    critical_codes.append("INVALID_DEVICE")
                has_critical_error = any(
                    e["code"] in critical_codes
                    for e in errors
                )
                if has_critical_error:
                    self.db.rollback()
                    error_msgs = "; ".join(e["message"] for e in errors)
                    raise ValidationError(
                        f"第{row_number}行: {error_msgs}",
                        errors[0]["code"],
                        {"row": row_number, "errors": errors},
                    )
                continue

            store_id = str(row_data.get("store_id", "")).strip()
            store_name = str(row_data.get("store_name", "")).strip() or None
            opening_time = self.validator.parse_time(row_data.get("opening_time"))
            closing_time = self.validator.parse_time(row_data.get("closing_time"))
            timezone = str(row_data.get("timezone", "")).strip()

            store = self._get_or_create_store(
                store_id, store_name, opening_time, closing_time, timezone
            )

            meter_id = str(row_data.get("meter_id", "")).strip()
            meter = self._get_or_create_meter(meter_id, store)

            reading_date = self.validator.parse_date(row_data.get("reading_date"))
            reading_time = self.validator.parse_time(row_data.get("reading_time"))
            reading_value = self.validator.parse_float(row_data.get("reading_value"))
            reading_unit = str(row_data.get("reading_unit", "kWh")).strip() or "kWh"
            operator = str(row_data.get("operator", "")).strip() or None
            device_id_str = str(row_data.get("device_id", "")).strip() or None

            device = None
            if device_id_str:
                device = self._get_device(device_id_str, store)
                if not device and missing_device_error:
                    pass

            reading_dict = {
                "row_number": row_number,
                "store_id": store_id,
                "meter_id": meter_id,
                "reading_date": reading_date,
                "reading_time": reading_time,
                "reading_value": reading_value,
            }

            if self.scheme:
                existing_reading = self._find_existing_reading(
                    store_id, meter_id, reading_date, reading_time
                )

                if existing_reading:
                    action, isolated = self._handle_conflict(
                        conflict_type=CONFLICT_TYPES["DUPLICATE_READING"],
                        batch=batch,
                        row_data=row_data,
                        row_number=row_number,
                        details={
                            "message": f"重复读数: {store_id} {meter_id} {reading_date} {reading_time}",
                            "existing_reading_id": existing_reading.id,
                            "existing_value": existing_reading.reading_value,
                            "new_value": reading_value,
                        },
                        handled_by=imported_by,
                    )
                    if action == "reject":
                        self.db.rollback()
                        raise ValidationError(
                            f"第{row_number}行: 重复读数记录",
                            "DUPLICATE_REPORT",
                            {"row": row_number, "existing_id": existing_reading.id},
                        )
                    elif action == "isolate":
                        isolated_count += 1
                        invalid_rows_count += 1
                        continue
                    elif action == "overwrite":
                        overwritten_count += 1
                        existing_reading.reading_value = reading_value
                        existing_reading.reading_unit = reading_unit
                        existing_reading.operator = operator
                        existing_reading.device_id = device.id if device else None
                        existing_reading.original_value = existing_reading.original_value or existing_reading.reading_value
                        reading_dict["reading_id"] = existing_reading.id
                        reading_dict["overwritten"] = True
                        valid_rows.append(reading_dict)
                        continue

            reading = MeterReading(
                batch_id=batch.id,
                raw_row_id=raw_row.id,
                meter_id=meter.id,
                store_id=store.id,
                reading_date=reading_date,
                reading_time=reading_time,
                reading_value=reading_value,
                reading_unit=reading_unit,
                timezone=timezone,
                operator=operator,
                device_id=device.id if device else None,
                original_value=reading_value,
            )
            self.db.add(reading)
            self.db.flush()

            reading_dict["reading_id"] = reading.id
            valid_rows.append(reading_dict)

        duplicates = self.validator.validate_duplicates(valid_rows)
        if duplicates:
            if not self.scheme:
                self.db.rollback()
                for d in duplicates:
                    if "reading_date" in d:
                        d["reading_date"] = str(d["reading_date"])
                    if "reading_time" in d:
                        d["reading_time"] = str(d["reading_time"])
                error_msgs = "; ".join(d["message"] for d in duplicates[:3])
                if len(duplicates) > 3:
                    error_msgs += f" (共{len(duplicates)}条重复记录)"
                raise ValidationError(
                    error_msgs,
                    "DUPLICATE_REPORT",
                    {"duplicates": duplicates},
                )

            handled_duplicates = []
            for d in duplicates:
                if "reading_date" in d:
                    d["reading_date"] = str(d["reading_date"])
                if "reading_time" in d:
                    d["reading_time"] = str(d["reading_time"])

                row_number = d.get("row")
                row_data = None
                for vr in valid_rows:
                    if vr.get("row_number") == row_number:
                        row_data = {
                            "store_id": vr.get("store_id"),
                            "meter_id": vr.get("meter_id"),
                            "reading_date": str(vr.get("reading_date")),
                            "reading_time": str(vr.get("reading_time")),
                            "reading_value": vr.get("reading_value"),
                        }
                        break

                action, isolated = self._handle_conflict(
                    conflict_type=CONFLICT_TYPES["DUPLICATE_READING"],
                    batch=batch,
                    row_data=row_data,
                    row_number=row_number,
                    details=d,
                    handled_by=imported_by,
                )
                if action == "reject":
                    self.db.rollback()
                    error_msgs = "; ".join(dup["message"] for dup in duplicates[:3])
                    if len(duplicates) > 3:
                        error_msgs += f" (共{len(duplicates)}条重复记录)"
                    raise ValidationError(
                        error_msgs,
                        "DUPLICATE_REPORT",
                        {"duplicates": duplicates},
                    )
                elif action == "isolate":
                    handled_duplicates.append(d)
                    isolated_count += 1
                    invalid_rows_count += 1
                    valid_rows = [vr for vr in valid_rows if vr.get("row_number") != row_number]
                elif action == "overwrite":
                    handled_duplicates.append(d)
                    overwritten_count += 1

            duplicates = [d for d in duplicates if d not in handled_duplicates]
            if duplicates:
                self.db.rollback()
                error_msgs = "; ".join(dup["message"] for dup in duplicates[:3])
                raise ValidationError(
                    error_msgs,
                    "DUPLICATE_REPORT",
                    {"duplicates": duplicates},
                )

        batch.valid_rows = len(valid_rows)
        batch.invalid_rows = invalid_rows_count
        batch.status = BATCH_STATUS["VALIDATED"]

        self.db.commit()

        if self.scheme:
            self.scheme_manager.log_audit(
                action="import_completed",
                batch_id=batch.id,
                scheme_id=self.scheme.id if self.scheme else None,
                details={
                    "valid_rows": len(valid_rows),
                    "invalid_rows": invalid_rows_count,
                    "isolated_rows": isolated_count,
                    "overwritten_rows": overwritten_count,
                },
                handled_by=imported_by,
            )

        return batch, valid_rows

    def update_field_mapping(
        self,
        mapping_name: str,
        source_field: str,
        target_field: str,
        data_type: str = "string",
        is_required: bool = False,
        description: str = None,
    ) -> FieldMapping:
        mapping = self.db.query(FieldMapping).filter(
            FieldMapping.mapping_name == mapping_name
        ).first()

        if mapping:
            mapping.source_field = source_field
            mapping.target_field = target_field
            mapping.data_type = data_type
            mapping.is_required = is_required
            mapping.description = description
        else:
            mapping = FieldMapping(
                mapping_name=mapping_name,
                source_field=source_field,
                target_field=target_field,
                data_type=data_type,
                is_required=is_required,
                description=description,
            )
            self.db.add(mapping)

        self.db.commit()
        return mapping

    def get_field_mappings(self) -> List[FieldMapping]:
        return self.db.query(FieldMapping).order_by(FieldMapping.mapping_name).all()

    @staticmethod
    def _calculate_file_hash(path: Path) -> str:
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
