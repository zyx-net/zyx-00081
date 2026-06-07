# 用电数据分析系统 - 使用手册

## 系统概述

本系统用于核对连锁门店的电表读数、营业时段和设备配置，识别各类用电异常。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 初始化系统

```bash
# Windows / Linux / macOS 通用
python -m power_analytics.cli init

# 重置数据库（删除所有数据）
python -m power_analytics.cli init --reset-db
```

### Windows 控制台编码说明

**重要**：Windows CMD/PowerShell 默认使用 GBK 编码，本系统已自动兼容：
- 所有输出会自动转换为 GBK 安全的文本
- 表情符号（✅, ❌ 等）自动替换为纯文本（[OK], [ERROR] 等）
- 如果希望显示完整 Unicode 表情，先执行：`chcp 65001`
- 系统**不会**因为编码问题崩溃或产生乱码

### 3. 生成样例数据

```bash
python -m power_analytics.cli generate-samples
```

### 4. 导入数据

```bash
python -m power_analytics.cli import-file sample_data/normal_readings.csv -n "正常数据批次"
```

### 5. 分析异常

```bash
python -m power_analytics.cli analyze 1
```

### 6. 导出报告

```bash
python -m power_analytics.cli export html 1
python -m power_analytics.cli export csv 1
```

## 核心功能详解

### 数据导入

**支持格式**: CSV、Excel (.xlsx, .xls)

**必填字段**:
- `store_id` - 门店编号
- `meter_id` - 电表编号
- `reading_date` - 抄表日期
- `reading_value` - 电表读数
- `timezone` - 时区

**可选字段**:
- `store_name` - 门店名称
- `reading_time` - 抄表时间 (默认 00:00)
- `reading_unit` - 读数单位 (默认 kWh)
- `opening_time` - 开门时间
- `closing_time` - 关门时间
- `operator` - 抄表员
- `device_id` - 设备编号

**命令示例**:
```bash
# 导入CSV文件
python -m power_analytics.cli import-file data.csv

# 导入Excel文件并指定批次名称
python -m power_analytics.cli import-file data.xlsx -n "2024年6月数据" -d "6月份各门店电表数据" -u "管理员"
```

### 批次管理

**查看批次列表**:
```bash
python -m power_analytics.cli list
python -m power_analytics.cli list -s processed
python -m power_analytics.cli list -l 50
```

**查看批次详情**:
```bash
python -m power_analytics.cli show 1
```

**提交批次**:
```bash
python -m power_analytics.cli commit 1 -u "审核员"
```

**回滚批次**:
```bash
python -m power_analytics.cli rollback 1 -r "数据有误，需要重新导入" -u "管理员"
```

### 异常识别

系统自动识别以下异常类型:

| 异常类型 | 代码 | 严重程度 | 说明 |
|---------|------|---------|------|
| 尖峰用电 | PEAK_USAGE | warning | 尖峰时段用电量超过平均值2倍 |
| 闭店后耗电 | OFF_PEAK_USAGE | warning | 门店关闭后仍有超过5kWh的用电 |
| 读数倒退 | READING_DROP | error | 电表读数较上次下降超过0.1kWh |
| 缺表 | METER_MISSING | error | 门店电表在本批次中无记录 |
| 重复上报 | DUPLICATE_REPORT | error | 同一门店同一时段多条记录 |
| 无效时区 | INVALID_TIMEZONE | error | 时区值不在支持列表中 |
| 无效设备 | INVALID_DEVICE | error | 引用不存在的设备 |
| 缺少字段 | MISSING_REQUIRED_FIELD | error | 缺少必填字段 |

**支持的时区**:
- Asia/Shanghai - 中国标准时间
- UTC - 协调世界时
- America/New_York - 美国东部时间
- Europe/London - 英国时间

**异常分析命令**:
```bash
python -m power_analytics.cli analyze 1
```

### 人工修正

**创建修正规则**:
```bash
python -m power_analytics.cli rule create   --code "READING_TOO_LOW"   --name "读数过低修正"   --version "v1.0"   --condition "reading_value < 1000"   --action "reading_value * 10"   --description "读数小于1000时乘以10"
```

**查看规则列表**:
```bash
python -m power_analytics.cli rule list
```

**应用规则**:
```bash
# 应用于指定批次
python -m power_analytics.cli rule apply 1 --batch-id 1

# 应用于所有批次
python -m power_analytics.cli rule apply 1
```

**人工单条修正**:
```bash
python -m power_analytics.cli correct 10 10500.0 --note "抄表错误，原读数少写了一位" -u "数据员"
```

**回滚修正**:
```bash
python -m power_analytics.cli rollback-correction 5 -u "管理员"
```

### 报告导出

**导出HTML报告**:
```bash
python -m power_analytics.cli export html 1
```

**导出CSV报告**:
```bash
python -m power_analytics.cli export csv 1
```

**导出异常报告**:
```bash
# 所有异常
python -m power_analytics.cli export anomalies

# 指定批次的异常
python -m power_analytics.cli export anomalies --batch-id 1

# 按类型过滤
python -m power_analytics.cli export anomalies --type READING_DROP

# 只看未解决的
python -m power_analytics.cli export anomalies --unresolved
```

**查看导出历史**:
```bash
python -m power_analytics.cli export history
```

### 字段映射配置

系统支持自定义字段映射，用于适配不同格式的导入文件。

**示例**: 如果导入文件使用中文列名，可以配置映射:
```python
from power_analytics.data_import import DataImportService

service = DataImportService()
service.update_field_mapping(
    mapping_name="store_id",
    source_field="门店编号",
    target_field="store_id",
    data_type="string",
    is_required=True,
    description="门店编号",
)
```

## 样例命令和预期结果

### 正常导入流程

```bash
# 1. 生成样例数据
python -m power_analytics.cli generate-samples

# 2. 导入正常数据 (预期: 成功，360行有效数据)
python -m power_analytics.cli import-file sample_data/normal_readings.csv -n "正常数据测试"

# 3. 分析异常 (预期: 发现约15-20个异常)
python -m power_analytics.cli analyze 1

# 4. 查看异常详情
python -m power_analytics.cli show 1

# 5. 导出HTML报告
python -m power_analytics.cli export html 1

# 6. 提交批次
python -m power_analytics.cli commit 1 -u "测试员"
```

### 异常识别测试

```bash
# 导入包含异常的数据 (预期: 成功，8行有效数据)
python -m power_analytics.cli import-file sample_data/with_anomalies.csv -n "异常数据测试"

# 分析异常 (预期异常数量: 5+)
# - 读数倒退: 1个
# - 重复上报: 1个
# - 闭店后耗电: 1个
# - 尖峰用电: 1个
# - 缺表: 1个 (S003店无数据)
python -m power_analytics.cli analyze 2

# 查看异常
python -m power_analytics.cli show 2
```

### 失败链路测试

**缺少必填列**:
```bash
# 预期: 失败，提示缺少 meter_id, timezone 列
python -m power_analytics.cli import-file sample_data/error_missing_columns.csv -n "缺少列测试"
```

**无效时区**:
```bash
# 预期: 失败，提示无效时区: Invalid/Timezone, Asia/Beijing
python -m power_analytics.cli import-file sample_data/error_invalid_timezone.csv -n "时区错误测试"
```

**无效设备引用**:
```bash
# 预期: 第1行: 设备不存在: 门店S001的设备DEV999
python -m power_analytics.cli import-file sample_data/error_invalid_device.csv -n "设备错误测试"
```

**重复记录测试**:
```bash
# 先导入一批数据
python -m power_analytics.cli import-file sample_data/normal_readings.csv -n "第一批"

# 再导入相同时间范围的数据
# 预期: 提示重复记录
python -m power_analytics.cli import-file sample_data/normal_readings.csv -n "重复导入测试"
```

### 人工修正测试

```bash
# 创建修正规则
python -m power_analytics.cli rule create   --code "FIX_DROP"   --name "修正读数倒退"   --version "v1.1"   --condition "reading_value < old_value"   --action "old_value"   --description "读数倒退时恢复为上次值"

# 应用规则
python -m power_analytics.cli rule apply 1 --batch-id 2

# 查看修正历史
python -m power_analytics.cli show 2

# 手动修正一条记录
python -m power_analytics.cli correct 5 10000.0 --note "人工修正异常读数"

# 回滚批次
python -m power_analytics.cli rollback 2 -r "需要重新处理"
```

## 数据持久化说明

系统使用 SQLite 数据库存储所有数据，数据文件位于:
`data/power_analytics.db`

重启系统后以下数据保持一致:
- 批次列表和状态
- 规则版本和配置
- 修正历史记录
- 导出汇总记录
- 原始导入行数据

## 预期异常数量汇总

| 测试场景 | 总行数 | 结果 | 异常类型/退出码 |
|---------|-------|------|----------------|
| normal_readings.csv | 360 | ✅ 导入成功 | 15-20个异常 (PEAK_USAGE, OFF_PEAK_USAGE) |
| with_anomalies.csv | 14 | ✅ 导入成功 | 5+个异常 (READING_DROP, DUPLICATE_REPORT, OFF_PEAK_USAGE, PEAK_USAGE, METER_MISSING) |
| error_missing_columns.csv | 1 | ❌ 导入失败 | 退出码 != 0 (MISSING_REQUIRED_FIELD) |
| error_invalid_timezone.csv | 2 | ❌ 导入失败 | 退出码 != 0 (INVALID_TIMEZONE) |
| **error_invalid_device.csv** | 1 | ❌ 导入失败 | 退出码 != 0 (**INVALID_DEVICE**) |
| 重复导入测试 | - | ❌ 导入失败 | 退出码 != 0 (DUPLICATE_REPORT) |

### 失败链路统一行为
所有失败场景（缺列、无效时区、无效设备、重复记录）：
- ✅ 回滚事务，**不生成批次记录**
- ✅ **不污染数据库**（无Batch、RawRow、MeterReading记录）
- ✅ 明确提示错误原因和具体行号
- ✅ 退出码 != 0
- ✅ 不生成导出汇总

## 常见问题

**Q: 如何重置数据库？**
A: `python -m power_analytics.cli init --reset-db`

**Q: 如何查看所有批次的汇总信息？**
A: `python -m power_analytics.cli list`

**Q: 如何只导出未解决的异常？**
A: `python -m power_analytics.cli export anomalies --unresolved`

**Q: 支持哪些日期时间格式？**
A: 支持 YYYY-MM-DD, YYYY/MM/DD, YYYYMMDD 等多种格式，时间支持 HH:MM, HH:MM:SS 等。
