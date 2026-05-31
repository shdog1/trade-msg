# trade-msg

A 股短线复盘提醒工具。收盘后采集行情，写入本地 MySQL，生成简洁中文复盘报告，并通过邮件推送到手机。

> 仅用于复盘研究。推荐值代表观察优先级，不构成投资建议。

## 功能

- 市场概览：指数、上涨/下跌家数、涨停/跌停、成交额、市场情绪。
- 热点与龙头：涨停池、连板高度、人气榜、行业/概念热度。
- 短线机会：按“龙头反弹、龙头低吸、龙头二波”分类输出候选股。
- 历史 K 线评分：结合近 5/10/20/60 日走势、前高回撤、均线状态、成交额变化。
- 本地数据库：行情、历史日 K、候选评分、报告发送记录写入 MySQL。
- 本地控制台：配置自动执行时间、评分权重，手动执行任务并查看输出。

## 快速开始

```powershell
cd E:\codexProjects\trade-msg
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

在 `.env` 填写邮箱 SMTP 和 MySQL 信息。`SMTP_PASSWORD` 通常是邮箱授权码。

```env
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=你的MySQL密码
MYSQL_DATABASE=trade_msg
MYSQL_CHARSET=utf8mb4
```

## 本地控制台

启动：

```powershell
python -m src.cli --web
```

浏览器打开：

```text
http://127.0.0.1:8765
```

控制台可做：

- 修改自动执行时间。
- 最低成交额以“亿元”为单位配置。
- 评分权重以百分比配置。
- 保存配置到 `config.yaml`。
- 安装 Windows 自动任务。
- 手动执行采集、复盘、发送、刷新交易日历、测试邮件、回补日 K。
- 查看执行过程输出，并可中断当前任务。
- 回补历史日 K 可填写多个股票代码，逗号、空格或换行分隔；留空则回补主板。

## 常用命令

采集当天复盘数据并写入 MySQL：

```powershell
python -m src.cli --fetch-only
```

刷新交易日历到 MySQL：

```powershell
python -m src.cli --refresh-calendar
```

回补最近 250 天主板历史日 K：

```powershell
python -m src.cli --backfill-days 250 --backfill-sleep 1.5
```

回补单只股票：

```powershell
python -m src.cli --backfill-days 250 --backfill-stock 600001 --backfill-sleep 1.5
```

回补多只股票：

```powershell
python -m src.cli --backfill-days 250 --backfill-stock 600001,000001 --backfill-stock 002001 --backfill-sleep 1.5
```

生成本地复盘，不发邮件：

```powershell
python -m src.cli --dry-run
```

发送复盘邮件：

```powershell
python -m src.cli --send
```

安装 Windows 每日自动任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows_task.ps1
```

自动任务使用 `trade_calendar` 判断是否交易日。非交易日跳过；手动 `--send` 仍会发送最近交易日复盘。

## 评分依据

候选股评分为 100 分制，权重可在控制台修改：

- 市场环境：默认 15%。
- 龙头强度：默认 25%。
- 历史形态：默认 30%。
- 当日确认：默认 20%。
- 流动性：默认 10%。

如果某只股票历史 K 线不足，系统保留当日强度兜底，但报告会标记“历史样本不足，按当日强度兜底”。

## 数据表

- `trade_calendar`：A 股交易日历。
- `stock_basic`：股票基础资料。
- `market_quotes`：每日收盘复盘行情快照。
- `quote_snapshots`：实时行情采集快照。
- `daily_bars`：历史日 K，用于短线形态分析。
- `index_quotes`：指数行情。
- `limit_pool`：涨停池和连板数据。
- `hot_ranks`：人气排名。
- `hot_topics`：行业/概念热度。
- `recap_candidates`：候选股评分结果。
- `recap_reports`：报告生成和发送状态。
- `fetch_runs`：采集任务日志。

## 输出文件

```text
reports/latest.html
reports/latest.txt
reports/YYYY-MM-DD/recap.html
reports/YYYY-MM-DD/recap.txt
```

## 测试

```powershell
python -m unittest discover -s tests -v
```
