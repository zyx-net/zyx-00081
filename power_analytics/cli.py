import sys
import json
from pathlib import Path

import click

from .config import BATCH_STATUS, SAMPLE_DATA_DIR, CONFLICT_STRATEGIES, CONFLICT_TYPES
from .database import init_db, get_db
from .validators import ValidationError
from .data_import import DataImportService
from .batch_manager import BatchManager
from .anomaly_detector import AnomalyDetector
from .correction_engine import RuleEngine
from .report_exporter import ReportExporter
from .scheme_manager import SchemeManager
from .output_utils import init_output, safe_echo

init_output()


@click.group()
@click.version_option()
def cli():
    """连锁门店用电数据分析系统"""
    pass


@cli.command("init")
@click.option("--reset-db", is_flag=True, help="重置数据库")
def init(reset_db):
    """初始化系统"""
    try:
        init_db(reset=reset_db)
        safe_echo(f"✅ 系统初始化成功{'（数据库已重置）' if reset_db else ''}")
    except Exception as e:
        safe_echo(f"❌ 初始化失败: {e}", err=True)
        sys.exit(1)


@cli.command("import-file")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("-n", "--name", "batch_name", help="批次名称")
@click.option("-d", "--description", help="批次描述")
@click.option("-u", "--user", "imported_by", help="导入人")
@click.option("--scheme", "scheme_id", type=int, help="使用的导入方案ID")
@click.option("--scheme-name", "scheme_name", help="使用的导入方案名称")
def import_file(file_path, batch_name, description, imported_by, scheme_id, scheme_name):
    """导入CSV/Excel数据文件"""
    try:
        actual_scheme_id = scheme_id
        if scheme_name and not scheme_id:
            sm = SchemeManager()
            scheme = sm.get_scheme_by_name(scheme_name)
            if scheme:
                actual_scheme_id = scheme.id
            else:
                safe_echo(f"❌ 方案不存在: {scheme_name}", err=True)
                sys.exit(1)
            sm.close()

        service = DataImportService(scheme_id=actual_scheme_id)
        batch, rows = service.import_file(
            file_path=file_path,
            batch_name=batch_name,
            description=description,
            imported_by=imported_by,
        )
        safe_echo(f"✅ 导入成功！批次ID: {batch.id}")
        safe_echo(f"   批次名称: {batch.name}")
        safe_echo(f"   总行数: {batch.total_rows}")
        safe_echo(f"   有效行数: {batch.valid_rows}")
        safe_echo(f"   无效行数: {batch.invalid_rows}")
        safe_echo(f"   状态: {batch.status}")
        if actual_scheme_id:
            safe_echo(f"   使用方案: ID={actual_scheme_id}")
        service.close()
    except ValidationError as e:
        safe_echo(f"❌ 导入失败: {e}", err=True)
        if e.details:
            import datetime
            def json_default(obj):
                if isinstance(obj, (datetime.date, datetime.datetime, datetime.time)):
                    return str(obj)
                return str(obj)
            safe_echo(f"   错误详情: {json.dumps(e.details, ensure_ascii=False, indent=2, default=json_default)}", err=True)
        sys.exit(1)
    except Exception as e:
        safe_echo(f"❌ 导入失败: {e}", err=True)
        sys.exit(1)


@cli.command("list")
@click.option("-s", "--status", help="按状态过滤")
@click.option("-l", "--limit", type=int, default=100, help="显示数量")
def list_batches(status, limit):
    """列出所有批次"""
    try:
        manager = BatchManager()
        batches = manager.list_batches(status=status, limit=limit)

        if not batches:
            safe_echo("暂无批次数据")
            return

        safe_echo(f"{'ID':<5} {'名称':<20} {'文件':<25} {'行数':<8} {'状态':<12} {'创建时间':<20}")
        safe_echo("-" * 90)
        for b in batches:
            status_display = {
                BATCH_STATUS["IMPORTED"]: "已导入",
                BATCH_STATUS["VALIDATED"]: "已校验",
                BATCH_STATUS["ANALYZED"]: "已分析",
                BATCH_STATUS["COMMITTED"]: "已提交",
                BATCH_STATUS["ROLLED_BACK"]: "已回滚",
            }.get(b.status, b.status)

            safe_echo(f"{b.id:<5} {b.name[:18]:<20} {b.file_name[:23]:<25} {b.valid_rows:<8} {status_display:<12} {b.created_at.strftime('%Y-%m-%d %H:%M'):<20}")
    except Exception as e:
        safe_echo(f"❌ 查询失败: {e}", err=True)
        sys.exit(1)


@cli.command("show")
@click.argument("batch_id", type=int)
def show_batch(batch_id):
    """显示批次详情"""
    try:
        manager = BatchManager()
        details = manager.get_batch_details(batch_id)

        if not details:
            safe_echo(f"❌ 批次不存在: {batch_id}", err=True)
            sys.exit(1)

        batch = details["batch"]
        safe_echo(f"\n📋 批次详情 #{batch.id}")
        safe_echo("=" * 50)
        safe_echo(f"名称: {batch.name}")
        if batch.description:
            safe_echo(f"描述: {batch.description}")
        safe_echo(f"文件: {batch.file_name}")
        safe_echo(f"有效读数: {details['readings_count']} 条")
        safe_echo(f"异常数量: {details['anomalies_count']} 个")
        safe_echo(f"修正记录: {details['corrections_count']} 条")
        safe_echo(f"状态: {batch.status}")
        safe_echo(f"创建时间: {batch.created_at}")

        if details["anomaly_summary"]:
            safe_echo("\n📊 异常汇总:")
            for code, info in details["anomaly_summary"].items():
                safe_echo(f"  {code}: {info['count']} 个 ({info['severity']})")

        if details["anomalies"]:
            safe_echo("\n⚠️  异常详情:")
            for a in details["anomalies"]:
                severity_icon = {"error": "🔴", "warning": "🟡", "info": "🔵"}.get(a.severity, "⚪")
                safe_echo(f"  {severity_icon} #{a.id} [{a.anomaly_code}] {a.description[:80]}...")
                if a.details:
                    try:
                        d = json.loads(a.details)
                        if "drop_amount" in d:
                            safe_echo(f"     下降: {d['drop_amount']:.2f} kWh")
                    except:
                        pass

        if details["corrections"]:
            safe_echo("\n✏️  修正记录:")
            for c in details["corrections"]:
                safe_echo(f"  #{c.id}: {c.old_value} → {c.new_value}")
                if c.note:
                    safe_echo(f"     备注: {c.note}")

    except Exception as e:
        safe_echo(f"❌ 查询失败: {e}", err=True)
        sys.exit(1)


@cli.command("analyze")
@click.argument("batch_id", type=int)
def analyze_batch(batch_id):
    """分析批次异常"""
    try:
        detector = AnomalyDetector()
        count, anomalies = detector.analyze_batch(batch_id)

        safe_echo(f"✅ 分析完成！发现 {count} 个异常")

        if anomalies:
            by_type = {}
            for a in anomalies:
                code = a.anomaly_code
                if code not in by_type:
                    by_type[code] = 0
                by_type[code] += 1

            safe_echo("\n按类型统计:")
            for code, cnt in by_type.items():
                safe_echo(f"  {code}: {cnt} 个")
    except ValueError as e:
        safe_echo(f"❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        safe_echo(f"❌ 分析失败: {e}", err=True)
        sys.exit(1)


@cli.command("commit")
@click.argument("batch_id", type=int)
@click.option("-u", "--user", "committed_by", help="提交人")
def commit_batch(batch_id, committed_by):
    """提交批次"""
    try:
        manager = BatchManager()
        batch = manager.commit_batch(batch_id, committed_by=committed_by)
        if not batch:
            safe_echo(f"❌ 批次不存在: {batch_id}", err=True)
            sys.exit(1)
        safe_echo(f"✅ 批次 #{batch_id} 已提交")
    except ValueError as e:
        safe_echo(f"❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        safe_echo(f"❌ 提交失败: {e}", err=True)
        sys.exit(1)


@cli.command("rollback")
@click.argument("batch_id", type=int)
@click.option("-r", "--reason", help="回滚原因")
@click.option("-u", "--user", "rolled_back_by", help="操作人")
def rollback_batch(batch_id, reason, rolled_back_by):
    """回滚批次"""
    try:
        manager = BatchManager()
        batch = manager.rollback_batch(batch_id, reason=reason, rolled_back_by=rolled_back_by)
        if not batch:
            safe_echo(f"❌ 批次不存在: {batch_id}", err=True)
            sys.exit(1)
        safe_echo(f"✅ 批次 #{batch_id} 已回滚")
        if reason:
            safe_echo(f"   原因: {reason}")
    except Exception as e:
        safe_echo(f"❌ 回滚失败: {e}", err=True)
        sys.exit(1)


@cli.group()
def rule():
    """修正规则管理"""
    pass


@rule.command("create")
@click.option("--code", required=True, help="规则代码")
@click.option("--name", required=True, help="规则名称")
@click.option("--version", required=True, help="规则版本")
@click.option("--condition", required=True, help="条件表达式")
@click.option("--action", required=True, help="动作表达式")
@click.option("--description", help="规则描述")
@click.option("-u", "--user", "created_by", help="创建人")
def create_rule(code, name, version, condition, action, description, created_by):
    """创建修正规则"""
    try:
        engine = RuleEngine()
        rule = engine.create_rule(
            code=code,
            name=name,
            version=version,
            condition=condition,
            action=action,
            description=description,
            created_by=created_by,
        )
        safe_echo(f"✅ 规则创建成功！ID: {rule.id}")
        safe_echo(f"   {code} v{version} - {name}")
    except ValueError as e:
        safe_echo(f"❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        safe_echo(f"❌ 创建失败: {e}", err=True)
        sys.exit(1)


@rule.command("list")
def list_rules():
    """列出所有规则"""
    try:
        engine = RuleEngine()
        rules = engine.list_rules(active_only=False)

        if not rules:
            safe_echo("暂无规则")
            return

        safe_echo(f"{'ID':<5} {'代码':<20} {'版本':<10} {'名称':<20} {'状态':<8}")
        safe_echo("-" * 65)
        for r in rules:
            status = "激活" if r.is_active else "停用"
            safe_echo(f"{r.id:<5} {r.code:<20} {r.version:<10} {r.name[:18]:<20} {status:<8}")
    except Exception as e:
        safe_echo(f"❌ 查询失败: {e}", err=True)
        sys.exit(1)


@rule.command("apply")
@click.argument("rule_id", type=int)
@click.option("--batch-id", type=int, help="指定批次ID，不指定则应用于所有批次")
def apply_rule(rule_id, batch_id):
    """应用修正规则"""
    try:
        engine = RuleEngine()
        count, corrections = engine.apply_rule(rule_id, batch_id=batch_id)
        safe_echo(f"✅ 规则应用完成！共修正 {count} 条记录")
        for c in corrections[:5]:
            safe_echo(f"   #{c.reading_id}: {c.old_value} → {c.new_value}")
        if len(corrections) > 5:
            safe_echo(f"   ... 还有 {len(corrections) - 5} 条")
    except ValueError as e:
        safe_echo(f"❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        safe_echo(f"❌ 应用失败: {e}", err=True)
        sys.exit(1)


@cli.command("correct")
@click.argument("reading_id", type=int)
@click.argument("new_value", type=float)
@click.option("--note", help="修正备注")
@click.option("-u", "--user", "applied_by", help="操作人")
def correct_reading(reading_id, new_value, note, applied_by):
    """人工修正读数"""
    try:
        engine = RuleEngine()
        correction = engine.manual_correct(
            reading_id=reading_id,
            new_value=new_value,
            note=note,
            applied_by=applied_by,
        )
        safe_echo(f"✅ 修正成功！")
        safe_echo(f"   读数ID: {reading_id}")
        safe_echo(f"   {correction.old_value} → {correction.new_value}")
        if note:
            safe_echo(f"   备注: {note}")
    except ValueError as e:
        safe_echo(f"❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        safe_echo(f"❌ 修正失败: {e}", err=True)
        sys.exit(1)


@cli.command("rollback-correction")
@click.argument("correction_id", type=int)
@click.option("-u", "--user", "rolled_back_by", help="操作人")
def rollback_correction(correction_id, rolled_back_by):
    """回滚单条修正"""
    try:
        engine = RuleEngine()
        correction = engine.rollback_correction(correction_id, rolled_back_by=rolled_back_by)
        safe_echo(f"✅ 修正已回滚！")
        safe_echo(f"   修正ID: {correction_id}")
        safe_echo(f"   恢复值: {correction.old_value}")
    except ValueError as e:
        safe_echo(f"❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        safe_echo(f"❌ 回滚失败: {e}", err=True)
        sys.exit(1)


@cli.group()
def export():
    """报告导出"""
    pass


@export.command("html")
@click.argument("batch_id", type=int)
@click.option("-u", "--user", "exported_by", help="导出人")
def export_html(batch_id, exported_by):
    """导出HTML报告"""
    try:
        exporter = ReportExporter()
        file_path = exporter.export_html(batch_id, exported_by=exported_by)
        safe_echo(f"✅ HTML报告导出成功！")
        safe_echo(f"   文件: {file_path}")
    except ValueError as e:
        safe_echo(f"❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        safe_echo(f"❌ 导出失败: {e}", err=True)
        sys.exit(1)


@export.command("csv")
@click.argument("batch_id", type=int)
@click.option("-u", "--user", "exported_by", help="导出人")
def export_csv(batch_id, exported_by):
    """导出CSV读数报告"""
    try:
        exporter = ReportExporter()
        file_path = exporter.export_csv(batch_id, exported_by=exported_by)
        safe_echo(f"✅ CSV报告导出成功！")
        safe_echo(f"   文件: {file_path}")
    except ValueError as e:
        safe_echo(f"❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        safe_echo(f"❌ 导出失败: {e}", err=True)
        sys.exit(1)


@export.command("anomalies")
@click.option("--batch-id", type=int, help="指定批次ID")
@click.option("--type", "anomaly_type", help="异常类型")
@click.option("--unresolved", is_flag=True, help="只导出未解决的")
@click.option("-u", "--user", "exported_by", help="导出人")
def export_anomalies(batch_id, anomaly_type, unresolved, exported_by):
    """导出异常CSV报告"""
    try:
        exporter = ReportExporter()
        file_path = exporter.export_anomalies_csv(
            batch_id=batch_id,
            anomaly_type=anomaly_type,
            unresolved=unresolved,
            exported_by=exported_by,
        )
        safe_echo(f"✅ 异常报告导出成功！")
        safe_echo(f"   文件: {file_path}")
    except Exception as e:
        safe_echo(f"❌ 导出失败: {e}", err=True)
        sys.exit(1)


@export.command("history")
@click.option("-l", "--limit", type=int, default=50, help="显示数量")
def export_history(limit):
    """查看导出历史"""
    try:
        exporter = ReportExporter()
        exports = exporter.get_export_history(limit=limit)

        if not exports:
            safe_echo("暂无导出记录")
            return

        safe_echo(f"{'ID':<5} {'类型':<15} {'文件名':<30} {'记录数':<8} {'异常数':<8} {'时间':<20}")
        safe_echo("-" * 90)
        for e in exports:
            safe_echo(f"{e.id:<5} {e.export_type:<15} {e.file_name[:28]:<30} {e.record_count:<8} {e.anomaly_count:<8} {e.created_at.strftime('%Y-%m-%d %H:%M'):<20}")
    except Exception as e:
        safe_echo(f"❌ 查询失败: {e}", err=True)
        sys.exit(1)


@cli.command("generate-samples")
def generate_samples():
    """生成样例数据"""
    try:
        from .sample_data_generator import SampleDataGenerator
        generator = SampleDataGenerator()
        files = generator.generate_all()
        safe_echo("✅ 样例数据生成完成！")
        for f in files:
            safe_echo(f"   {f}")
    except Exception as e:
        safe_echo(f"❌ 生成失败: {e}", err=True)
        sys.exit(1)


@cli.group()
def scheme():
    """导入方案管理"""
    pass


@scheme.command("create")
@click.option("--name", required=True, help="方案名称")
@click.option("--description", help="方案描述")
@click.option("--field-mapping", "field_mappings", multiple=True, nargs=2, help="字段映射，格式: 源字段 目标字段")
@click.option("--default-timezone", default="Asia/Shanghai", help="默认时区")
@click.option("--device-config", "device_config_path", type=click.Path(exists=True), help="设备配置文件路径")
@click.option("--conflict-batch-name", type=click.Choice(["reject", "isolate", "overwrite"]), default="reject", help="同批次重名处理策略")
@click.option("--conflict-reading", type=click.Choice(["reject", "isolate", "overwrite"]), default="reject", help="重复读数处理策略")
@click.option("--conflict-missing-device", type=click.Choice(["reject", "isolate", "overwrite"]), default="reject", help="缺失设备处理策略")
@click.option("-u", "--user", "created_by", help="创建人")
def create_scheme(name, description, field_mappings, default_timezone, device_config_path,
                  conflict_batch_name, conflict_reading, conflict_missing_device, created_by):
    """创建导入方案"""
    try:
        fm = {}
        for source, target in field_mappings:
            fm[source] = target
        if not fm:
            from .config import REQUIRED_FIELDS, OPTIONAL_FIELDS
            for f in REQUIRED_FIELDS + OPTIONAL_FIELDS:
                fm[f] = f

        strategies = {
            CONFLICT_TYPES["DUPLICATE_BATCH_NAME"]: conflict_batch_name,
            CONFLICT_TYPES["DUPLICATE_READING"]: conflict_reading,
            CONFLICT_TYPES["MISSING_DEVICE"]: conflict_missing_device,
        }

        manager = SchemeManager()
        scheme = manager.create_scheme(
            name=name,
            field_mappings=fm,
            default_timezone=default_timezone,
            device_config_path=device_config_path,
            conflict_strategies=strategies,
            description=description,
            created_by=created_by,
        )
        scheme_id = scheme.id
        scheme_name = scheme.name
        scheme_description = scheme.description
        manager.close()
        safe_echo(f"✅ 方案创建成功！ID: {scheme_id}")
        safe_echo(f"   名称: {scheme_name}")
        if scheme_description:
            safe_echo(f"   描述: {scheme_description}")
    except ValueError as e:
        safe_echo(f"❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        safe_echo(f"❌ 创建失败: {e}", err=True)
        sys.exit(1)


@scheme.command("list")
@click.option("--all", "show_all", is_flag=True, help="显示所有方案（包括已停用的）")
def list_schemes(show_all):
    """列出所有导入方案"""
    try:
        manager = SchemeManager()
        schemes = manager.list_schemes(active_only=not show_all)
        manager.close()

        if not schemes:
            safe_echo("暂无导入方案")
            return

        safe_echo(f"{'ID':<5} {'名称':<25} {'时区':<15} {'状态':<8} {'创建时间':<20}")
        safe_echo("-" * 75)
        for s in schemes:
            status = "激活" if s.is_active else "停用"
            safe_echo(f"{s.id:<5} {s.name[:23]:<25} {s.default_timezone:<15} {status:<8} {s.created_at.strftime('%Y-%m-%d %H:%M'):<20}")
    except Exception as e:
        safe_echo(f"❌ 查询失败: {e}", err=True)
        sys.exit(1)


@scheme.command("show")
@click.argument("scheme_id", type=int)
def show_scheme(scheme_id):
    """显示方案详情"""
    try:
        manager = SchemeManager()
        scheme = manager.get_scheme(scheme_id)
        if not scheme:
            safe_echo(f"❌ 方案不存在: {scheme_id}", err=True)
            sys.exit(1)

        config = manager.get_scheme_config(scheme)
        manager.close()

        safe_echo(f"\n📋 导入方案 #{scheme.id}")
        safe_echo("=" * 50)
        safe_echo(f"名称: {scheme.name}")
        if scheme.description:
            safe_echo(f"描述: {scheme.description}")
        safe_echo(f"默认时区: {scheme.default_timezone}")
        if scheme.device_config_path:
            safe_echo(f"设备配置: {scheme.device_config_path}")
        safe_echo(f"状态: {'激活' if scheme.is_active else '停用'}")
        safe_echo(f"创建时间: {scheme.created_at}")
        if scheme.created_by:
            safe_echo(f"创建人: {scheme.created_by}")

        safe_echo(f"\n🔗 字段映射:")
        for source, target in config["field_mappings"].items():
            if source == target:
                safe_echo(f"  {source} → {target}")
            else:
                safe_echo(f"  {source} → {target} (重命名)")

        safe_echo(f"\n⚔️  冲突处理策略:")
        for conflict_type, strategy in config["conflict_strategies"].items():
            strategy_display = {
                "reject": "拒绝",
                "isolate": "隔离",
                "overwrite": "覆盖",
            }.get(strategy, strategy)
            type_display = {
                "duplicate_batch_name": "同批次重名",
                "duplicate_reading": "重复读数",
                "missing_device": "缺失设备",
            }.get(conflict_type, conflict_type)
            safe_echo(f"  {type_display}: {strategy_display}")
    except Exception as e:
        safe_echo(f"❌ 查询失败: {e}", err=True)
        sys.exit(1)


@scheme.command("export")
@click.argument("scheme_id", type=int)
@click.option("-o", "--output", type=click.Path(), help="输出文件路径")
def export_scheme(scheme_id, output):
    """导出方案为JSON"""
    try:
        manager = SchemeManager()
        file_path = manager.export_scheme_to_json(scheme_id, output_path=output)
        manager.close()
        safe_echo(f"✅ 方案导出成功！")
        safe_echo(f"   文件: {file_path}")
    except ValueError as e:
        safe_echo(f"❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        safe_echo(f"❌ 导出失败: {e}", err=True)
        sys.exit(1)


@scheme.command("import")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("-u", "--user", "created_by", help="导入人")
def import_scheme(file_path, created_by):
    """从JSON导入方案"""
    try:
        manager = SchemeManager()
        scheme = manager.import_scheme_from_json(file_path, created_by=created_by)
        scheme_id = scheme.id
        scheme_name = scheme.name
        manager.close()
        safe_echo(f"✅ 方案导入成功！ID: {scheme_id}")
        safe_echo(f"   名称: {scheme_name}")
    except ValueError as e:
        safe_echo(f"❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        safe_echo(f"❌ 导入失败: {e}", err=True)
        sys.exit(1)


@scheme.command("delete")
@click.argument("scheme_id", type=int)
def delete_scheme(scheme_id):
    """删除导入方案"""
    try:
        manager = SchemeManager()
        success = manager.delete_scheme(scheme_id)
        manager.close()
        if success:
            safe_echo(f"✅ 方案已删除: {scheme_id}")
        else:
            safe_echo(f"❌ 方案不存在: {scheme_id}", err=True)
            sys.exit(1)
    except Exception as e:
        safe_echo(f"❌ 删除失败: {e}", err=True)
        sys.exit(1)


@cli.group()
def audit():
    """审计日志管理"""
    pass


@audit.command("logs")
@click.option("--batch-id", type=int, help="按批次ID过滤")
@click.option("--scheme-id", type=int, help="按方案ID过滤")
@click.option("-l", "--limit", type=int, default=50, help="显示数量")
def audit_logs(batch_id, scheme_id, limit):
    """查看导入审计日志"""
    try:
        manager = SchemeManager()
        logs = manager.get_audit_logs(batch_id=batch_id, scheme_id=scheme_id, limit=limit)
        manager.close()

        if not logs:
            safe_echo("暂无审计日志")
            return

        safe_echo(f"{'ID':<6} {'动作':<18} {'批次':<8} {'冲突类型':<16} {'策略':<10} {'时间':<20}")
        safe_echo("-" * 80)
        for log in logs:
            action_display = {
                "batch_created": "批次创建",
                "batch_overwritten": "批次覆盖",
                "conflict_detected": "冲突检测",
                "import_completed": "导入完成",
            }.get(log.action, log.action)
            conflict_display = {
                "duplicate_batch_name": "重名",
                "duplicate_reading": "重复读数",
                "missing_device": "缺失设备",
            }.get(log.conflict_type, log.conflict_type or "-")
            strategy_display = {
                "reject": "拒绝",
                "isolate": "隔离",
                "overwrite": "覆盖",
            }.get(log.conflict_strategy, log.conflict_strategy or "-")
            batch_str = str(log.batch_id) if log.batch_id else "-"
            safe_echo(f"{log.id:<6} {action_display:<18} {batch_str:<8} {conflict_display:<16} {strategy_display:<10} {log.created_at.strftime('%Y-%m-%d %H:%M'):<20}")
    except Exception as e:
        safe_echo(f"❌ 查询失败: {e}", err=True)
        sys.exit(1)


@audit.command("isolated")
@click.option("--batch-id", type=int, help="按批次ID过滤")
@click.option("--pending", is_flag=True, help="只显示待处理的")
def audit_isolated(batch_id, pending):
    """查看隔离记录"""
    try:
        manager = SchemeManager()
        resolution = "pending" if pending else None
        records = manager.get_isolated_records(batch_id=batch_id, resolution=resolution)
        manager.close()

        if not records:
            safe_echo("暂无隔离记录")
            return

        safe_echo(f"{'ID':<6} {'批次':<8} {'行号':<8} {'冲突类型':<16} {'状态':<10} {'时间':<20}")
        safe_echo("-" * 70)
        for r in records:
            type_display = {
                "duplicate_batch_name": "重名",
                "duplicate_reading": "重复读数",
                "missing_device": "缺失设备",
            }.get(r.conflict_type, r.conflict_type)
            safe_echo(f"{r.id:<6} {r.batch_id:<8} {r.row_number:<8} {type_display:<16} {r.resolution:<10} {r.created_at.strftime('%Y-%m-%d %H:%M'):<20}")
    except Exception as e:
        safe_echo(f"❌ 查询失败: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli()
