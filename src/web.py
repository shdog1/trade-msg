from __future__ import annotations

import argparse
import html
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import yaml

from .config import ROOT


CONFIG_PATH = ROOT / "config.yaml"
TASK_SCRIPT = ROOT / "scripts" / "install_windows_task.ps1"
SCORING_KEYS = [
    ("market_environment", "市场环境"),
    ("leader_strength", "龙头强度"),
    ("historical_shape", "历史形态"),
    ("intraday_confirmation", "当日确认"),
    ("liquidity_risk", "流动性"),
]


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), ConsoleHandler)
    print(f"Trade console running at http://{host}:{port}")
    server.serve_forever()


class ConsoleHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/":
            self.send_error(404)
            return
        self.respond(render_page(load_config(), None))

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        action = form_value(form, "action")
        config = load_config()
        message = ""
        try:
            if action == "save_config":
                update_config(config, form)
                save_config(config)
                message = "配置已保存。"
            elif action == "install_task":
                update_config(config, form)
                save_config(config)
                message = run_command(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(TASK_SCRIPT), "-Time", form_value(form, "report_time")])
            elif action in CLI_ACTIONS:
                message = run_command([sys.executable, "-m", "src.cli", *CLI_ACTIONS[action]])
            elif action == "backfill":
                days = form_value(form, "backfill_days") or "250"
                sleep = form_value(form, "backfill_sleep") or "1.5"
                message = run_command([sys.executable, "-m", "src.cli", "--backfill-days", days, "--backfill-sleep", sleep])
            else:
                message = "未知操作。"
        except Exception as exc:  # noqa: BLE001
            message = f"执行失败: {exc}"
        self.respond(render_page(load_config(), message))

    def respond(self, content: str) -> None:
        body = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


CLI_ACTIONS = {
    "dry_run": ["--dry-run"],
    "send": ["--send"],
    "fetch_only": ["--fetch-only"],
    "refresh_calendar": ["--refresh-calendar"],
    "test_email": ["--test-email"],
}


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def save_config(config: dict) -> None:
    with CONFIG_PATH.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, allow_unicode=True, sort_keys=False)


def update_config(config: dict, form: dict[str, list[str]]) -> None:
    app = config.setdefault("app", {})
    market = config.setdefault("market", {})
    scoring = config.setdefault("scoring", {})
    app["report_time"] = form_value(form, "report_time") or app.get("report_time", "18:00")
    app["data_ready_time"] = form_value(form, "data_ready_time") or app.get("data_ready_time", "09:00")
    app["skip_non_trading_day"] = form_value(form, "skip_non_trading_day") == "on"
    market["max_candidates"] = int(float(form_value(form, "max_candidates") or market.get("max_candidates", 8)))
    market["min_turnover_amount"] = int(float(form_value(form, "min_turnover_amount") or market.get("min_turnover_amount", 300000000)))
    for key, _ in SCORING_KEYS:
        scoring[key] = float(form_value(form, key) or scoring.get(key, 0))


def run_command(command: list[str]) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=3600,
        check=False,
    )
    output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
    return output or f"命令完成，退出码 {result.returncode}。"


def form_value(form: dict[str, list[str]], key: str) -> str:
    return form.get(key, [""])[0].strip()


def render_page(config: dict, message: str | None) -> str:
    app = config.get("app", {})
    market = config.get("market", {})
    scoring = config.get("scoring", {})
    weight_total = sum(float(scoring.get(key, 0) or 0) for key, _ in SCORING_KEYS)
    rows = "\n".join(
        f"""
        <label>{label}<input type="number" step="0.01" min="0" name="{key}" value="{html.escape(str(scoring.get(key, '0')))}"></label>
        """
        for key, label in SCORING_KEYS
    )
    message_html = f"<pre>{html.escape(message)}</pre>" if message else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>trade-msg 控制台</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Arial,sans-serif;margin:0;background:#f5f7fb;color:#1f2937}}
main{{max-width:980px;margin:0 auto;padding:24px}}
h1{{font-size:24px;margin:0 0 16px}} h2{{font-size:18px;margin:0 0 12px}}
section{{background:#fff;border:1px solid #e5e7eb;padding:16px;margin:14px 0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}
label{{display:flex;flex-direction:column;gap:6px;font-size:13px;color:#4b5563}}
input{{padding:8px;border:1px solid #d1d5db;font-size:14px}}
button{{padding:9px 12px;border:1px solid #1f5fbf;background:#2f6fed;color:#fff;cursor:pointer;margin:4px 6px 4px 0}}
button.secondary{{background:#fff;color:#1f5fbf}}
pre{{white-space:pre-wrap;background:#111827;color:#e5e7eb;padding:14px;max-height:320px;overflow:auto}}
.hint{{font-size:13px;color:#6b7280}}
</style>
</head>
<body><main>
<h1>trade-msg 控制台</h1>
{message_html}
<form method="post">
<section>
<h2>基础配置</h2>
<div class="grid">
<label>自动执行时间<input name="report_time" type="time" value="{html.escape(str(app.get('report_time', '18:00')))}"></label>
<label>数据可用时间<input name="data_ready_time" type="time" value="{html.escape(str(app.get('data_ready_time', '09:00')))}"></label>
<label>最大候选数<input name="max_candidates" type="number" min="1" max="50" value="{html.escape(str(market.get('max_candidates', 8)))}"></label>
<label>最低成交额<input name="min_turnover_amount" type="number" min="0" step="10000000" value="{html.escape(str(market.get('min_turnover_amount', 300000000)))}"></label>
</div>
<p><label><input name="skip_non_trading_day" type="checkbox" {'checked' if app.get('skip_non_trading_day', True) else ''}> 自动任务跳过非交易日</label></p>
</section>
<section>
<h2>评分权重</h2>
<p class="hint">当前合计：{weight_total:.2f}。系统会自动归一化，不必严格等于 1。</p>
<div class="grid">{rows}</div>
</section>
<section>
<button name="action" value="save_config">保存配置</button>
<button name="action" value="install_task">保存并安装自动任务</button>
</section>
<section>
<h2>手动执行</h2>
<button class="secondary" name="action" value="dry_run">生成复盘</button>
<button class="secondary" name="action" value="send">发送邮件</button>
<button class="secondary" name="action" value="fetch_only">采集行情</button>
<button class="secondary" name="action" value="refresh_calendar">刷新交易日历</button>
<button class="secondary" name="action" value="test_email">测试邮件</button>
<div class="grid">
<label>回补天数<input name="backfill_days" type="number" min="1" value="250"></label>
<label>请求间隔秒<input name="backfill_sleep" type="number" min="0" step="0.1" value="1.5"></label>
</div>
<button class="secondary" name="action" value="backfill">回补主板日 K</button>
</section>
</form>
</main></body></html>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local trade-msg web console.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    run_server(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
