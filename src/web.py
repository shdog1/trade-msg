from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import yaml

from .config import ROOT, load_settings
from .database import MySQLStore


CONFIG_PATH = ROOT / "config.yaml"
TASK_SCRIPT = ROOT / "scripts" / "install_windows_task.ps1"
SCORING_KEYS = [
    ("market_environment", "市场环境"),
    ("leader_strength", "龙头强度"),
    ("historical_shape", "历史形态"),
    ("intraday_confirmation", "当日确认"),
    ("liquidity_risk", "流动性"),
]


@dataclass
class JobState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    process: subprocess.Popen[str] | None = None
    title: str = ""
    output: list[str] = field(default_factory=list)
    return_code: int | None = None

    def start(self, title: str, command: list[str]) -> None:
        with self.lock:
            if self.process and self.process.poll() is None:
                self.output.append("已有任务正在执行，请先中断或等待完成。")
                return
            self.title = title
            self.output = [f"已启动：{title}", "> " + " ".join(command)]
            self.return_code = None
            self.process = subprocess.Popen(
                command,
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
            )
            threading.Thread(target=self._reader, daemon=True).start()

    def note(self, message: str) -> None:
        with self.lock:
            if self.process and self.process.poll() is None:
                self.output.append(message)
                return
            self.title = "配置操作"
            self.return_code = None
            self.output = [message]

    def _reader(self) -> None:
        process = self.process
        if not process:
            return
        assert process.stdout is not None
        for line in process.stdout:
            with self.lock:
                self.output.append(line.rstrip())
        code = process.wait()
        with self.lock:
            self.return_code = code
            self.output.append(f"任务结束，退出码 {code}。")

    def stop(self) -> None:
        with self.lock:
            if not self.process or self.process.poll() is not None:
                self.note("当前没有运行中的任务。")
                return
            self.process.terminate()
            self.output.append("已请求中断当前任务。")

    def snapshot(self) -> dict[str, object]:
        with self.lock:
            running = bool(self.process and self.process.poll() is None)
            return {
                "title": self.title,
                "running": running,
                "return_code": self.return_code,
                "output": "\n".join(self.output[-800:]),
            }


JOB = JobState()


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), ConsoleHandler)
    print(f"Trade console running at http://{host}:{port}")
    server.serve_forever()


class ConsoleHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            self.respond_html(render_page(load_config()))
            return
        if self.path.startswith("/report"):
            self.respond_html(render_report_page())
            return
        if self.path == "/status":
            self.respond_json(JOB.snapshot())
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        action = form_value(form, "action")
        config = load_config()
        try:
            if action == "save_config":
                update_config(config, form)
                save_config(config)
                JOB.note("配置已保存。")
            elif action == "install_task":
                update_config(config, form)
                save_config(config)
                command = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(TASK_SCRIPT), "-Time", form_value(form, "report_time")]
                JOB.start("安装自动任务", command)
            elif action == "stop":
                JOB.stop()
            elif action in CLI_ACTIONS:
                title, args = CLI_ACTIONS[action]
                JOB.start(title, [sys.executable, "-u", "-m", "src.cli", *args])
            elif action == "backfill":
                JOB.start("回补历史日 K", build_backfill_command(form))
            else:
                JOB.note("未知操作。")
        except Exception as exc:  # noqa: BLE001
            JOB.note(f"执行失败：{exc}")
        self.respond_html(render_page(load_config()))

    def respond_html(self, content: str) -> None:
        body = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def respond_json(self, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


CLI_ACTIONS = {
    "dry_run": ("生成复盘", ["--dry-run"]),
    "send": ("发送邮件", ["--send"]),
    "fetch_only": ("采集行情", ["--fetch-only"]),
    "refresh_calendar": ("刷新交易日历", ["--refresh-calendar"]),
    "test_email": ("测试邮件", ["--test-email"]),
}


def build_backfill_command(form: dict[str, list[str]]) -> list[str]:
    command = [
        sys.executable,
        "-u",
        "-m",
        "src.cli",
        "--backfill-days",
        form_value(form, "backfill_days") or "250",
        "--backfill-sleep",
        form_value(form, "backfill_sleep") or "1.5",
    ]
    for code in split_codes(form_value(form, "backfill_stocks")):
        command.extend(["--backfill-stock", code])
    return command


def split_codes(value: str) -> list[str]:
    codes: list[str] = []
    for item in value.replace("，", ",").replace("\n", ",").replace(" ", ",").split(","):
        code = item.strip()
        if code:
            codes.append(code)
    return codes


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
    turnover_yi = float(form_value(form, "min_turnover_yi") or 3)
    market["min_turnover_amount"] = int(turnover_yi * 100_000_000)
    for key, _ in SCORING_KEYS:
        scoring[key] = float(form_value(form, key) or 0) / 100


def form_value(form: dict[str, list[str]], key: str) -> str:
    return form.get(key, [""])[0].strip()


def render_page(config: dict) -> str:
    app = config.get("app", {})
    market = config.get("market", {})
    scoring = config.get("scoring", {})
    min_turnover_yi = float(market.get("min_turnover_amount", 300_000_000) or 0) / 100_000_000
    weight_total = sum(float(scoring.get(key, 0) or 0) * 100 for key, _ in SCORING_KEYS)
    rows = "\n".join(
        f"""
        <label>{label}（%）<input type="number" step="1" min="0" name="{key}" value="{html.escape(str(round(float(scoring.get(key, 0) or 0) * 100, 2)))}"></label>
        """
        for key, label in SCORING_KEYS
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>trade-msg 控制台</title>
<style>
:root{{
  --bg:#f5f7fb;--panel:#ffffff;--ink:#172033;--muted:#667085;--line:#d8deea;
  --blue:#2f6fed;--blue-soft:#eaf1ff;--green:#138a5e;--green-soft:#e8f7ef;
  --amber:#a15c07;--amber-soft:#fff4df;--red:#b42318;--red-soft:#fee4e2;
}}
*{{box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Arial,sans-serif;margin:0;background:
linear-gradient(135deg,#f7f9ff 0%,#f3fbf7 55%,#fff8ec 100%);color:var(--ink)}}
main{{max-width:1120px;margin:0 auto;padding:28px}}
.top{{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin-bottom:18px}}
h1{{font-size:26px;margin:0}} h2{{font-size:18px;margin:0 0 14px}} p{{margin:0}}
.hint,.status{{font-size:13px;color:var(--muted)}}
.layout{{display:grid;grid-template-columns:1fr;gap:16px}}
section{{background:rgba(255,255,255,.92);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 10px 30px rgba(31,43,70,.07)}}
.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}}
.grid.weights{{grid-template-columns:repeat(5,minmax(0,1fr))}}
label{{display:flex;flex-direction:column;gap:7px;font-size:13px;color:#475467;min-width:0}}
input{{width:100%;height:40px;border:1px solid #cfd6e4;border-radius:10px;padding:8px 10px;background:#fff;font-size:14px;color:var(--ink);outline:none}}
input:focus{{border-color:var(--blue);box-shadow:0 0 0 3px rgba(47,111,237,.14)}}
input[type=checkbox]{{width:18px;height:18px;margin:0}}
.check{{display:flex;align-items:center;gap:9px;flex-direction:row;margin-top:14px}}
.actions{{display:flex;flex-wrap:wrap;gap:10px}}
button{{min-height:38px;border:1px solid var(--blue);border-radius:10px;background:var(--blue);color:#fff;cursor:pointer;padding:9px 13px;font-size:14px}}
button.secondary{{background:var(--blue-soft);color:#164ca5;border-color:#b7cbff}}
button.success{{background:var(--green);border-color:var(--green)}}
button.warning{{background:var(--amber);border-color:var(--amber)}}
button.danger{{background:var(--red);border-color:var(--red)}}
.link-button{{display:inline-flex;align-items:center;min-height:38px;border:1px solid #b7cbff;border-radius:10px;background:var(--blue-soft);color:#164ca5;text-decoration:none;padding:9px 13px;font-size:14px}}
.job-head{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px}}
.pill{{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:5px 10px;background:var(--green-soft);color:var(--green);font-size:13px}}
pre{{white-space:pre-wrap;background:#111827;color:#e5e7eb;border-radius:12px;padding:14px;min-height:230px;max-height:430px;overflow:auto;margin:0;font-size:13px;line-height:1.45}}
@media(max-width:900px){{.grid,.grid.weights{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
@media(max-width:560px){{main{{padding:16px}}.grid,.grid.weights{{grid-template-columns:1fr}}.top,.job-head{{align-items:flex-start;flex-direction:column}}}}
</style>
<script>
async function pollStatus(){{
  const res = await fetch('/status');
  const data = await res.json();
  document.getElementById('job-title').textContent = data.title || '无任务';
  document.getElementById('job-state').textContent = data.running ? '运行中' : '空闲';
  document.getElementById('job-output').textContent = data.output || '';
}}
setInterval(pollStatus, 1200);
window.addEventListener('load', pollStatus);
</script>
</head>
<body><main>
<div class="top">
  <div><h1>trade-msg 控制台</h1><p class="hint">本地配置、执行、日志查看。</p></div>
  <div class="actions"><a class="link-button" href="/report">查看最近报告</a><span class="pill">本机运行 127.0.0.1</span></div>
</div>
<form method="post" class="layout">
<section>
<h2>基础配置</h2>
<div class="grid">
<label>自动执行时间<input name="report_time" type="time" value="{html.escape(str(app.get('report_time', '18:00')))}"></label>
<label>数据可用时间<input name="data_ready_time" type="time" value="{html.escape(str(app.get('data_ready_time', '09:00')))}"></label>
<label>最大候选数<input name="max_candidates" type="number" min="1" max="50" value="{html.escape(str(market.get('max_candidates', 8)))}"></label>
<label>最低成交额（亿元）<input name="min_turnover_yi" type="number" min="0" step="0.1" value="{min_turnover_yi:.2f}"></label>
</div>
<label class="check"><input name="skip_non_trading_day" type="checkbox" {'checked' if app.get('skip_non_trading_day', True) else ''}> 自动任务跳过非交易日</label>
</section>
<section>
<h2>评分权重</h2>
<p class="hint">当前合计：{weight_total:.1f}%。系统自动归一化，不要求合计等于 100%。</p>
<div class="grid weights">{rows}</div>
</section>
<section>
<h2>配置操作</h2>
<div class="actions">
<button class="success" name="action" value="save_config">保存配置</button>
<button class="warning" name="action" value="install_task">保存并安装自动任务</button>
</div>
</section>
<section>
<h2>手动执行</h2>
<div class="actions">
<button class="secondary" name="action" value="dry_run">生成复盘</button>
<button class="secondary" name="action" value="send">发送邮件</button>
<button class="secondary" name="action" value="fetch_only">采集行情</button>
<button class="secondary" name="action" value="refresh_calendar">刷新交易日历</button>
<button class="secondary" name="action" value="test_email">测试邮件</button>
</div>
<div class="grid" style="margin-top:14px">
<label>回补天数<input name="backfill_days" type="number" min="1" value="250"></label>
<label>请求间隔秒<input name="backfill_sleep" type="number" min="0" step="0.1" value="1.5"></label>
<label style="grid-column:span 2">指定股票代码（逗号/空格/换行分隔，可空）<input name="backfill_stocks" placeholder="600001, 000001"></label>
</div>
<div class="actions" style="margin-top:12px"><button class="secondary" name="action" value="backfill">回补历史日 K</button></div>
</section>
<section>
<div class="job-head">
  <h2>执行过程</h2>
  <p class="status">任务：<span id="job-title">无任务</span> | 状态：<span id="job-state">空闲</span></p>
</div>
<div class="actions" style="margin-bottom:12px"><button class="danger" name="action" value="stop">中断当前任务</button></div>
<pre id="job-output"></pre>
</section>
</form>
</main></body></html>"""


def render_report_page() -> str:
    try:
        payload = load_latest_report_payload()
    except Exception as exc:  # noqa: BLE001
        body = f"<section><h2>读取失败</h2><p>{html.escape(str(exc))}</p></section>"
        return render_shell("最近复盘报告", body, active_report=True)

    if not payload["report"]:
        return render_shell("最近复盘报告", "<section><h2>暂无报告</h2><p>请先生成一次复盘。</p></section>", active_report=True)

    report = payload["report"]
    candidates = payload["candidates"]
    cards = "\n".join(render_candidate_chart(item, payload["bars"].get(item["code"], [])) for item in candidates)
    candidate_html = cards or "<p class=\"hint\">暂无候选股。</p>"
    body = f"""
<section>
<h2>{html.escape(str(report.get('title') or '最近复盘报告'))}</h2>
<p class="hint">交易日：{html.escape(str(report.get('trade_date')))} | 发送状态：{html.escape(str(report.get('send_status') or '-'))}</p>
</section>
<section>
<h2>候选股票日线与成交量</h2>
<div class="chart-grid">{candidate_html}</div>
</section>
"""
    return render_shell("最近复盘报告", body, active_report=True)


def render_shell(title: str, body: str, active_report: bool = False) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
{base_style()}
</head>
<body><main>
<div class="top">
  <div><h1>{html.escape(title)}</h1><p class="hint">最近一次复盘结果。</p></div>
  <div class="actions"><a class="link-button" href="/">控制台</a><a class="link-button {'active' if active_report else ''}" href="/report">最近报告</a></div>
</div>
{body}
</main></body></html>"""


def load_latest_report_payload() -> dict[str, object]:
    settings = load_settings(CONFIG_PATH)
    store = MySQLStore.from_env(settings.raw)
    store.initialize()
    report_df = store._read_df("SELECT * FROM recap_reports ORDER BY trade_date DESC LIMIT 1", {})
    if report_df.empty:
        return {"report": None, "candidates": [], "bars": {}}
    report = dict(report_df.iloc[0].to_dict())
    trade_date = report["trade_date"]
    candidate_df = store._read_df(
        "SELECT code, name, strategy_tags, score, trigger_text, invalidation_text, reasons "
        "FROM recap_candidates WHERE trade_date = :trade_date ORDER BY score DESC",
        {"trade_date": trade_date},
    )
    candidates = [normalize_candidate_row(row) for row in candidate_df.to_dict("records")]
    bars = {
        item["code"]: store._read_df(
            "SELECT trade_date, close_price, turnover FROM daily_bars "
            "WHERE code = :code AND trade_date <= :trade_date ORDER BY trade_date DESC LIMIT 80",
            {"code": item["code"], "trade_date": trade_date},
        ).sort_values("trade_date").to_dict("records")
        for item in candidates
    }
    return {"report": report, "candidates": candidates, "bars": bars}


def normalize_candidate_row(row: dict[str, object]) -> dict[str, object]:
    return {
        "code": str(row.get("code") or ""),
        "name": str(row.get("name") or ""),
        "score": int(row.get("score") or 0),
        "strategy_tags": parse_json_list(row.get("strategy_tags")),
        "reasons": parse_json_list(row.get("reasons")),
        "trigger_text": str(row.get("trigger_text") or ""),
        "invalidation_text": str(row.get("invalidation_text") or ""),
    }


def parse_json_list(value: object) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return [str(value)]
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return [str(parsed)]


def render_candidate_chart(candidate: dict[str, object], bars: list[dict[str, object]]) -> str:
    tags = "".join(f"<span class=\"tag\">{html.escape(tag)}</span>" for tag in candidate["strategy_tags"])
    reasons = "; ".join(str(item) for item in candidate["reasons"])
    return f"""
<article class="stock-card">
<div class="stock-head">
  <div><strong>{html.escape(str(candidate['code']))} {html.escape(str(candidate['name']))}</strong><div>{tags}</div></div>
  <span class="score">{candidate['score']}%</span>
</div>
{render_price_volume_svg(bars)}
<p class="hint">入场：{html.escape(str(candidate['trigger_text']))}</p>
<p class="hint">失效：{html.escape(str(candidate['invalidation_text']))}</p>
<p class="hint">依据：{html.escape(reasons)}</p>
</article>
"""


def render_price_volume_svg(bars: list[dict[str, object]]) -> str:
    if len(bars) < 2:
        return "<div class=\"empty-chart\">日线数据不足</div>"
    closes = [float(item.get("close_price") or 0) for item in bars]
    turnovers = [float(item.get("turnover") or 0) for item in bars]
    width, height = 520, 220
    price_top, price_bottom = 14, 138
    volume_top, volume_bottom = 155, 210
    min_price, max_price = min(closes), max(closes)
    max_volume = max(turnovers) if turnovers else 0
    price_range = max(max_price - min_price, 0.01)
    step = width / max(len(closes) - 1, 1)
    points = []
    bars_svg = []
    for index, close in enumerate(closes):
        x = index * step
        y = price_bottom - ((close - min_price) / price_range) * (price_bottom - price_top)
        points.append(f"{x:.1f},{y:.1f}")
        volume = turnovers[index] if index < len(turnovers) else 0
        bar_h = 0 if max_volume <= 0 else (volume / max_volume) * (volume_bottom - volume_top)
        color = "#16a34a" if index == 0 or close >= closes[index - 1] else "#dc2626"
        bars_svg.append(f"<rect x=\"{max(0, x - 2):.1f}\" y=\"{volume_bottom - bar_h:.1f}\" width=\"4\" height=\"{bar_h:.1f}\" fill=\"{color}\" opacity=\"0.55\"/>")
    last = closes[-1]
    first = closes[0]
    change = (last / first - 1) * 100 if first else 0
    return f"""
<svg viewBox="0 0 {width} {height}" role="img" aria-label="日线图和成交量">
<rect width="{width}" height="{height}" fill="#fbfcff" rx="10"/>
<line x1="0" y1="{price_bottom}" x2="{width}" y2="{price_bottom}" stroke="#e5e7eb"/>
<line x1="0" y1="{volume_top}" x2="{width}" y2="{volume_top}" stroke="#e5e7eb"/>
<polyline points="{' '.join(points)}" fill="none" stroke="#2f6fed" stroke-width="2.4"/>
{''.join(bars_svg)}
<text x="10" y="28" fill="#475467" font-size="13">收盘 {last:.2f} / 区间 {change:.1f}%</text>
<text x="10" y="152" fill="#667085" font-size="12">成交量</text>
</svg>
"""


def base_style() -> str:
    return """
<style>
:root{--ink:#172033;--muted:#667085;--line:#d8deea;--blue:#2f6fed;--blue-soft:#eaf1ff;--green:#138a5e;--amber:#a15c07;--red:#b42318}
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Arial,sans-serif;margin:0;background:linear-gradient(135deg,#f7f9ff 0%,#f3fbf7 55%,#fff8ec 100%);color:var(--ink)}
main{max-width:1120px;margin:0 auto;padding:28px}
.top{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin-bottom:18px}
h1{font-size:26px;margin:0} h2{font-size:18px;margin:0 0 14px} p{margin:0 0 8px}
.hint,.status{font-size:13px;color:var(--muted)}
section,.stock-card{background:rgba(255,255,255,.92);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 10px 30px rgba(31,43,70,.07)}
section{margin-bottom:16px}
.actions{display:flex;flex-wrap:wrap;gap:10px;align-items:center}.link-button{display:inline-flex;align-items:center;min-height:38px;border:1px solid #b7cbff;border-radius:10px;background:var(--blue-soft);color:#164ca5;text-decoration:none;padding:9px 13px;font-size:14px}.link-button.active{background:var(--blue);color:#fff}
.chart-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.stock-head{display:flex;justify-content:space-between;gap:12px;margin-bottom:10px}.score{font-weight:700;color:#b42318}.tag{display:inline-block;background:#eaf1ff;color:#164ca5;border-radius:999px;padding:3px 8px;margin:6px 5px 0 0;font-size:12px}
svg{width:100%;height:auto;margin:4px 0 10px}.empty-chart{height:220px;display:grid;place-items:center;background:#f8fafc;border-radius:10px;color:var(--muted)}
@media(max-width:820px){.chart-grid{grid-template-columns:1fr}.top{align-items:flex-start;flex-direction:column}main{padding:16px}}
</style>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local trade-msg web console.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    run_server(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
