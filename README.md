# trade-msg

A 股短线复盘提醒工具。每天收盘后自动获取行情数据，写入本地 MySQL，生成简洁中文复盘，并通过邮件推送到手机。

> 仅用于复盘研究，不构成投资建议。

## 主要功能

- 市场概览：指数、涨跌家数、涨停/跌停、成交额、市场情绪。
- 热点与龙头：涨停池、连板高度、人气榜、行业/概念热度。
- 短线机会：按“龙头反弹、龙头低吸、龙头二波”分类输出候选股。
- 候选股信息：代码、名称、策略标签、推荐值、入场观察条件、失效条件、核心依据。
- 数据入库：行情、指数、涨停池、人气榜、行业/概念热度、候选评分、报告发送状态写入 MySQL。
- 报告归档：按交易日期保存 HTML 和 TXT 文件。

## 快速开始

```powershell
cd E:\codexProjects\trade-msg
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

在 `.env` 中填写邮箱 SMTP 信息。`SMTP_PASSWORD` 通常是邮箱授权码，不是登录密码。

同时填写 MySQL 信息：

```env
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=你的MySQL密码
MYSQL_DATABASE=trade_msg
MYSQL_CHARSET=utf8mb4
```

## 常用命令

生成本地复盘，不发送邮件：

```powershell
python -m src.cli --dry-run
```

只采集行情并写入 MySQL：

```powershell
python -m src.cli --fetch-only
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

## 输出文件

```text
reports/latest.html
reports/latest.txt
reports/YYYY-MM-DD/recap.html
reports/YYYY-MM-DD/recap.txt
```

报告日期按最近可用交易日计算：每天 09:00 前使用上一个交易日；周末和节假日自动回退到最近交易日。

## 配置

- `config.yaml`：复盘规则、候选数量、报告时间、交易日分界时间。
- `.env`：邮箱账号、授权码、收件人等私密配置，不会提交到 Git。
- MySQL 默认数据库：`trade_msg`，首次运行会自动建库建表。

## 测试

```powershell
python -m unittest discover -s tests -v
```
