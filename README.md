# 连锁门店用电数据分析系统

本地数据分析工具，用于核对连锁门店的电表读数、营业时段和设备配置，识别尖峰用电、闭店后耗电、读数倒退、缺表、重复上报等异常。

## 功能特性

- 📊 CSV/Excel 数据导入和字段映射
- 🔍 5种异常类型自动识别（尖峰用电、闭店后耗电、读数倒退、缺表、重复上报）
- 📝 批次生命周期管理（导入→校验→分析→提交→回滚）
- ✏️ 人工修正规则引擎（条件表达式、自动应用、手动修正、回滚）
- 📈 HTML/CSV/异常报告导出
- 💾 SQLite 本地持久化，重启数据一致
- 🖥️ 跨平台安全输出（Windows GBK 编码兼容）
- 📋 **可复用导入方案管理**：字段映射、时区默认值、设备配置、冲突策略一键保存为 JSON
- ⚔️ **智能冲突处理**：同批次重名、重复读数、缺失设备支持拒绝/隔离/覆盖三种策略
- 📜 **审计日志**：所有冲突处理和导入操作全程记录，可追溯可审计
- 🚧 **隔离机制**：异常数据可隔离暂存，不影响正常数据导入
- 🔄 **方案导入导出**：方案可 JSON 格式导出，跨环境复用
- 🛡️ **回滚保护**：批次回滚、修正回滚不受新功能影响，审计日志永久保留

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 初始化系统

```bash
python -m power_analytics.cli init --reset-db
```

### 3. 生成样例数据

```bash
python -m power_analytics.cli generate-samples
```

### 4. Windows 控制台注意事项

**Windows CMD/PowerShell 默认使用 GBK 编码**，本系统已自动处理：
- 所有输出会自动替换表情符号为纯文本（如 `[OK]` 替代 `✅`）
- 如果希望看到完整的 Unicode 表情，执行：`chcp 65001`
- 本系统不会因 GBK 编码问题崩溃或产生乱码

### 5. 完整验收测试命令（Windows）

```powershell
# 初始化
python -m power_analytics.cli init --reset-db

# 生成样例数据
python -m power_analytics.cli generate-samples

# ========== 成功导入测试 ==========
# 导入正常数据 (360行)
python -m power_analytics.cli import-file sample_data/normal_readings.csv -n "正常数据测试"

# 导入带异常的数据 (14行)
python -m power_analytics.cli import-file sample_data/with_anomalies.csv -n "异常数据测试"

# ========== 失败链路测试（均应失败，退出码 != 0）==========
# 1. 缺少必填列 (缺少 meter_id, timezone)
python -m power_analytics.cli import-file sample_data/error_missing_columns.csv -n "缺列测试"

# 2. 无效时区 (Invalid/Timezone, Asia/Beijing)
python -m power_analytics.cli import-file sample_data/error_invalid_timezone.csv -n "时区错误测试"

# 3. 无效设备引用 (DEV999 不存在)
python -m power_analytics.cli import-file sample_data/error_invalid_device.csv -n "设备错误测试"

# ========== 异常分析 ==========
python -m power_analytics.cli analyze 2

# 查看异常详情
python -m power_analytics.cli show 2

# ========== 人工修正 ==========
python -m power_analytics.cli correct 1 99999.0 --note "人工测试修正" -u "测试员"

# ========== 回滚测试 ==========
python -m power_analytics.cli rollback 2 -r "测试回滚" -u "测试员"

# 验证回滚后状态
python -m power_analytics.cli list

# ========== 报告导出 ==========
python -m power_analytics.cli export html 1 -u "测试员"
python -m power_analytics.cli export csv 1 -u "测试员"
python -m power_analytics.cli export anomalies --batch-id 1 -u "测试员"

# 查看导出历史
python -m power_analytics.cli export history
```

### 6. 预期结果汇总

| 测试场景 | 结果 | 预期异常/退出码 |
|---------|------|----------------|
| `init --reset-db` | ✅ 成功 | 退出码 0 |
| `generate-samples` | ✅ 成功 | 生成 7 个测试文件 |
| `import normal_readings.csv` | ✅ 成功 | 360 行有效，退出码 0 |
| `import with_anomalies.csv` | ✅ 成功 | 14 行有效，退出码 0 |
| `analyze 2` | ✅ 成功 | 发现 5+ 个异常 |
| **import error_missing_columns.csv** | ❌ 失败 | 退出码 != 0，提示缺少必填列 |
| **import error_invalid_timezone.csv** | ❌ 失败 | 退出码 != 0，提示无效时区 |
| **import error_invalid_device.csv** | ❌ 失败 | 退出码 != 0，提示设备不存在 |
| `rollback 2` | ✅ 成功 | 批次状态变为"已回滚" |
| `export html 1` | ✅ 成功 | 生成 HTML 报告 |

## 项目结构

```
power_analytics/
├── __init__.py              # 包初始化
├── config.py                # 全局配置
├── database.py              # 数据库连接管理
├── models.py                # 12张核心数据表模型
├── validators.py            # 数据校验器
├── data_import.py           # CSV/Excel导入服务
├── batch_manager.py         # 批次生命周期管理
├── anomaly_detector.py      # 异常检测引擎
├── correction_engine.py     # 修正规则引擎
├── report_exporter.py       # 报告导出服务
├── cli.py                   # 命令行接口
├── output_utils.py          # 跨平台安全输出工具
├── sample_data_generator.py # 样例数据生成器
└── templates/
    └── report.html          # HTML报告模板
```

## 运行测试

```bash
# 运行所有自动化测试
python -m pytest test_power_analytics.py -v

# 查看测试覆盖率
python -m pytest test_power_analytics.py -v --cov=power_analytics
```

## 核心修复说明

### 1. Windows GBK 编码问题 (已修复)

**问题**：Windows CMD 默认 GBK 编码，输出表情符号（✅, ❌ 等）触发 `UnicodeEncodeError`。

**解决方案**：
- 创建 `output_utils.py` 提供安全输出函数
- 自动检测控制台编码，不支持 Unicode 时将表情替换为纯文本（`[OK]`, `[ERROR]` 等）
- CLI 中所有 `click.echo()` 替换为 `safe_echo()`
- 尝试启用 Windows UTF-8 控制台模式

### 2. 无效设备引用处理 (已修复)

**问题**：导入引用不存在设备的行时，只标记为无效行但继续处理，最终生成"有效行数为0"的批次，污染数据库。

**根因**：`data_import.py` 中只有 `MISSING_REQUIRED_FIELD` 和 `INVALID_TIMEZONE` 触发回滚，`INVALID_DEVICE` 只是标记无效行。

**解决方案**：
- 将 `INVALID_DEVICE` 和 `DUPLICATE_REPORT` 加入严重错误列表
- 发现即回滚事务，不生成批次记录
- 明确提示"设备不存在"，包含具体的设备编号
- 修复验证逻辑漏洞：数据库无设备时也能正确检测

## 详细文档

- [使用手册](docs/USAGE.md) - 完整命令参考和示例
- [架构总结](docs/SUMMARY.md) - 数据表结构和核心特性

## 许可证

MIT License
