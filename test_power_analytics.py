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

from power_analytics.config import DB_PATH
from power_analytics.database import init_db, create_db_session, get_db, reset_engine_cache
from power_analytics.models import Batch, CorrectionRule, Correction, ExportSummary, Device, Store
from power_analytics.data_import import DataImportService
from power_analytics.batch_manager import BatchManager
from power_analytics.anomaly_detector import AnomalyDetector
from power_analytics.correction_engine import RuleEngine
from power_analytics.report_exporter import ReportExporter
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


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
