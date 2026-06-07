import sys
import json
from pathlib import Path

import click

from .config import BATCH_STATUS, SAMPLE_DATA_DIR
from .database import init_db, get_db
from .validators import ValidationError
from .data_import import DataImportService
from .batch_manager import BatchManager
from .anomaly_detector import AnomalyDetector
from .correction_engine import RuleEngine
from .report_exporter import ReportExporter


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
        click.echo(f"✅ 系统初始化成功{'（数据库已重置）' if reset_db else ''}")
    except Exception as e:
        click.echo(f"❌ 初始化失败: {e}", err=True)
        sys.exit(1)


@cli.command("import-file")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("-n", "--name", "batch_name", help="批次名称")
@click.option("-d", "--description", help="批次描述")
@click.option("-u", "--user", "imported_by", help="导入人")
def import_file(file_path, batch_name, description, imported_by):
    """导入CSV/Excel数据文件"""
    try:
        service = DataImportService()
        batch, rows = service.import_file(
            file_path=file_path,
            batch_name=batch_name,
            description=description,
            imported_by=imported_by,
        )
        click.echo(f"✅ 导入成功！批次ID: {batch.id}")
        click.echo(f"   批次名称: {batch.name}")
        click.echo(f"   总行数: {batch.total_rows}")
        click.echo(f"   有效行数: {batch.valid_rows}")
        click.echo(f"   无效行数: {batch.invalid_rows}")
        click.echo(f"   状态: {batch.status}")
    except ValidationError as e:
        click.echo(f"❌ 导入失败: {e}", err=True)
        if e.details:
            import datetime
            def json_default(obj):
                if isinstance(obj, (datetime.date, datetime.datetime, datetime.time)):
                    return str(obj)
                return str(obj)
            click.echo(f"   错误详情: {json.dumps(e.details, ensure_ascii=False, indent=2, default=json_default)}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ 导入失败: {e}", err=True)
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
            click.echo("暂无批次数据")
            return

        click.echo(f"{'ID':<5} {'名称':<20} {'文件':<25} {'行数':<8} {'状态':<12} {'创建时间':<20}")
        click.echo("-" * 90)
        for b in batches:
            status_display = {
                BATCH_STATUS["IMPORTED"]: "已导入",
                BATCH_STATUS["VALIDATED"]: "已校验",
                BATCH_STATUS["ANALYZED"]: "已分析",
                BATCH_STATUS["COMMITTED"]: "已提交",
                BATCH_STATUS["ROLLED_BACK"]: "已回滚",
            }.get(b.status, b.status)

            click.echo(f"{b.id:<5} {b.name[:18]:<20} {b.file_name[:23]:<25} {b.valid_rows:<8} {status_display:<12} {b.created_at.strftime('%Y-%m-%d %H:%M'):<20}")
    except Exception as e:
        click.echo(f"❌ 查询失败: {e}", err=True)
        sys.exit(1)


@cli.command("show")
@click.argument("batch_id", type=int)
def show_batch(batch_id):
    """显示批次详情"""
    try:
        manager = BatchManager()
        details = manager.get_batch_details(batch_id)

        if not details:
            click.echo(f"❌ 批次不存在: {batch_id}", err=True)
            sys.exit(1)

        batch = details["batch"]
        click.echo(f"\n📋 批次详情 #{batch.id}")
        click.echo("=" * 50)
        click.echo(f"名称: {batch.name}")
        if batch.description:
            click.echo(f"描述: {batch.description}")
        click.echo(f"文件: {batch.file_name}")
        click.echo(f"有效读数: {details['readings_count']} 条")
        click.echo(f"异常数量: {details['anomalies_count']} 个")
        click.echo(f"修正记录: {details['corrections_count']} 条")
        click.echo(f"状态: {batch.status}")
        click.echo(f"创建时间: {batch.created_at}")

        if details["anomaly_summary"]:
            click.echo("\n📊 异常汇总:")
            for code, info in details["anomaly_summary"].items():
                click.echo(f"  {code}: {info['count']} 个 ({info['severity']})")

        if details["anomalies"]:
            click.echo("\n⚠️  异常详情:")
            for a in details["anomalies"]:
                severity_icon = {"error": "🔴", "warning": "🟡", "info": "🔵"}.get(a.severity, "⚪")
                click.echo(f"  {severity_icon} #{a.id} [{a.anomaly_code}] {a.description[:80]}...")
                if a.details:
                    try:
                        d = json.loads(a.details)
                        if "drop_amount" in d:
                            click.echo(f"     下降: {d['drop_amount']:.2f} kWh")
                    except:
                        pass

        if details["corrections"]:
            click.echo("\n✏️  修正记录:")
            for c in details["corrections"]:
                click.echo(f"  #{c.id}: {c.old_value} → {c.new_value}")
                if c.note:
                    click.echo(f"     备注: {c.note}")

    except Exception as e:
        click.echo(f"❌ 查询失败: {e}", err=True)
        sys.exit(1)


@cli.command("analyze")
@click.argument("batch_id", type=int)
def analyze_batch(batch_id):
    """分析批次异常"""
    try:
        detector = AnomalyDetector()
        count, anomalies = detector.analyze_batch(batch_id)

        click.echo(f"✅ 分析完成！发现 {count} 个异常")

        if anomalies:
            by_type = {}
            for a in anomalies:
                code = a.anomaly_code
                if code not in by_type:
                    by_type[code] = 0
                by_type[code] += 1

            click.echo("\n按类型统计:")
            for code, cnt in by_type.items():
                click.echo(f"  {code}: {cnt} 个")
    except ValueError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ 分析失败: {e}", err=True)
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
            click.echo(f"❌ 批次不存在: {batch_id}", err=True)
            sys.exit(1)
        click.echo(f"✅ 批次 #{batch_id} 已提交")
    except ValueError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ 提交失败: {e}", err=True)
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
            click.echo(f"❌ 批次不存在: {batch_id}", err=True)
            sys.exit(1)
        click.echo(f"✅ 批次 #{batch_id} 已回滚")
        if reason:
            click.echo(f"   原因: {reason}")
    except Exception as e:
        click.echo(f"❌ 回滚失败: {e}", err=True)
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
        click.echo(f"✅ 规则创建成功！ID: {rule.id}")
        click.echo(f"   {code} v{version} - {name}")
    except ValueError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ 创建失败: {e}", err=True)
        sys.exit(1)


@rule.command("list")
def list_rules():
    """列出所有规则"""
    try:
        engine = RuleEngine()
        rules = engine.list_rules(active_only=False)

        if not rules:
            click.echo("暂无规则")
            return

        click.echo(f"{'ID':<5} {'代码':<20} {'版本':<10} {'名称':<20} {'状态':<8}")
        click.echo("-" * 65)
        for r in rules:
            status = "激活" if r.is_active else "停用"
            click.echo(f"{r.id:<5} {r.code:<20} {r.version:<10} {r.name[:18]:<20} {status:<8}")
    except Exception as e:
        click.echo(f"❌ 查询失败: {e}", err=True)
        sys.exit(1)


@rule.command("apply")
@click.argument("rule_id", type=int)
@click.option("--batch-id", type=int, help="指定批次ID，不指定则应用于所有批次")
def apply_rule(rule_id, batch_id):
    """应用修正规则"""
    try:
        engine = RuleEngine()
        count, corrections = engine.apply_rule(rule_id, batch_id=batch_id)
        click.echo(f"✅ 规则应用完成！共修正 {count} 条记录")
        for c in corrections[:5]:
            click.echo(f"   #{c.reading_id}: {c.old_value} → {c.new_value}")
        if len(corrections) > 5:
            click.echo(f"   ... 还有 {len(corrections) - 5} 条")
    except ValueError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ 应用失败: {e}", err=True)
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
        click.echo(f"✅ 修正成功！")
        click.echo(f"   读数ID: {reading_id}")
        click.echo(f"   {correction.old_value} → {correction.new_value}")
        if note:
            click.echo(f"   备注: {note}")
    except ValueError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ 修正失败: {e}", err=True)
        sys.exit(1)


@cli.command("rollback-correction")
@click.argument("correction_id", type=int)
@click.option("-u", "--user", "rolled_back_by", help="操作人")
def rollback_correction(correction_id, rolled_back_by):
    """回滚单条修正"""
    try:
        engine = RuleEngine()
        correction = engine.rollback_correction(correction_id, rolled_back_by=rolled_back_by)
        click.echo(f"✅ 修正已回滚！")
        click.echo(f"   修正ID: {correction_id}")
        click.echo(f"   恢复值: {correction.old_value}")
    except ValueError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ 回滚失败: {e}", err=True)
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
        click.echo(f"✅ HTML报告导出成功！")
        click.echo(f"   文件: {file_path}")
    except ValueError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ 导出失败: {e}", err=True)
        sys.exit(1)


@export.command("csv")
@click.argument("batch_id", type=int)
@click.option("-u", "--user", "exported_by", help="导出人")
def export_csv(batch_id, exported_by):
    """导出CSV读数报告"""
    try:
        exporter = ReportExporter()
        file_path = exporter.export_csv(batch_id, exported_by=exported_by)
        click.echo(f"✅ CSV报告导出成功！")
        click.echo(f"   文件: {file_path}")
    except ValueError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ 导出失败: {e}", err=True)
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
        click.echo(f"✅ 异常报告导出成功！")
        click.echo(f"   文件: {file_path}")
    except Exception as e:
        click.echo(f"❌ 导出失败: {e}", err=True)
        sys.exit(1)


@export.command("history")
@click.option("-l", "--limit", type=int, default=50, help="显示数量")
def export_history(limit):
    """查看导出历史"""
    try:
        exporter = ReportExporter()
        exports = exporter.get_export_history(limit=limit)

        if not exports:
            click.echo("暂无导出记录")
            return

        click.echo(f"{'ID':<5} {'类型':<15} {'文件名':<30} {'记录数':<8} {'异常数':<8} {'时间':<20}")
        click.echo("-" * 90)
        for e in exports:
            click.echo(f"{e.id:<5} {e.export_type:<15} {e.file_name[:28]:<30} {e.record_count:<8} {e.anomaly_count:<8} {e.created_at.strftime('%Y-%m-%d %H:%M'):<20}")
    except Exception as e:
        click.echo(f"❌ 查询失败: {e}", err=True)
        sys.exit(1)


@cli.command("generate-samples")
def generate_samples():
    """生成样例数据"""
    try:
        from .sample_data_generator import SampleDataGenerator
        generator = SampleDataGenerator()
        files = generator.generate_all()
        click.echo("✅ 样例数据生成完成！")
        for f in files:
            click.echo(f"   {f}")
    except Exception as e:
        click.echo(f"❌ 生成失败: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli()
