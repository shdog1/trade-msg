# trade-msg

A 股短线复盘提醒工具。每天收盘后采集行情，写入本地 MySQL，生成简洁中文复盘报告，并通过邮件推送到手机。

> 仅用于复盘研究。推荐值代表观察优先级，不构成投资建议。

## 主要功能

- 市场概览：指数、上涨/下跌家数、涨停/跌停、成交额、市场情绪。
- 热点与龙头：涨停池、连板高度、人气榜、行业/概念热度。
- 短线机会：按“龙头反弹、龙头低吸、龙头二波”分类输出候选股。
- 历史 K 线评分：结合近 5/10/20/60 日走势、前高回撤、均线状态、成交额变化。
- 数据入库：行情、指数、涨停池、人气榜、板块热度、历史日 K、候选评分、报告发送记录写入 MySQL。
- 报告归档：按交易日期保存 HTML 和 TXT 文件。

## 快速开始

```powershell
cd E:\codexProjects\trade-msg
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

在 `.env` 填写邮箱 SMTP 和 MySQL 信息。`SMTP_PASSWORD` 通常是邮箱授权码，不是登录密码。

```env
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=你的MySQL密码
MYSQL_DATABASE=trade_msg
MYSQL_CHARSET=utf8mb4
```

## 常用命令

启动本地控制台：

```powershell
python -m src.cli --web
```

浏览器打开：

```text
http://127.0.0.1:8765
```

采集当天复盘数据并写入 MySQL：

```powershell
python -m src.cli --fetch-only
```

回补最近 250 天主板历史日 K：

```powershell
python -m src.cli --backfill-days 250 --backfill-sleep 1.5
```

刷新交易日历到 MySQL：

```powershell
python -m src.cli --refresh-calendar
```

回补单只股票历史日 K：

```powershell
python -m src.cli --backfill-days 250 --backfill-stock 600001 --backfill-sleep 1.5
```

如需回补全 A：

```powershell
python -m src.cli --backfill-days 250 --backfill-all --backfill-sleep 1.5
```

生成本地复盘，不发邮件：

```powershell
python -m src.cli --dry-run
```

发送复盘邮件：

```powershell
python -m src.cli --send
```

指定交易日复盘：

```powershell
python -m src.cli --date 2026-05-29 --dry-run
```

测试邮箱配置：

```powershell
python -m src.cli --test-email
```

安装 Windows 每日 18:00 自动任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows_task.ps1
```

自动任务会使用 `trade_calendar` 判断当天是否交易日。非交易日自动跳过；手动执行 `python -m src.cli --send` 仍会发送最近交易日复盘。

## 分析依据

候选股评分为 100 分制：

- 市场环境：15 分，看上涨占比、涨停数、平均涨跌幅。
- 龙头强度：25 分，看人气排名、连板/涨停强度、成交额。
- 历史形态：30 分，看近 20/60 日涨幅、前高回撤、均线位置、是否二波突破。
- 当日确认：20 分，看当日涨跌幅、量比、振幅、策略标签。
- 流动性：10 分，看成交额。

如果某只股票历史 K 线不足，系统会保留当日强度兜底，但报告会标记“历史样本不足，按当日强度兜底”。

评分权重可在本地控制台修改，保存后写入 `config.yaml`。权重会自动归一化，不要求合计必须等于 1。

## 数据表

- `stock_basic`：股票基础资料。
- `market_quotes`：每日收盘复盘行情快照。
- `quote_snapshots`：实时行情采集快照。
- `daily_bars`：历史日 K，用于短线形态分析。
- `trade_calendar`：A 股交易日历，用于自动任务跳过非交易日。
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

报告日期按最近可用交易日计算：每天 09:00 前使用上一个交易日；周末和节假日自动回退到最近交易日。

## 测试

```powershell
python -m unittest discover -s tests -v
```
