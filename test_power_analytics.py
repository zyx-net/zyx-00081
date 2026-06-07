import os
import sys
import subprocess
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from power_analytics.config import DB_PATH, CONFLICT_STRATEGIES, CONFLICT_TYPES
from power_analytics.database import init_db, create_db_session, get_db, reset_engine_cache
from power_analytics.models import Batch, CorrectionRule, Correction, ExportSummary, Device, Store, ImportScheme, ImportAuditLog, IsolatedRecord
from power_analytics.data_import import DataImportService
from power_analytics.batch_manager import BatchManager
from power_analytics.anomaly_detector import AnomalyDetector
from power_analytics.correction_engine import RuleEngine
from power_analytics.report_exporter import ReportExporter
from power_analytics.scheme_manager import SchemeManager
from power_analytics.validators import ValidationError
from power_analytics.output_utils import safe_str, is_unicode_supported, get_console_encoding


TEST_DATA_DIR = Path(__file__).resolve().parent / "sample_data"


class TestOutputEncoding:
    def test_safe_str_with_emoji(self):
        test_str = "✅ 测试成功"
        result = safe_str(test_str)
        assert "测试成功" in result
        if not is_unicode_supported():
            assert "[OK]" in result
            assert "\u2705" not in result

    def test_safe_str_with_all_emojis(self):
        emojis = ["✅", "❌", "⚡", "📊", "📋", "🔍", "📝", "🎯", "🚨", "🔴", "🟡", "🔵", "📈"]
        for emoji in emojis:
            test_str = f"{emoji} test"
            result = safe_str(test_str)
            assert "test" in result

    def test_console_encoding_detection(self):
        encoding = get_console_encoding()
        assert isinstance(encoding, str)
        assert len(encoding) > 0

    def test_unicode_support_detection(self):
        result = is_unicode_supported()
        assert isinstance(result, bool)


class TestImportFailures:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        test_db = tmp_path / "test.db"
        os.environ["POWER_ANALYTICS_DB_PATH"] = str(test_db)
        reset_engine_cache()
        init_db(reset=False)
        self._setup_devices()
        self._cleanup_services = []
        yield
        for svc in self._cleanup_services:
            try:
                svc.close()
            except:
                pass
        for key in list(os.environ.keys()):
            if key == "POWER_ANALYTICS_DB_PATH":
                del os.environ[key]
        reset_engine_cache()
        try:
            if test_db.exists():
                test_db.unlink()
        except:
            pass

    def _track_service(self, svc):
        self._cleanup_services.append(svc)
        return svc

    def _setup_devices(self):
        with get_db() as db:
            for store_id in ["S001", "S002", "S003"]:
                store = Store(store_id=store_id, store_name=f"门店{store_id}")
                db.add(store)
                db.flush()
                device = Device(device_id=f"DEV{store_id}", store_id=store.id, device_name=f"设备{store_id}")
                db.add(device)
            db.commit()

    def test_import_missing_columns_failure(self):
        service = self._track_service(DataImportService())
        with pytest.raises(ValidationError) as excinfo:
            service.import_file(
                str(TEST_DATA_DIR / "error_missing_columns.csv"),
                batch_name="缺列测试"
            )
        assert "缺少必填列" in str(excinfo.value)
        assert excinfo.value.error_code == "MISSING_REQUIRED_FIELD"
        assert "meter_id" in str(excinfo.value)
        assert "timezone" in str(excinfo.value)
        self._assert_no_batches_created()

    def test_import_invalid_timezone_failure(self):
        service = self._track_service(DataImportService())
        with pytest.raises(ValidationError) as excinfo:
            service.import_file(
                str(TEST_DATA_DIR / "error_invalid_timezone.csv"),
                batch_name="时区错误测试"
            )
        assert "无效时区" in str(excinfo.value)
        assert excinfo.value.error_code == "INVALID_TIMEZONE"
        self._assert_no_batches_created()

    def test_import_invalid_device_failure(self):
        service = self._track_service(DataImportService())
        with pytest.raises(ValidationError) as excinfo:
            service.import_file(
                str(TEST_DATA_DIR / "error_invalid_device.csv"),
                batch_name="设备错误测试"
            )
        assert "设备不存在" in str(excinfo.value)
        assert excinfo.value.error_code == "INVALID_DEVICE"
        assert "DEV999" in str(excinfo.value)
        self._assert_no_batches_created()

    def test_import_duplicate_records_failure(self):
        dup_file = TEST_DATA_DIR / "test_duplicates.csv"
        self._create_duplicate_test_file(dup_file)
        try:
            service = self._track_service(DataImportService())
            with pytest.raises(ValidationError) as excinfo:
                service.import_file(str(dup_file), batch_name="重复测试")
            assert "重复记录" in str(excinfo.value)
            assert excinfo.value.error_code == "DUPLICATE_REPORT"
            self._assert_no_batches_created()
        finally:
            if dup_file.exists():
                dup_file.unlink()

    def test_failure_does_not_pollute_database(self):
        try:
            service = self._track_service(DataImportService())
            service.import_file(
                str(TEST_DATA_DIR / "error_missing_columns.csv"),
                batch_name="污染测试"
            )
        except ValidationError:
            pass
        self._assert_no_batches_created()
        self._assert_no_export_summaries()

    def _assert_no_batches_created(self):
        with get_db() as db:
            count = db.query(Batch).count()
            assert count == 0, f"数据库中不应有批次，但实际有 {count} 个"

    def _assert_no_export_summaries(self):
        with get_db() as db:
            count = db.query(ExportSummary).count()
            assert count == 0, f"数据库中不应有导出汇总，但实际有 {count} 个"

    def _create_duplicate_test_file(self, filepath):
        import csv
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow([
                "store_id", "store_name", "meter_id", "reading_date", "reading_time",
                "reading_value", "reading_unit", "timezone", "opening_time", "closing_time", "operator"
            ])
            writer.writerow([
                "S001", "测试店", "M001", "2024-06-01", "08:00",
                "1000", "kWh", "Asia/Shanghai", "08:00", "22:00", "测试员"
            ])
            writer.writerow([
                "S001", "测试店", "M001", "2024-06-01", "08:00",
                "1000", "kWh", "Asia/Shanghai", "08:00", "22:00", "测试员"
            ])


class TestSuccessfulWorkflow:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        test_db = tmp_path / "test.db"
        os.environ["POWER_ANALYTICS_DB_PATH"] = str(test_db)
        reset_engine_cache()
        init_db(reset=False)
        self._setup_devices()
        self._cleanup_services = []
        yield
        for svc in self._cleanup_services:
            try:
                svc.close()
            except:
                pass
        for key in list(os.environ.keys()):
            if key == "POWER_ANALYTICS_DB_PATH":
                del os.environ[key]
        reset_engine_cache()
        try:
            if test_db.exists():
                test_db.unlink()
        except:
            pass

    def _track_service(self, svc):
        self._cleanup_services.append(svc)
        return svc

    def _setup_devices(self):
        with get_db() as db:
            for store_id in ["S001", "S002", "S003"]:
                store = Store(store_id=store_id, store_name=f"门店{store_id}")
                db.add(store)
                db.flush()
                device = Device(device_id=f"DEV{store_id}", store_id=store.id, device_name=f"设备{store_id}")
                db.add(device)
            db.commit()

    def test_full_workflow(self):
        service = self._track_service(DataImportService())
        batch, rows = service.import_file(
            str(TEST_DATA_DIR / "with_anomalies.csv"),
            batch_name="完整流程测试",
            imported_by="测试员"
        )
        assert batch.id is not None
        assert batch.total_rows == 14
        assert batch.valid_rows == 14
        assert batch.status == "validated"

        detector = self._track_service(AnomalyDetector())
        count, anomalies = detector.analyze_batch(batch.id)
        assert count > 0
        assert len(anomalies) == count

        manager = self._track_service(BatchManager())
        details = manager.get_batch_details(batch.id)
        assert details["anomalies_count"] == count
        assert details["readings_count"] == 14

        engine = self._track_service(RuleEngine())
        rule = engine.create_rule(
            code="TEST_RULE",
            name="测试规则",
            version="v1.0",
            condition="reading_value > 0",
            action="reading_value + 1",
            description="测试规则",
            created_by="测试员"
        )
        assert rule.id is not None

        count, corrections = engine.apply_rule(rule.id, batch_id=batch.id)
        assert count >= 0

        reading_id = details["anomalies"][0].reading_id
        if reading_id:
            correction = engine.manual_correct(
                reading_id=reading_id,
                new_value=99999.0,
                note="人工测试修正",
                applied_by="测试员"
            )
            assert correction.id is not None

            engine.rollback_correction(correction.id, rolled_back_by="测试员")

        exporter = self._track_service(ReportExporter())
        html_path = exporter.export_html(batch.id, exported_by="测试员")
        assert html_path.exists()
        assert html_path.stat().st_size > 0

        csv_path = exporter.export_csv(batch.id, exported_by="测试员")
        assert csv_path.exists()
        assert csv_path.stat().st_size > 0

        manager.rollback_batch(batch.id, reason="测试回滚", rolled_back_by="测试员")
        details = manager.get_batch_details(batch.id)
        assert details["batch"].status == "rolled_back"

    def test_persistence_after_restart(self):
        service = self._track_service(DataImportService())
        batch1, _ = service.import_file(
            str(TEST_DATA_DIR / "with_anomalies.csv"),
            batch_name="持久化测试1",
            imported_by="测试员"
        )
        batch2, _ = service.import_file(
            str(TEST_DATA_DIR / "normal_readings.csv"),
            batch_name="持久化测试2",
            imported_by="测试员"
        )

        engine = self._track_service(RuleEngine())
        rule = engine.create_rule(
            code="PERSIST_RULE",
            name="持久化规则",
            version="v1.0",
            condition="reading_value > 0",
            action="reading_value",
            description="持久化测试规则"
        )

        batch1_id = batch1.id
        batch2_id = batch2.id
        rule_id = rule.id

        service.close()
        engine.close()

        manager = self._track_service(BatchManager())
        b1 = manager.get_batch(batch1_id)
        b2 = manager.get_batch(batch2_id)
        assert b1 is not None
        assert b2 is not None
        assert b1.name == "持久化测试1"
        assert b2.name == "持久化测试2"

        engine2 = self._track_service(RuleEngine())
        r = engine2.get_rule(rule_id)
        assert r is not None
        assert r.code == "PERSIST_RULE"
        assert r.version == "v1.0"

        manager.rollback_batch(batch1_id, reason="测试", rolled_back_by="测试")

        manager.close()
        engine2.close()

        manager2 = self._track_service(BatchManager())
        b1_after = manager2.get_batch(batch1_id)
        assert b1_after.status == "rolled_back"
        b2_after = manager2.get_batch(batch2_id)
        assert b2_after.status != "rolled_back"

    def test_no_pollution_between_batches(self):
        service = self._track_service(DataImportService())
        batch1, _ = service.import_file(
            str(TEST_DATA_DIR / "with_anomalies.csv"),
            batch_name="批次1",
            imported_by="测试员"
        )

        try:
            service2 = self._track_service(DataImportService())
            service2.import_file(
                str(TEST_DATA_DIR / "error_invalid_device.csv"),
                batch_name="失败批次"
            )
        except ValidationError:
            pass

        manager = self._track_service(BatchManager())
        batches = manager.list_batches()
        assert len(batches) == 1
        assert batches[0].name == "批次1"

        b1 = manager.get_batch(batch1.id)
        assert b1 is not None
        assert b1.valid_rows == 14


class TestCLIIntegration:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = Path(self.test_dir) / "test.db"
        os.environ["POWER_ANALYTICS_DB_PATH"] = str(self.db_path)
        yield
        del os.environ["POWER_ANALYTICS_DB_PATH"]
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _run_cli(self, args, cwd=None):
        cmd = [sys.executable, "-m", "power_analytics.cli"] + args
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd or Path(__file__).resolve().parent,
            env=env,
        )
        return result

    def test_cli_init(self):
        result = self._run_cli(["init", "--reset-db"])
        assert result.returncode == 0
        assert ("系统初始化成功" in result.stdout or "[OK]" in result.stdout)

    def test_cli_import_missing_columns(self):
        self._run_cli(["init", "--reset-db"])
        result = self._run_cli([
            "import-file",
            str(TEST_DATA_DIR / "error_missing_columns.csv"),
            "-n", "CLI缺列测试"
        ])
        assert result.returncode != 0
        assert ("缺少必填列" in result.stderr or "[ERROR]" in result.stderr)
        assert "meter_id" in result.stderr

    def test_cli_import_invalid_device(self):
        self._run_cli(["init", "--reset-db"])
        result = self._run_cli([
            "import-file",
            str(TEST_DATA_DIR / "error_invalid_device.csv"),
            "-n", "CLI设备错误测试"
        ])
        assert result.returncode != 0
        assert ("设备不存在" in result.stderr or "[ERROR]" in result.stderr)
        assert "DEV999" in result.stderr

    def test_cli_import_invalid_timezone(self):
        self._run_cli(["init", "--reset-db"])
        result = self._run_cli([
            "import-file",
            str(TEST_DATA_DIR / "error_invalid_timezone.csv"),
            "-n", "CLI时区测试"
        ])
        assert result.returncode != 0
        assert ("无效时区" in result.stderr or "[ERROR]" in result.stderr)

    def test_cli_workflow(self):
        self._run_cli(["init", "--reset-db"])

        result = self._run_cli([
            "import-file",
            str(TEST_DATA_DIR / "with_anomalies.csv"),
            "-n", "CLI完整测试",
            "-u", "测试员"
        ])
        assert result.returncode == 0
        assert ("导入成功" in result.stdout or "[OK]" in result.stdout)
        assert "批次ID: 1" in result.stdout

        result = self._run_cli(["analyze", "1"])
        assert result.returncode == 0
        assert ("分析完成" in result.stdout or "[OK]" in result.stdout)

        result = self._run_cli(["show", "1"])
        assert result.returncode == 0

        result = self._run_cli(["export", "html", "1", "-u", "测试员"])
        assert result.returncode == 0
        assert ("HTML报告导出成功" in result.stdout or "[OK]" in result.stdout)

        result = self._run_cli(["rollback", "1", "-r", "CLI测试回滚", "-u", "测试员"])
        assert result.returncode == 0
        assert ("已回滚" in result.stdout or "[OK]" in result.stdout)

        result = self._run_cli(["list"])
        assert result.returncode == 0
        assert "已回滚" in result.stdout


class TestImportSchemeManagement:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.test_dir = tmp_path
        self.db_path = tmp_path / "test.db"
        os.environ["POWER_ANALYTICS_DB_PATH"] = str(self.db_path)
        reset_engine_cache()
        init_db(reset=False)
        self._setup_devices()
        self._cleanup_services = []
        yield
        for svc in self._cleanup_services:
            try:
                svc.close()
            except:
                pass
        for key in list(os.environ.keys()):
            if key == "POWER_ANALYTICS_DB_PATH":
                del os.environ[key]
        reset_engine_cache()

    def _track_service(self, svc):
        self._cleanup_services.append(svc)
        return svc

    def _setup_devices(self):
        with get_db() as db:
            for store_id in ["S001", "S002", "S003"]:
                store = Store(store_id=store_id, store_name=f"门店{store_id}")
                db.add(store)
                db.flush()
                device_num = store_id.replace("S", "")
                device = Device(device_id=f"DEVS{device_num}", store_id=store.id, device_name=f"设备{store_id}")
                db.add(device)
            db.commit()

    def test_create_scheme(self):
        manager = self._track_service(SchemeManager())
        field_mappings = {
            "store_id": "store_id",
            "meter_id": "meter_id",
            "reading_date": "reading_date",
            "reading_value": "reading_value",
            "timezone": "timezone",
        }
        strategies = {
            CONFLICT_TYPES["DUPLICATE_BATCH_NAME"]: CONFLICT_STRATEGIES["REJECT"],
            CONFLICT_TYPES["DUPLICATE_READING"]: CONFLICT_STRATEGIES["ISOLATE"],
            CONFLICT_TYPES["MISSING_DEVICE"]: CONFLICT_STRATEGIES["OVERWRITE"],
        }
        scheme = manager.create_scheme(
            name="测试方案1",
            field_mappings=field_mappings,
            default_timezone="Asia/Shanghai",
            device_config_path=str(TEST_DATA_DIR / "devices_config.csv"),
            conflict_strategies=strategies,
            description="测试导入方案",
            created_by="测试员",
        )
        assert scheme.id is not None
        assert scheme.name == "测试方案1"
        assert scheme.default_timezone == "Asia/Shanghai"

        config = manager.get_scheme_config(scheme)
        assert config["conflict_strategies"][CONFLICT_TYPES["DUPLICATE_BATCH_NAME"]] == "reject"
        assert config["conflict_strategies"][CONFLICT_TYPES["DUPLICATE_READING"]] == "isolate"
        assert config["conflict_strategies"][CONFLICT_TYPES["MISSING_DEVICE"]] == "overwrite"

    def test_scheme_persistence_after_restart(self):
        manager = self._track_service(SchemeManager())
        field_mappings = {"store_id": "store_id", "meter_id": "meter_id"}
        strategies = {
            CONFLICT_TYPES["DUPLICATE_BATCH_NAME"]: CONFLICT_STRATEGIES["OVERWRITE"],
        }
        scheme = manager.create_scheme(
            name="持久化测试方案",
            field_mappings=field_mappings,
            conflict_strategies=strategies,
            created_by="测试员",
        )
        scheme_id = scheme.id
        manager.close()

        manager2 = self._track_service(SchemeManager())
        loaded = manager2.get_scheme(scheme_id)
        assert loaded is not None
        assert loaded.name == "持久化测试方案"

        config = manager2.get_scheme_config(loaded)
        assert config["conflict_strategies"][CONFLICT_TYPES["DUPLICATE_BATCH_NAME"]] == "overwrite"

    def test_export_import_scheme(self):
        manager = self._track_service(SchemeManager())
        field_mappings = {"store_id": "store_id", "meter_id": "meter_id", "reading_value": "reading_value"}
        strategies = {
            CONFLICT_TYPES["DUPLICATE_READING"]: CONFLICT_STRATEGIES["ISOLATE"],
        }
        scheme = manager.create_scheme(
            name="导出测试方案",
            field_mappings=field_mappings,
            default_timezone="UTC",
            conflict_strategies=strategies,
            description="导出测试",
        )

        export_path = self.test_dir / "exported_scheme.json"
        result_path = manager.export_scheme_to_json(scheme.id, output_path=str(export_path))
        assert result_path.exists()

        with open(result_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["name"] == "导出测试方案"
        assert data["config"]["default_timezone"] == "UTC"
        assert data["config"]["field_mappings"]["reading_value"] == "reading_value"

        manager.close()
        reset_engine_cache()

        manager2 = self._track_service(SchemeManager())
        existing = manager2.get_scheme_by_name("导出测试方案")
        if existing:
            manager2.delete_scheme(existing.id)
        imported = manager2.import_scheme_from_json(str(export_path), created_by="导入员")
        assert imported.id is not None
        assert imported.name == "导出测试方案"
        assert imported.created_by == "导入员"

        config = manager2.get_scheme_config(imported)
        assert config["default_timezone"] == "UTC"

    def test_list_delete_scheme(self):
        manager = self._track_service(SchemeManager())
        for i in range(3):
            manager.create_scheme(
                name=f"列表测试方案{i}",
                field_mappings={"store_id": "store_id"},
                created_by="测试员",
            )

        schemes = manager.list_schemes(active_only=False)
        assert len(schemes) == 3

        success = manager.delete_scheme(schemes[0].id)
        assert success

        schemes_after = manager.list_schemes(active_only=False)
        assert len(schemes_after) == 2

    def test_duplicate_scheme_name_rejected(self):
        manager = self._track_service(SchemeManager())
        manager.create_scheme(
            name="重名测试方案",
            field_mappings={"store_id": "store_id"},
            created_by="测试员",
        )
        with pytest.raises(ValueError) as excinfo:
            manager.create_scheme(
                name="重名测试方案",
                field_mappings={"meter_id": "meter_id"},
            )
        assert "已存在" in str(excinfo.value)


class TestConflictStrategies:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.test_dir = tmp_path
        self.db_path = tmp_path / "test.db"
        os.environ["POWER_ANALYTICS_DB_PATH"] = str(self.db_path)
        reset_engine_cache()
        init_db(reset=False)
        self._setup_devices()
        self._cleanup_services = []
        yield
        for svc in self._cleanup_services:
            try:
                svc.close()
            except:
                pass
        for key in list(os.environ.keys()):
            if key == "POWER_ANALYTICS_DB_PATH":
                del os.environ[key]
        reset_engine_cache()

    def _track_service(self, svc):
        self._cleanup_services.append(svc)
        return svc

    def _setup_devices(self):
        with get_db() as db:
            for store_id in ["S001", "S002", "S003"]:
                store = Store(store_id=store_id, store_name=f"门店{store_id}")
                db.add(store)
                db.flush()
                device_num = store_id.replace("S", "")
                device = Device(device_id=f"DEVS{device_num}", store_id=store.id, device_name=f"设备{store_id}")
                db.add(device)
            db.commit()

    def _create_scheme(self, duplicate_batch_name_strategy, duplicate_reading_strategy, missing_device_strategy):
        manager = self._track_service(SchemeManager())
        field_mappings = {
            "store_id": "store_id",
            "store_name": "store_name",
            "meter_id": "meter_id",
            "reading_date": "reading_date",
            "reading_time": "reading_time",
            "reading_value": "reading_value",
            "reading_unit": "reading_unit",
            "timezone": "timezone",
            "opening_time": "opening_time",
            "closing_time": "closing_time",
            "operator": "operator",
            "device_id": "device_id",
        }
        strategies = {
            CONFLICT_TYPES["DUPLICATE_BATCH_NAME"]: duplicate_batch_name_strategy,
            CONFLICT_TYPES["DUPLICATE_READING"]: duplicate_reading_strategy,
            CONFLICT_TYPES["MISSING_DEVICE"]: missing_device_strategy,
        }
        return manager.create_scheme(
            name="冲突测试方案",
            field_mappings=field_mappings,
            conflict_strategies=strategies,
            created_by="测试员",
        )

    def test_duplicate_batch_name_reject(self):
        scheme = self._create_scheme(
            CONFLICT_STRATEGIES["REJECT"],
            CONFLICT_STRATEGIES["REJECT"],
            CONFLICT_STRATEGIES["REJECT"],
        )
        service = self._track_service(DataImportService(scheme_id=scheme.id))
        batch1, _ = service.import_file(
            str(TEST_DATA_DIR / "duplicate_name_test.csv"),
            batch_name="重名测试批次",
            imported_by="测试员",
        )
        assert batch1.id is not None

        service2 = self._track_service(DataImportService(scheme_id=scheme.id))
        with pytest.raises(ValidationError) as excinfo:
            service2.import_file(
                str(TEST_DATA_DIR / "duplicate_name_test.csv"),
                batch_name="重名测试批次",
                imported_by="测试员",
            )
        assert "批次名称已存在" in str(excinfo.value)
        assert excinfo.value.error_code == "DUPLICATE_BATCH_NAME"

    def test_duplicate_batch_name_overwrite(self):
        scheme = self._create_scheme(
            CONFLICT_STRATEGIES["OVERWRITE"],
            CONFLICT_STRATEGIES["REJECT"],
            CONFLICT_STRATEGIES["REJECT"],
        )
        service = self._track_service(DataImportService(scheme_id=scheme.id))
        batch1, _ = service.import_file(
            str(TEST_DATA_DIR / "duplicate_name_test.csv"),
            batch_name="覆盖测试批次",
            imported_by="测试员",
        )
        batch1_id = batch1.id
        service.close()

        service2 = self._track_service(DataImportService(scheme_id=scheme.id))
        batch2, _ = service2.import_file(
            str(TEST_DATA_DIR / "duplicate_name_test.csv"),
            batch_name="覆盖测试批次",
            imported_by="测试员",
        )
        batch2_id = batch2.id
        batch2_name = batch2.name
        service2.close()

        manager = self._track_service(BatchManager())
        all_batches = manager.list_batches()
        assert len(all_batches) == 1

        assert batch2_id is not None
        assert batch2_name == "覆盖测试批次"

        scheme_manager = self._track_service(SchemeManager())
        all_logs = scheme_manager.get_audit_logs()
        assert any(log.action == "batch_overwritten" for log in all_logs)

    def test_duplicate_reading_isolate(self):
        scheme = self._create_scheme(
            CONFLICT_STRATEGIES["REJECT"],
            CONFLICT_STRATEGIES["ISOLATE"],
            CONFLICT_STRATEGIES["REJECT"],
        )
        service = self._track_service(DataImportService(scheme_id=scheme.id))
        batch, rows = service.import_file(
            str(TEST_DATA_DIR / "duplicate_reading_test.csv"),
            batch_name="重复读数隔离测试",
            imported_by="测试员",
        )

        assert batch.valid_rows == 2
        assert batch.invalid_rows == 1

        scheme_manager = self._track_service(SchemeManager())
        isolated = scheme_manager.get_isolated_records(batch_id=batch.id)
        assert len(isolated) == 1
        assert isolated[0].conflict_type == CONFLICT_TYPES["DUPLICATE_READING"]
        assert isolated[0].resolution == "pending"

    def test_duplicate_reading_overwrite(self):
        scheme = self._create_scheme(
            CONFLICT_STRATEGIES["REJECT"],
            CONFLICT_STRATEGIES["OVERWRITE"],
            CONFLICT_STRATEGIES["REJECT"],
        )
        service = self._track_service(DataImportService(scheme_id=scheme.id))
        batch, rows = service.import_file(
            str(TEST_DATA_DIR / "duplicate_reading_test.csv"),
            batch_name="重复读数覆盖测试",
            imported_by="测试员",
        )

        assert batch.valid_rows == 3

        with get_db() as db:
            from power_analytics.models import MeterReading, Meter, Store
            readings = db.query(MeterReading).join(Meter).join(Store).filter(
                Store.store_id == "S001",
                Meter.meter_id == "M001",
            ).all()
            assert len(readings) == 2
            for r in readings:
                if str(r.reading_date) == "2024-06-01" and str(r.reading_time) == "08:00":
                    assert r.reading_value == 1050.0

    def test_missing_device_isolate(self):
        scheme = self._create_scheme(
            CONFLICT_STRATEGIES["REJECT"],
            CONFLICT_STRATEGIES["REJECT"],
            CONFLICT_STRATEGIES["ISOLATE"],
        )
        service = self._track_service(DataImportService(scheme_id=scheme.id))
        batch, rows = service.import_file(
            str(TEST_DATA_DIR / "error_invalid_device.csv"),
            batch_name="缺失设备隔离测试",
            imported_by="测试员",
        )

        assert batch.valid_rows >= 0
        assert batch.invalid_rows >= 1

        scheme_manager = self._track_service(SchemeManager())
        isolated = scheme_manager.get_isolated_records(batch_id=batch.id)
        assert len(isolated) >= 1
        assert isolated[0].conflict_type == CONFLICT_TYPES["MISSING_DEVICE"]

    def test_missing_device_overwrite(self):
        scheme = self._create_scheme(
            CONFLICT_STRATEGIES["REJECT"],
            CONFLICT_STRATEGIES["REJECT"],
            CONFLICT_STRATEGIES["OVERWRITE"],
        )
        service = self._track_service(DataImportService(scheme_id=scheme.id))
        batch, rows = service.import_file(
            str(TEST_DATA_DIR / "error_invalid_device.csv"),
            batch_name="缺失设备覆盖测试",
            imported_by="测试员",
        )

        assert batch.valid_rows > 0
        assert len(rows) > 0

    def test_audit_logging(self):
        scheme = self._create_scheme(
            CONFLICT_STRATEGIES["REJECT"],
            CONFLICT_STRATEGIES["ISOLATE"],
            CONFLICT_STRATEGIES["ISOLATE"],
        )
        service = self._track_service(DataImportService(scheme_id=scheme.id))
        batch, _ = service.import_file(
            str(TEST_DATA_DIR / "duplicate_reading_test.csv"),
            batch_name="审计日志测试",
            imported_by="测试员",
        )

        scheme_manager = self._track_service(SchemeManager())
        logs = scheme_manager.get_audit_logs(batch_id=batch.id)

        actions = [log.action for log in logs]
        assert "batch_created" in actions
        assert "conflict_detected" in actions
        assert "import_completed" in actions

        conflict_logs = [log for log in logs if log.conflict_type == CONFLICT_TYPES["DUPLICATE_READING"]]
        assert len(conflict_logs) > 0
        assert conflict_logs[0].conflict_strategy == CONFLICT_STRATEGIES["ISOLATE"]


class TestRollbackPreservation:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.test_dir = tmp_path
        self.db_path = tmp_path / "test.db"
        os.environ["POWER_ANALYTICS_DB_PATH"] = str(self.db_path)
        reset_engine_cache()
        init_db(reset=False)
        self._setup_devices()
        self._cleanup_services = []
        yield
        for svc in self._cleanup_services:
            try:
                svc.close()
            except:
                pass
        for key in list(os.environ.keys()):
            if key == "POWER_ANALYTICS_DB_PATH":
                del os.environ[key]
        reset_engine_cache()

    def _track_service(self, svc):
        self._cleanup_services.append(svc)
        return svc

    def _setup_devices(self):
        with get_db() as db:
            for store_id in ["S001", "S002", "S003"]:
                store = Store(store_id=store_id, store_name=f"门店{store_id}")
                db.add(store)
                db.flush()
                device_num = store_id.replace("S", "")
                device = Device(device_id=f"DEVS{device_num}", store_id=store.id, device_name=f"设备{store_id}")
                db.add(device)
            db.commit()

    def test_batch_rollback_preserves_audit_logs(self):
        scheme_manager = self._track_service(SchemeManager())
        scheme = scheme_manager.create_scheme(
            name="回滚测试方案",
            field_mappings={"store_id": "store_id", "meter_id": "meter_id"},
            conflict_strategies={
                CONFLICT_TYPES["DUPLICATE_READING"]: CONFLICT_STRATEGIES["ISOLATE"],
            },
            created_by="测试员",
        )

        service = self._track_service(DataImportService(scheme_id=scheme.id))
        batch, _ = service.import_file(
            str(TEST_DATA_DIR / "duplicate_reading_test.csv"),
            batch_name="回滚测试批次",
            imported_by="测试员",
        )
        batch_id = batch.id

        manager = self._track_service(BatchManager())
        manager.rollback_batch(batch_id, reason="测试回滚", rolled_back_by="测试员")

        logs = scheme_manager.get_audit_logs(batch_id=batch_id)
        assert len(logs) > 0

        isolated = scheme_manager.get_isolated_records(batch_id=batch_id)
        for iso in isolated:
            assert iso.resolution == "rolled_back"

    def test_correction_rollback_still_works(self):
        service = self._track_service(DataImportService())
        batch, _ = service.import_file(
            str(TEST_DATA_DIR / "normal_readings.csv"),
            batch_name="修正回滚测试",
            imported_by="测试员",
        )

        detector = self._track_service(AnomalyDetector())
        detector.analyze_batch(batch.id)

        manager = self._track_service(BatchManager())
        details = manager.get_batch_details(batch.id)

        if details["readings_count"] > 0:
            reading_id = None
            for r in details.get("readings", []):
                reading_id = r.id if hasattr(r, 'id') else None
                break

            if reading_id is None:
                with get_db() as db:
                    from power_analytics.models import MeterReading
                    r = db.query(MeterReading).filter(MeterReading.batch_id == batch.id).first()
                    if r:
                        reading_id = r.id

            if reading_id:
                engine = self._track_service(RuleEngine())
                correction = engine.manual_correct(
                    reading_id=reading_id,
                    new_value=99999.0,
                    note="测试修正",
                    applied_by="测试员",
                )
                correction_id = correction.id

                rolled_back = engine.rollback_correction(correction_id, rolled_back_by="测试员")
                assert rolled_back.status == "rolled_back"

                with get_db() as db:
                    from power_analytics.models import MeterReading
                    r = db.query(MeterReading).filter(MeterReading.id == reading_id).first()
                    assert r.reading_value != 99999.0
                    assert r.is_corrected == False


class TestSchemePersistenceAcrossRestarts:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.test_dir = tmp_path
        self.db_path = tmp_path / "persistent_test.db"
        self._original_env = os.environ.get("POWER_ANALYTICS_DB_PATH")
        os.environ["POWER_ANALYTICS_DB_PATH"] = str(self.db_path)
        reset_engine_cache()
        init_db(reset=False)
        yield
        reset_engine_cache()
        if self._original_env:
            os.environ["POWER_ANALYTICS_DB_PATH"] = self._original_env
        else:
            if "POWER_ANALYTICS_DB_PATH" in os.environ:
                del os.environ["POWER_ANALYTICS_DB_PATH"]

    def test_scheme_survives_restart(self):
        manager = SchemeManager()
        field_mappings = {
            "store_id": "store_id",
            "meter_id": "meter_id",
            "reading_value": "reading_value",
            "custom_field": "reading_time",
        }
        strategies = {
            CONFLICT_TYPES["DUPLICATE_BATCH_NAME"]: CONFLICT_STRATEGIES["OVERWRITE"],
            CONFLICT_TYPES["DUPLICATE_READING"]: CONFLICT_STRATEGIES["ISOLATE"],
            CONFLICT_TYPES["MISSING_DEVICE"]: CONFLICT_STRATEGIES["OVERWRITE"],
        }
        scheme = manager.create_scheme(
            name="重启持久化测试",
            field_mappings=field_mappings,
            default_timezone="America/New_York",
            device_config_path=str(TEST_DATA_DIR / "devices_config.csv"),
            conflict_strategies=strategies,
            description="测试重启后方案是否保留",
            created_by="测试员",
        )
        scheme_id = scheme.id
        manager.close()
        reset_engine_cache()

        manager2 = SchemeManager()
        loaded = manager2.get_scheme(scheme_id)
        assert loaded is not None
        assert loaded.name == "重启持久化测试"
        assert loaded.default_timezone == "America/New_York"
        assert loaded.description == "测试重启后方案是否保留"
        assert loaded.created_by == "测试员"

        config = manager2.get_scheme_config(loaded)
        assert config["field_mappings"]["custom_field"] == "reading_time"
        assert config["conflict_strategies"][CONFLICT_TYPES["DUPLICATE_BATCH_NAME"]] == "overwrite"
        assert config["conflict_strategies"][CONFLICT_TYPES["MISSING_DEVICE"]] == "overwrite"
        manager2.close()

    def test_import_with_scheme_after_restart(self):
        manager = SchemeManager()
        field_mappings = {
            "store_id": "store_id",
            "store_name": "store_name",
            "meter_id": "meter_id",
            "reading_date": "reading_date",
            "reading_time": "reading_time",
            "reading_value": "reading_value",
            "reading_unit": "reading_unit",
            "timezone": "timezone",
            "opening_time": "opening_time",
            "closing_time": "closing_time",
            "operator": "operator",
            "device_id": "device_id",
        }
        strategies = {
            CONFLICT_TYPES["DUPLICATE_READING"]: CONFLICT_STRATEGIES["ISOLATE"],
        }
        scheme = manager.create_scheme(
            name="重启导入测试",
            field_mappings=field_mappings,
            conflict_strategies=strategies,
        )
        scheme_id = scheme.id
        manager.close()
        reset_engine_cache()

        with get_db() as db:
            for store_id in ["S001", "S002", "S003"]:
                store = Store(store_id=store_id, store_name=f"门店{store_id}")
                db.add(store)
                db.flush()
                device_num = store_id.replace("S", "")
                device = Device(device_id=f"DEVS{device_num}", store_id=store.id, device_name=f"设备{store_id}")
                db.add(device)
            db.commit()

        service = DataImportService(scheme_id=scheme_id)
        batch, rows = service.import_file(
            str(TEST_DATA_DIR / "duplicate_reading_test.csv"),
            batch_name="重启后导入测试",
            imported_by="测试员",
        )
        assert batch.id is not None
        assert batch.valid_rows == 2
        service.close()

        scheme_manager = SchemeManager()
        logs = scheme_manager.get_audit_logs(batch_id=batch.id)
        assert len(logs) > 0
        assert any(log.conflict_strategy == "isolate" for log in logs)
        scheme_manager.close()


class TestCLISchemeCommands:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = Path(self.test_dir) / "test.db"
        os.environ["POWER_ANALYTICS_DB_PATH"] = str(self.db_path)
        self._run_cli(["init", "--reset-db"])
        yield
        del os.environ["POWER_ANALYTICS_DB_PATH"]
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _run_cli(self, args, cwd=None):
        cmd = [sys.executable, "-m", "power_analytics.cli"] + args
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd or Path(__file__).resolve().parent,
            env=env,
        )
        return result

    def test_cli_create_scheme(self):
        result = self._run_cli([
            "scheme", "create",
            "--name", "CLI测试方案",
            "--description", "CLI创建测试",
            "--default-timezone", "Asia/Shanghai",
            "--conflict-batch-name", "reject",
            "--conflict-reading", "isolate",
            "--conflict-missing-device", "overwrite",
            "-u", "CLI用户",
        ])
        assert result.returncode == 0
        assert ("方案创建成功" in result.stdout or "[OK]" in result.stdout)
        assert "ID: 1" in result.stdout

    def test_cli_list_schemes(self):
        self._run_cli(["scheme", "create", "--name", "列表测试1"])
        self._run_cli(["scheme", "create", "--name", "列表测试2"])

        result = self._run_cli(["scheme", "list"])
        assert result.returncode == 0
        assert "列表测试1" in result.stdout
        assert "列表测试2" in result.stdout

    def test_cli_show_scheme(self):
        self._run_cli(["scheme", "create", "--name", "详情测试方案"])

        result = self._run_cli(["scheme", "show", "1"])
        assert result.returncode == 0
        assert "详情测试方案" in result.stdout
        assert "字段映射" in result.stdout
        assert "冲突处理策略" in result.stdout

    def test_cli_export_import_scheme(self):
        self._run_cli(["scheme", "create", "--name", "导出导入测试"])

        export_path = Path(self.test_dir) / "cli_export.json"
        result = self._run_cli(["scheme", "export", "1", "-o", str(export_path)])
        assert result.returncode == 0
        assert export_path.exists()

        self._run_cli(["scheme", "delete", "1"])

        result = self._run_cli(["scheme", "import", str(export_path), "-u", "导入用户"])
        assert result.returncode == 0
        assert ("方案导入成功" in result.stdout or "[OK]" in result.stdout)

        result = self._run_cli(["scheme", "list", "--all"])
        assert result.returncode == 0
        assert result.stdout.count("导出导入测试") == 1

    def test_cli_delete_scheme(self):
        self._run_cli(["scheme", "create", "--name", "删除测试方案"])

        result = self._run_cli(["scheme", "delete", "1"])
        assert result.returncode == 0
        assert ("方案已删除" in result.stdout or "[OK]" in result.stdout)

        result = self._run_cli(["scheme", "list"])
        assert "删除测试方案" not in result.stdout

    def test_cli_import_with_scheme(self):
        self._run_cli(["scheme", "create", "--name", "导入使用测试"])

        result = self._run_cli([
            "import-file",
            str(TEST_DATA_DIR / "normal_readings.csv"),
            "-n", "方案导入测试",
            "--scheme", "1",
            "-u", "测试员",
        ])
        assert result.returncode == 0
        assert ("导入成功" in result.stdout or "[OK]" in result.stdout)
        assert "使用方案: ID=1" in result.stdout

    def test_cli_audit_logs(self):
        self._run_cli(["scheme", "create", "--name", "审计测试方案"])
        self._run_cli([
            "import-file",
            str(TEST_DATA_DIR / "normal_readings.csv"),
            "-n", "审计测试批次",
            "--scheme", "1",
        ])

        result = self._run_cli(["audit", "logs"])
        assert result.returncode == 0
        assert ("批次创建" in result.stdout or "batch_created" in result.stdout or "导入完成" in result.stdout)

    def test_cli_isolated_records(self):
        with get_db() as db:
            from power_analytics.models import Store, Device
            for store_id in ["S001", "S002", "S003"]:
                store = Store(store_id=store_id, store_name=f"门店{store_id}")
                db.add(store)
                db.flush()
                device_num = store_id.replace("S", "")
                device = Device(device_id=f"DEVS{device_num}", store_id=store.id, device_name=f"设备{store_id}")
                db.add(device)
            db.commit()

        self._run_cli([
            "scheme", "create",
            "--name", "隔离测试方案",
            "--conflict-reading", "isolate",
        ])
        self._run_cli([
            "import-file",
            str(TEST_DATA_DIR / "duplicate_reading_test.csv"),
            "-n", "隔离测试批次",
            "--scheme", "1",
        ])

        result = self._run_cli(["audit", "isolated"])
        assert result.returncode == 0
        assert ("重复读数" in result.stdout or "duplicate_reading" in result.stdout or "隔离" in result.stdout)
        assert "pending" in result.stdout or "待处理" in result.stdout

    def test_cli_failure_scenarios(self):
        result = self._run_cli(["scheme", "show", "999"])
        assert result.returncode != 0
        assert ("方案不存在" in result.stderr or "[ERROR]" in result.stderr)

        result = self._run_cli(["scheme", "delete", "999"])
        assert result.returncode != 0

        result = self._run_cli(["scheme", "import", "nonexistent.json"])
        assert result.returncode != 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
