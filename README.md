# trade-msg

A 股短线复盘工具：采集行情并写入 MySQL，生成中文复盘报告，可通过邮件推送。

> 仅用于复盘研究，候选结果不构成投资建议。

## 主要功能

- 市场概览、涨停池、连板天梯和热点排行
- 连板平台洗盘形态识别与候选评分
- 历史日 K、行情快照和报告记录持久化
- 本地 Web 控制台、邮件通知和 Windows 定时任务

## 快速开始

要求：Python 3.10+、MySQL。

```powershell
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

在 `.env` 中填写 MySQL 和邮箱 SMTP 配置，然后启动控制台：

```powershell
python -m src.cli --web
```

浏览器访问 <http://127.0.0.1:8765>。运行时间和策略参数保存在 `config.yaml`。

## 常用命令

```powershell
# 完整日常流程
python -m src.cli --daily-job

# 仅采集数据
python -m src.cli --fetch-only

# 生成本地报告，不发邮件
python -m src.cli --dry-run

# 生成并发送邮件
python -m src.cli --send

# 刷新交易日历
python -m src.cli --refresh-calendar

# 回补日 K，可用 --backfill-stock 指定股票
python -m src.cli --backfill-days 250 --backfill-sleep 1.5

# 回补连板数据
python -m src.cli --backfill-limit-pool-days 90 --limit-pool-sleep 1.0
```

完整参数：

```powershell
python -m src.cli --help
```

报告输出到 `reports/`。

## Windows 定时任务

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows_task.ps1
```

定时任务会根据交易日历自动跳过非交易日。

## 测试

```powershell
python -m pytest
```
