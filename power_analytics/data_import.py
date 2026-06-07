import hashlib
import json
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional

import pandas as pd
from sqlalchemy.orm import Session

from .config import BATCH_STATUS, OPTIONAL_FIELDS
from .database import get_db, create_db_session
from .models import (
    Batch,
    RawRow,
    Store,
    Meter,
    Device,
    MeterReading,
    FieldMapping,
)
from .validators import DataValidator, ValidationError


class DataImportService:
    def __init__(self, db: Session = None):
        self.db = db or create_db_session()
        self.validator = DataValidator()

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

        file_hash = self._calculate_file_hash(path)

        batch = Batch(
            name=batch_name or path.stem,
            description=description,
            file_name=path.name,
            file_hash=file_hash,
            total_rows=len(df),
            status=BATCH_STATUS["IMPORTED"],
            imported_by=imported_by,
        )
        self.db.add(batch)
        self.db.flush()

        store_devices = self._get_store_devices()
        all_rows_data = []
        valid_rows = []
        invalid_rows_count = 0

        for idx, row in df.iterrows():
            row_number = idx + 1
            row_data = row.to_dict()
            raw_data = json.dumps(row_data, ensure_ascii=False)

            is_row_valid, errors = self.validator.validate_row(
                row_data, row_number, store_devices
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
                has_critical_error = any(
                    e["code"] in ["MISSING_REQUIRED_FIELD", "INVALID_TIMEZONE", "INVALID_DEVICE", "DUPLICATE_REPORT"]
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

            valid_rows.append({
                "row_number": row_number,
                "reading_id": reading.id,
                "store_id": store_id,
                "meter_id": meter_id,
                "reading_date": reading_date,
                "reading_time": reading_time,
                "reading_value": reading_value,
            })

        duplicates = self.validator.validate_duplicates(valid_rows)
        if duplicates:
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

        batch.valid_rows = len(valid_rows)
        batch.invalid_rows = invalid_rows_count
        batch.status = BATCH_STATUS["VALIDATED"]

        self.db.commit()

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
