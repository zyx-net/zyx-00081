from datetime import datetime, time

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    Date,
    Time,
    Boolean,
    Text,
    ForeignKey,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import relationship

from .database import Base
from .config import (
    BATCH_STATUS,
    ANOMALY_SEVERITY,
    ANOMALY_STATUS,
    CORRECTION_STATUS,
    REQUIRED_FIELDS,
    OPTIONAL_FIELDS,
)


class Batch(Base):
    __tablename__ = "batches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    file_name = Column(String(500), nullable=False)
    file_hash = Column(String(64), nullable=True)
    total_rows = Column(Integer, default=0)
    valid_rows = Column(Integer, default=0)
    invalid_rows = Column(Integer, default=0)
    status = Column(String(50), default=BATCH_STATUS["IMPORTED"], nullable=False)
    imported_by = Column(String(100), nullable=True)
    committed_by = Column(String(100), nullable=True)
    committed_at = Column(DateTime, nullable=True)
    rollback_reason = Column(Text, nullable=True)
    rolled_back_by = Column(String(100), nullable=True)
    rolled_back_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    raw_rows = relationship("RawRow", back_populates="batch", cascade="all, delete-orphan")
    meter_readings = relationship("MeterReading", back_populates="batch", cascade="all, delete-orphan")
    anomalies = relationship("Anomaly", back_populates="batch", cascade="all, delete-orphan")


class RawRow(Base):
    __tablename__ = "raw_rows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    row_number = Column(Integer, nullable=False)
    raw_data = Column(Text, nullable=False)
    is_valid = Column(Boolean, default=True, nullable=False)
    validation_errors = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    batch = relationship("Batch", back_populates="raw_rows")

    __table_args__ = (
        UniqueConstraint("batch_id", "row_number", name="uq_batch_row"),
    )


class Store(Base):
    __tablename__ = "stores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    store_id = Column(String(50), unique=True, nullable=False)
    store_name = Column(String(255), nullable=True)
    opening_time = Column(Time, default=time(8, 0), nullable=False)
    closing_time = Column(Time, default=time(22, 0), nullable=False)
    timezone = Column(String(50), default="Asia/Shanghai", nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    meters = relationship("Meter", back_populates="store", cascade="all, delete-orphan")
    devices = relationship("Device", back_populates="store", cascade="all, delete-orphan")


class Meter(Base):
    __tablename__ = "meters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    meter_id = Column(String(50), unique=True, nullable=False)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    meter_name = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    store = relationship("Store", back_populates="meters")
    readings = relationship("MeterReading", back_populates="meter", cascade="all, delete-orphan")


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(50), nullable=False)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    device_name = Column(String(255), nullable=True)
    power_kw = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    store = relationship("Store", back_populates="devices")

    __table_args__ = (
        UniqueConstraint("device_id", "store_id", name="uq_device_store"),
    )


class MeterReading(Base):
    __tablename__ = "meter_readings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    raw_row_id = Column(Integer, ForeignKey("raw_rows.id"), nullable=True)
    meter_id = Column(Integer, ForeignKey("meters.id"), nullable=False)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=False)
    reading_date = Column(Date, nullable=False)
    reading_time = Column(Time, nullable=False)
    reading_value = Column(Float, nullable=False)
    reading_unit = Column(String(20), default="kWh", nullable=False)
    timezone = Column(String(50), nullable=False)
    operator = Column(String(100), nullable=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=True)
    original_value = Column(Float, nullable=True)
    is_corrected = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    batch = relationship("Batch", back_populates="meter_readings")
    meter = relationship("Meter", back_populates="readings")
    store = relationship("Store")
    device = relationship("Device")
    anomalies = relationship("Anomaly", back_populates="reading", cascade="all, delete-orphan")
    corrections = relationship("Correction", back_populates="reading", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_meter_datetime", "meter_id", "reading_date", "reading_time"),
        Index("idx_store_date", "store_id", "reading_date"),
    )


class AnomalyType(Base):
    __tablename__ = "anomaly_types"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(50), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    severity = Column(String(20), nullable=False)
    is_enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False)


class Anomaly(Base):
    __tablename__ = "anomalies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    reading_id = Column(Integer, ForeignKey("meter_readings.id"), nullable=True)
    anomaly_type_id = Column(Integer, ForeignKey("anomaly_types.id"), nullable=False)
    anomaly_code = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    description = Column(Text, nullable=False)
    details = Column(Text, nullable=True)
    status = Column(String(20), default=ANOMALY_STATUS["OPEN"], nullable=False)
    resolved_by = Column(String(100), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolution_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    batch = relationship("Batch", back_populates="anomalies")
    reading = relationship("MeterReading", back_populates="anomalies")
    anomaly_type = relationship("AnomalyType")


class CorrectionRule(Base):
    __tablename__ = "correction_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(50), nullable=False)
    name = Column(String(255), nullable=False)
    version = Column(String(50), nullable=False)
    description = Column(Text, nullable=True)
    condition = Column(Text, nullable=False)
    action = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_rule_code_version"),
    )


class Correction(Base):
    __tablename__ = "corrections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    reading_id = Column(Integer, ForeignKey("meter_readings.id"), nullable=False)
    rule_id = Column(Integer, ForeignKey("correction_rules.id"), nullable=True)
    old_value = Column(Float, nullable=False)
    new_value = Column(Float, nullable=False)
    note = Column(Text, nullable=True)
    status = Column(String(20), default=CORRECTION_STATUS["APPLIED"], nullable=False)
    applied_by = Column(String(100), nullable=True)
    rolled_back_by = Column(String(100), nullable=True)
    rolled_back_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    reading = relationship("MeterReading", back_populates="corrections")
    rule = relationship("CorrectionRule")


class FieldMapping(Base):
    __tablename__ = "field_mappings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mapping_name = Column(String(100), unique=True, nullable=False)
    source_field = Column(String(255), nullable=False)
    target_field = Column(String(255), nullable=False)
    data_type = Column(String(50), default="string", nullable=False)
    is_required = Column(Boolean, default=False, nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)


class ExportSummary(Base):
    __tablename__ = "export_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=True)
    export_type = Column(String(50), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_name = Column(String(255), nullable=False)
    record_count = Column(Integer, default=0)
    anomaly_count = Column(Integer, default=0)
    exported_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    batch = relationship("Batch")


ANOMALY_TYPES_DATA = [
    {
        "code": "PEAK_USAGE",
        "name": "尖峰用电",
        "description": "尖峰时段用电量超过平均值2倍",
        "severity": ANOMALY_SEVERITY["WARNING"],
    },
    {
        "code": "OFF_PEAK_USAGE",
        "name": "闭店后耗电",
        "description": "门店关闭后仍有超过阈值的用电",
        "severity": ANOMALY_SEVERITY["WARNING"],
    },
    {
        "code": "READING_DROP",
        "name": "读数倒退",
        "description": "电表读数较上次下降超过阈值",
        "severity": ANOMALY_SEVERITY["ERROR"],
    },
    {
        "code": "METER_MISSING",
        "name": "缺表",
        "description": "门店电表在本批次中无记录",
        "severity": ANOMALY_SEVERITY["ERROR"],
    },
    {
        "code": "DUPLICATE_REPORT",
        "name": "重复上报",
        "description": "同一门店同一时段多条记录",
        "severity": ANOMALY_SEVERITY["ERROR"],
    },
    {
        "code": "INVALID_TIMEZONE",
        "name": "无效时区",
        "description": "时区值不在支持列表中",
        "severity": ANOMALY_SEVERITY["ERROR"],
    },
    {
        "code": "INVALID_DEVICE",
        "name": "无效设备",
        "description": "引用不存在的设备",
        "severity": ANOMALY_SEVERITY["ERROR"],
    },
    {
        "code": "MISSING_REQUIRED_FIELD",
        "name": "缺少必填字段",
        "description": "缺少必填字段",
        "severity": ANOMALY_SEVERITY["ERROR"],
    },
]


def init_anomaly_types(db):
    for at in ANOMALY_TYPES_DATA:
        existing = db.query(AnomalyType).filter(AnomalyType.code == at["code"]).first()
        if not existing:
            db.add(AnomalyType(**at))


def init_default_field_mappings(db):
    all_fields = REQUIRED_FIELDS + OPTIONAL_FIELDS
    for field in all_fields:
        existing = db.query(FieldMapping).filter(FieldMapping.mapping_name == field).first()
        if not existing:
            db.add(FieldMapping(
                mapping_name=field,
                source_field=field,
                target_field=field,
                data_type="string" if field not in ["reading_value"] else "float",
                is_required=field in REQUIRED_FIELDS,
                description=f"{field} 字段映射",
            ))
