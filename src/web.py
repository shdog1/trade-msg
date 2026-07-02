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
from urllib.parse import parse_qs, urlencode, urlparse

import yaml
import pandas as pd

from .config import ROOT, load_settings
from .database import MySQLStore


CONFIG_PATH = ROOT / "config.yaml"
TASK_SCRIPT = ROOT / "scripts" / "install_windows_task.ps1"
SEARCH_PAGE_SIZE = 12
PATTERN_DEFAULT_LIMIT = 12
PATTERN_LIMIT_OPTIONS = (12, 20, 50)
PATTERN_DEFAULT_SORT = "score_desc"
PATTERN_SORT_OPTIONS = (
    ("score_desc", "推荐值 ↓"),
    ("score_asc", "推荐值 ↑"),
    ("price_asc", "股价 ↑"),
    ("price_desc", "股价 ↓"),
    ("turnover_rate_asc", "换手率 ↑"),
    ("turnover_rate_desc", "换手率 ↓"),
    ("volume_asc", "成交量 ↑"),
    ("volume_desc", "成交量 ↓"),
    ("turnover_asc", "成交额 ↑"),
    ("turnover_desc", "成交额 ↓"),
    ("market_cap_asc", "总市值 ↑"),
    ("market_cap_desc", "总市值 ↓"),
)


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
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.respond_html(render_page(load_config()))
            return
        if parsed.path == "/search":
            query = parse_qs(parsed.query)
            self.respond_html(
                render_stock_search_page(
                    form_value(query, "chart_style") or None,
                    form_value(query, "stock_query") or None,
                    form_value(query, "search_page") or None,
                )
            )
            return
        if parsed.path == "/ladder":
            query = parse_qs(parsed.query)
            self.respond_html(render_limit_ladder_page(form_value(query, "ladder_date") or None))
            return
        if parsed.path == "/patterns":
            query = parse_qs(parsed.query)
            self.respond_html(
                render_limit_platform_page(
                    form_value(query, "chart_style") or None,
                    form_value(query, "pattern_query") or None,
                    form_value(query, "pattern_stage") or None,
                    form_value(query, "min_score") or None,
                    form_value(query, "display_count") or None,
                    form_value(query, "pattern_sort") or None,
                )
            )
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
            elif action == "backfill_limit_pool":
                JOB.start("回补连板天梯数据", build_backfill_limit_pool_command(form))
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
    "refresh_patterns": ("更新策略选股", ["--dry-run"]),
    "send": ("发送邮件", ["--send"]),
    "fetch_only": ("完整采集行情", ["--daily-job", "--dry-run"]),
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


def build_backfill_limit_pool_command(form: dict[str, list[str]]) -> list[str]:
    return [
        sys.executable,
        "-u",
        "-m",
        "src.cli",
        "--backfill-limit-pool-days",
        form_value(form, "limit_pool_days") or "90",
        "--limit-pool-sleep",
        form_value(form, "limit_pool_sleep") or "1.0",
    ]


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
    app["report_time"] = form_value(form, "report_time") or app.get("report_time", "18:00")
    app["data_ready_time"] = form_value(form, "data_ready_time") or app.get("data_ready_time", "09:00")
    app["skip_non_trading_day"] = form_value(form, "skip_non_trading_day") == "on"
    market.pop("max_candidates", None)
    market.pop("min_turnover_amount", None)
    config.pop("scoring", None)


def form_value(form: dict[str, list[str]], key: str) -> str:
    return form.get(key, [""])[0].strip()


def render_top_tabs(active_route: str) -> str:
    tabs = (
        ("/", "控制台"),
        ("/search", "搜索日 K"),
        ("/ladder", "连板天梯"),
        ("/patterns", "策略中心"),
    )
    return "".join(
        f'<a class="link-button{" active" if route == active_route else ""}" href="{route}">{label}</a>'
        for route, label in tabs
    )


def render_page(config: dict) -> str:
    app = config.get("app", {})
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>trade-msg 控制台</title>
<style>
:root{{
  --canvas:#f4f1e9;--panel:#fffdf8;--panel-strong:#ffffff;--ink:#14233a;--muted:#687386;
  --line:#d9d7cf;--line-strong:#c6c3b8;--navy:#183b66;--blue:#245f9e;--blue-soft:#e9f1f8;
  --green:#176b52;--green-soft:#e7f3ed;--amber:#9b5b13;--amber-soft:#fbefdc;
  --red:#ae342b;--red-soft:#f8e8e5;--shadow:0 16px 44px rgba(31,42,55,.08);
}}
*{{box-sizing:border-box}}
body{{font-family:'Microsoft YaHei UI','Noto Sans SC','Source Han Sans SC',sans-serif;margin:0;background:radial-gradient(circle at 8% 0%,rgba(36,95,158,.10),transparent 28%),radial-gradient(circle at 92% 10%,rgba(155,91,19,.08),transparent 24%),var(--canvas);color:var(--ink);font-variant-numeric:tabular-nums}}
main{{max-width:1680px;margin:0 auto;padding:22px 28px 40px}}
.top{{position:sticky;top:12px;z-index:20;display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:22px;padding:14px 16px;border:1px solid rgba(198,195,184,.88);border-radius:18px;background:rgba(255,253,248,.88);box-shadow:0 12px 34px rgba(31,42,55,.09);backdrop-filter:blur(16px)}}
.eyebrow{{display:block;margin-bottom:4px;color:var(--blue);font-size:10px;font-weight:800;letter-spacing:.14em}}
h1{{font-size:28px;line-height:1.12;letter-spacing:-.025em;margin:0 0 4px}}h2{{font-size:18px;letter-spacing:-.01em;margin:0 0 16px}}p{{margin:0}}
.hint,.status{{font-size:12px;line-height:1.55;color:var(--muted)}}
.layout{{display:grid;grid-template-columns:minmax(0,2fr) minmax(280px,1fr);gap:16px}}
section{{background:rgba(255,253,248,.94);border:1px solid var(--line);border-radius:18px;padding:22px;box-shadow:var(--shadow)}}
.base-config{{grid-column:1}}.config-actions{{grid-column:2}}.manual-actions,.job-console{{grid-column:1/-1}}
.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}}
label{{display:flex;flex-direction:column;gap:7px;font-size:12px;font-weight:700;color:#4d5a6d;min-width:0}}
input{{width:100%;height:42px;border:1px solid var(--line-strong);border-radius:10px;padding:8px 11px;background:var(--panel-strong);font:inherit;font-size:14px;font-weight:500;color:var(--ink);outline:none}}
input:focus{{border-color:var(--blue);box-shadow:0 0 0 3px rgba(36,95,158,.13)}}
input[type=checkbox]{{width:18px;height:18px;margin:0}}
.check{{display:flex;align-items:center;gap:9px;flex-direction:row;margin-top:14px}}
.actions{{display:flex;flex-wrap:wrap;gap:9px;align-items:center}}.top>.actions{{flex-wrap:nowrap;overflow-x:auto;padding:3px}}
button{{min-height:40px;border:1px solid var(--blue);border-radius:10px;background:var(--blue);color:#fff;cursor:pointer;padding:9px 14px;font:inherit;font-size:13px;font-weight:700;transition:transform .16s ease,box-shadow .16s ease,filter .16s ease}}button:hover{{transform:translateY(-1px);box-shadow:0 7px 18px rgba(31,42,55,.12);filter:saturate(1.08)}}
button.secondary{{background:var(--blue-soft);color:#164ca5;border-color:#b7cbff}}
button.success{{background:var(--green);border-color:var(--green)}}
button.warning{{background:var(--amber);border-color:var(--amber)}}
button.danger{{background:var(--red);border-color:var(--red)}}
.link-button{{display:inline-flex;align-items:center;justify-content:center;min-height:36px;white-space:nowrap;border:1px solid transparent;border-radius:9px;background:transparent;color:#3d4c61;text-decoration:none;padding:8px 11px;font-size:12px;font-weight:700}}.link-button:hover{{background:var(--blue-soft);color:var(--blue)}}
.config-actions>.actions{{align-items:stretch;flex-direction:column}}.config-actions button{{width:100%}}
.job-head{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}}
.pill{{display:inline-flex;align-items:center;gap:6px;white-space:nowrap;border-radius:999px;padding:6px 10px;background:var(--green-soft);color:var(--green);font-size:11px;font-weight:800}}
pre{{white-space:pre-wrap;background:#101b2a;color:#dbe7f3;border:1px solid #25364b;border-radius:13px;padding:16px;min-height:230px;max-height:430px;overflow:auto;margin:0;font-family:'Cascadia Mono','Microsoft YaHei UI',monospace;font-size:12px;line-height:1.6;box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}}
@media(max-width:960px){{.layout{{grid-template-columns:1fr}}.base-config,.config-actions,.manual-actions,.job-console{{grid-column:1}}.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.top{{position:static;align-items:flex-start;flex-direction:column}}.top>.actions{{width:100%}}}}
@media(max-width:560px){{main{{padding:12px}}section{{padding:18px}}.grid{{grid-template-columns:1fr}}.job-head{{align-items:flex-start;flex-direction:column}}h1{{font-size:24px}}}}
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
  <div><span class="eyebrow">TRADE MSG / OPERATIONS</span><h1>trade-msg 控制台</h1><p class="hint">本地配置、执行、日志查看。</p></div>
  <div class="actions">{render_top_tabs('/')}</div>
</div>
<form method="post" class="layout">
<section class="base-config">
<h2>基础配置</h2>
<div class="grid">
<label>自动执行时间<input name="report_time" type="time" value="{html.escape(str(app.get('report_time', '18:00')))}"></label>
<label>数据可用时间<input name="data_ready_time" type="time" value="{html.escape(str(app.get('data_ready_time', '09:00')))}"></label>
</div>
<label class="check"><input name="skip_non_trading_day" type="checkbox" {'checked' if app.get('skip_non_trading_day', True) else ''}> 自动任务跳过非交易日</label>
</section>
<section class="config-actions">
<h2>配置操作</h2>
<div class="actions">
<button class="success" name="action" value="save_config">保存配置</button>
<button class="warning" name="action" value="install_task">保存并安装自动任务</button>
</div>
</section>
<section class="manual-actions">
<h2>手动执行</h2>
<div class="actions">
<button class="secondary" name="action" value="dry_run">生成复盘</button>
<button class="secondary" name="action" value="refresh_patterns">更新策略选股</button>
<button class="secondary" name="action" value="send">发送邮件</button>
<button class="secondary" name="action" value="fetch_only">完整采集行情</button>
<button class="secondary" name="action" value="refresh_calendar">刷新交易日历</button>
<button class="secondary" name="action" value="test_email">测试邮件</button>
</div>
<div class="grid" style="margin-top:14px">
<label>回补天数<input name="backfill_days" type="number" min="1" value="250"></label>
<label>请求间隔秒<input name="backfill_sleep" type="number" min="0" step="0.1" value="1.5"></label>
<label style="grid-column:span 2">指定股票代码（逗号/空格/换行分隔，可空）<input name="backfill_stocks" placeholder="600001, 000001"></label>
</div>
<div class="actions" style="margin-top:12px"><button class="secondary" name="action" value="backfill">回补历史日 K</button></div>
<div class="grid" style="margin-top:14px">
<label>连板回补交易日<input name="limit_pool_days" type="number" min="1" max="250" value="90"></label>
<label>连板请求间隔秒<input name="limit_pool_sleep" type="number" min="0" step="0.1" value="1.0"></label>
</div>
<div class="actions" style="margin-top:12px"><button class="secondary" name="action" value="backfill_limit_pool">回补连板天梯数据</button></div>
</section>
<section class="job-console">
<div class="job-head">
  <h2>执行过程</h2>
  <p class="status">任务：<span id="job-title">无任务</span> | 状态：<span id="job-state">空闲</span></p>
</div>
<div class="actions" style="margin-bottom:12px"><button class="danger" name="action" value="stop">中断当前任务</button></div>
<pre id="job-output"></pre>
</section>
</form>
</main></body></html>"""


def render_limit_ladder_page(ladder_date_text: str | None = None) -> str:
    try:
        payload = load_limit_ladder_page_payload(ladder_date_text)
    except Exception as exc:  # noqa: BLE001
        body = f"<section><h2>读取失败</h2><p>{html.escape(str(exc))}</p></section>"
        return render_shell("连板天梯", body, active_ladder=True, subtitle="查看每日连板高度与最高板演变。")

    selected_date = payload.get("ladder_date")
    latest_date = payload.get("latest_date")
    if not latest_date:
        body = '<section><div class="empty-search"><strong>暂无连板数据</strong><p>请先运行每日数据任务。</p></div></section>'
    else:
        ladder_items = payload["limit_ladder"]
        highest_days = max((int(item.get("max_limit_up_days") or 0) for item in ladder_items), default=0)
        body = f"""
<section class="strategy-hero">
<div><span class="eyebrow">LIMIT-UP LADDER</span><h2>每日连板高度</h2>
<p class="hint">当前查看 {html.escape(str(selected_date))}；日期切换会同步更新榜单和最近十日趋势。</p></div>
<div class="metric-strip"><span><b>{len(ladder_items)}</b>只上榜</span><span><b>{highest_days}</b>板最高</span><span><b>{len(payload['limit_ladder_chart'])}</b>日趋势</span></div>
</section>
<section>
{render_limit_ladder(payload['limit_ladder'], selected_date, latest_date, payload['ladder_date_options'], route='/ladder')}
</section>
<section>
<h2>最高板趋势</h2>
{render_limit_ladder_chart(payload['limit_ladder_chart'])}
</section>
"""
    return render_shell("连板天梯", body, active_ladder=True, subtitle="查看每日连板高度与最高板演变。")


def render_limit_platform_page(
    chart_style: str | None = None,
    pattern_query: str | None = None,
    pattern_stage: str | None = None,
    min_score_text: str | None = None,
    display_count_text: str | None = None,
    pattern_sort_text: str | None = None,
) -> str:
    try:
        payload = load_limit_platform_payload()
    except Exception as exc:  # noqa: BLE001
        body = f"<section><h2>读取失败</h2><p>{html.escape(str(exc))}</p></section>"
        return render_shell("策略中心", body, active_patterns=True, subtitle="集中查看策略候选与日线结构。")

    chart_style = normalize_chart_style(chart_style)
    query = (pattern_query or "").strip()
    stage = (pattern_stage or "").strip()
    min_score = parse_pattern_min_score(min_score_text)
    display_limit = parse_pattern_display_count(display_count_text)
    pattern_sort = normalize_pattern_sort(pattern_sort_text)
    candidates = payload["pattern_candidates"]
    stage_options = pattern_stage_options(candidates)
    filtered_candidates = sort_pattern_candidates(
        filter_pattern_candidates(candidates, query, stage, min_score),
        pattern_sort,
    )
    visible_candidates = visible_pattern_candidates(filtered_candidates, display_limit)
    highest_score = max((int(item.get("score") or 0) for item in filtered_candidates), default=0)
    filter_params = pattern_query_params(query, stage, min_score, display_limit, pattern_sort)
    cards = "\n".join(
        render_candidate_chart(item, payload["bars"].get(item["code"], []), chart_style)
        for item in visible_candidates
    )
    trade_date = payload.get("trade_date")
    if cards:
        cards_html = f'<div class="chart-grid">{cards}</div>'
    elif candidates:
        cards_html = '<div class="empty-search"><strong>没有匹配候选</strong><p>调整筛选后再查看。</p></div>'
    else:
        cards_html = '<div class="empty-search"><strong>暂无策略候选</strong><p>候选会在每日复盘完成后更新。</p></div>'
    table_html = render_pattern_candidate_table(
        visible_candidates,
        payload["bars"],
        chart_style,
        query,
        stage,
        min_score,
        display_limit,
        pattern_sort,
    )
    show_more_html = render_pattern_show_more(
        chart_style,
        filtered_candidates,
        visible_candidates,
        query,
        stage,
        min_score,
        display_limit,
        pattern_sort,
    )
    filtered_count = len(filtered_candidates)
    visible_count = len(visible_candidates)
    total_count = len(candidates)
    body = f"""
<section class="strategy-hero">
<div class="strategy-copy">
  <div><span class="eyebrow">PATTERN WATCH</span><h2>策略候选工作台</h2><p class="hint">候选日期：{html.escape(str(trade_date)) if trade_date else '暂无数据'}。</p></div>
</div>
<div class="hero-rail">
  <div class="section-controls">
    {render_chart_style_select(chart_style, None, route='/patterns', extra_params=filter_params)}
    <form method="post" action="/" class="inline-action-form">
      <button class="search-button" name="action" value="refresh_patterns" type="submit">更新策略选股</button>
    </form>
  </div>
  <div class="metric-strip"><span><b>{total_count}</b>只候选</span><span><b>{filtered_count}</b>只匹配</span><span><b>{highest_score}%</b>最高推荐值</span><span><b>{visible_count}</b>只展示</span></div>
</div>
</section>

<section class="filter-panel">
{render_pattern_filter_form(chart_style, query, stage, min_score, display_limit, stage_options, pattern_sort)}
</section>

<section class="summary-panel">
<div class="section-head">
  <div><h2>候选列表</h2><p class="hint">显示 {visible_count} / {filtered_count} 只。</p></div>
</div>
{table_html or '<div class="empty-search"><strong>没有匹配候选</strong><p>调整筛选后再查看。</p></div>'}
{show_more_html}
</section>

<section class="chart-section">
<div class="section-head">
  <div><h2>候选走势</h2><p class="hint">显示 {visible_count} 只候选的最近 80 根日 K。</p></div>
</div>
{cards_html}
</section>
{render_hover_chart_layer()}
"""
    return render_shell("策略中心", body, active_patterns=True, subtitle="集中查看策略候选与日线结构。")


def render_stock_search_page(
    chart_style: str | None = None,
    stock_query: str | None = None,
    search_page_text: str | None = None,
) -> str:
    try:
        payload = load_stock_search_payload(stock_query, search_page_text)
    except Exception as exc:  # noqa: BLE001
        body = f"<section><h2>读取失败</h2><p>{html.escape(str(exc))}</p></section>"
        return render_shell("搜索日 K", body, active_search=True, subtitle="独立查询沪深股票日线。")

    chart_style = normalize_chart_style(chart_style)
    query = str(payload.get("search_query") or "")
    results = payload.get("search_results") or []
    current_page = int(payload.get("search_page") or 1)
    total = int(payload.get("search_total") or 0)
    cards = "\n".join(
        render_search_result_card(item, payload["search_bars"].get(item["code"], []), chart_style)
        for item in results
    )
    if query and not cards:
        results_html = "<div class=\"empty-search\"><strong>没有找到匹配股票</strong><p>请尝试六位代码或缩短名称关键词。</p></div>"
    elif cards:
        results_html = f'<div class="chart-grid">{cards}</div>'
    else:
        results_html = "<div class=\"empty-search\"><strong>输入代码或名称开始查询</strong><p>支持模糊名称、精确代码和分页浏览；北交所及退市股票不展示。</p></div>"
    trade_date = payload.get("trade_date")
    date_text = html.escape(str(trade_date)) if trade_date else "暂无数据"
    body = f"""
<section class="search-hero">
<div class="section-head">
  <div><span class="eyebrow">DAILY K SEARCH</span><h2>股票日线检索</h2><p class="hint">数据截至 {date_text}，每页展示 {SEARCH_PAGE_SIZE} 只。</p></div>
  {render_chart_style_select(chart_style, None, query, current_page, route='/search')}
</div>
{render_stock_search_form(query, chart_style)}
</section>
<section>
{results_html}
{render_search_pagination(query, current_page, total, chart_style)}
</section>
"""
    return render_shell("搜索日 K", body, active_search=True, subtitle="独立查询沪深股票日线。")


def render_shell(
    title: str,
    body: str,
    active_search: bool = False,
    active_ladder: bool = False,
    active_patterns: bool = False,
    subtitle: str = "最近一次复盘结果。",
) -> str:
    active_route = next(
        (
            route
            for is_active, route in (
                (active_search, "/search"),
                (active_ladder, "/ladder"),
                (active_patterns, "/patterns"),
            )
            if is_active
        ),
        "",
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
{base_style()}
</head>
<body><main>
<div class="top">
  <div><span class="eyebrow">TRADE MSG / MARKET DESK</span><h1>{html.escape(title)}</h1><p class="hint">{html.escape(subtitle)}</p></div>
  <div class="actions">{render_top_tabs(active_route)}</div>
</div>
{body}
</main></body></html>"""


def load_limit_ladder_page_payload(ladder_date_text: str | None = None) -> dict[str, object]:
    settings = load_settings(CONFIG_PATH)
    store = MySQLStore.from_env(settings.raw)
    store.initialize()
    latest_df = store._read_df("SELECT MAX(trade_date) AS trade_date FROM limit_pool", {})
    latest_value = None if latest_df.empty else latest_df.iloc[0].get("trade_date")
    if latest_value is None or pd.isna(latest_value):
        return {
            "latest_date": None,
            "ladder_date": None,
            "ladder_date_options": [],
            "limit_ladder": [],
            "limit_ladder_chart": [],
        }
    latest_date = pd.to_datetime(latest_value).date()
    selected_date = parse_optional_date(ladder_date_text) or latest_date
    return {
        "latest_date": latest_date,
        "ladder_date": selected_date,
        "ladder_date_options": load_ladder_date_options(store, latest_date, selected_date),
        "limit_ladder": load_limit_ladder(store, selected_date),
        "limit_ladder_chart": load_limit_ladder_chart(store, selected_date),
    }


def load_limit_platform_payload() -> dict[str, object]:
    settings = load_settings(CONFIG_PATH)
    store = MySQLStore.from_env(settings.raw)
    store.initialize()
    latest_df = store._read_df(
        "SELECT MAX(trade_date) AS trade_date FROM recap_reports",
        {},
    )
    latest_value = None if latest_df.empty else latest_df.iloc[0].get("trade_date")
    if latest_value is None or pd.isna(latest_value):
        return {"trade_date": None, "pattern_candidates": [], "bars": {}}
    trade_date = pd.to_datetime(latest_value).date()
    pattern_df = store._read_df(
        """
        SELECT pc.code,
               pc.name,
               pc.pattern_type,
               pc.stage,
               pc.score,
               pc.trigger_text,
               pc.invalidation_text,
               pc.reasons,
               mq.close_price,
               mq.volume,
               mq.turnover,
               mq.turnover_rate,
               mq.total_market_cap,
               lur.event_date AS limit_reason_date,
               lur.reason AS limit_reason
        FROM recap_pattern_candidates pc
        LEFT JOIN market_quotes mq ON mq.trade_date = pc.trade_date AND mq.code = pc.code AND mq.source = 'akshare'
        LEFT JOIN limit_up_reasons lur
          ON lur.id = (
              SELECT lur2.id
              FROM limit_up_reasons lur2
              WHERE lur2.code = pc.code
                AND lur2.as_of_date <= pc.trade_date
                AND lur2.source = 'dabanke'
              ORDER BY lur2.as_of_date DESC
              LIMIT 1
          )
        WHERE pc.trade_date = :trade_date AND pc.pattern_type = :pattern_type
        ORDER BY pc.score DESC
        """,
        {"trade_date": trade_date, "pattern_type": "limit_platform_wash"},
    )
    pattern_candidates = [normalize_pattern_candidate_row(row) for row in pattern_df.to_dict("records")]
    chart_candidates = unique_candidates_by_code(pattern_candidates)
    bars = {
        item["code"]: store._read_df(
            "SELECT trade_date, open_price, high_price, low_price, close_price, volume, turnover FROM daily_bars "
            "WHERE code = :code AND trade_date <= :trade_date ORDER BY trade_date DESC LIMIT 80",
            {"code": item["code"], "trade_date": trade_date},
        ).sort_values("trade_date").to_dict("records")
        for item in chart_candidates
    }
    return {
        "trade_date": trade_date,
        "pattern_candidates": pattern_candidates,
        "bars": bars,
    }


def load_stock_search_payload(
    stock_query: str | None = None,
    search_page_text: str | None = None,
) -> dict[str, object]:
    settings = load_settings(CONFIG_PATH)
    store = MySQLStore.from_env(settings.raw)
    store.initialize()
    latest_df = store._read_df("SELECT MAX(trade_date) AS trade_date FROM daily_bars", {})
    latest_value = None if latest_df.empty else latest_df.iloc[0].get("trade_date")
    if latest_value is None or pd.isna(latest_value):
        return empty_stock_search_payload(stock_query)
    trade_date = pd.to_datetime(latest_value).date()
    search_results, search_total, search_page = search_stock_candidates(
        store,
        stock_query,
        trade_date,
        parse_search_page(search_page_text),
    )
    search_bars = {
        item["code"]: store._read_df(
            "SELECT trade_date, open_price, high_price, low_price, close_price, volume, turnover FROM daily_bars "
            "WHERE code = :code AND trade_date <= :trade_date ORDER BY trade_date DESC LIMIT 80",
            {"code": item["code"], "trade_date": trade_date},
        ).sort_values("trade_date").to_dict("records")
        for item in search_results
    }
    return {
        "trade_date": trade_date,
        "search_query": (stock_query or "").strip(),
        "search_results": search_results,
        "search_page": search_page,
        "search_total": search_total,
        "search_bars": search_bars,
    }


def empty_stock_search_payload(stock_query: str | None = None) -> dict[str, object]:
    return {
        "trade_date": None,
        "search_query": (stock_query or "").strip(),
        "search_results": [],
        "search_page": 1,
        "search_total": 0,
        "search_bars": {},
    }


def search_stock_candidates(
    store: MySQLStore,
    stock_query: str | None,
    trade_date: date,
    page: int = 1,
    page_size: int = SEARCH_PAGE_SIZE,
) -> tuple[list[dict[str, object]], int, int]:
    query = (stock_query or "").strip()
    if not query:
        return [], 0, 1
    page_size = max(1, page_size)
    page = max(1, page)
    exact_code = query if query.isdigit() and len(query) == 6 else ""
    if exact_code:
        candidate_df = store._read_df(
            """
            SELECT sb.code, sb.name, MAX(db.trade_date) AS latest_trade_date
            FROM stock_basic sb
            JOIN daily_bars db ON db.code = sb.code AND db.trade_date <= :trade_date
            WHERE sb.code = :code
              AND sb.market <> 'beijing'
              AND COALESCE(sb.listing_status, 'listed') = 'listed'
              AND sb.name NOT LIKE :delisted_name
            GROUP BY sb.code, sb.name
            LIMIT 1
            """,
            {"code": exact_code, "trade_date": trade_date, "delisted_name": "%退%"},
        )
        if candidate_df.empty:
            candidate_df = store._read_df(
                """
                SELECT db.code, COALESCE(MAX(db.name), db.code) AS name,
                       MAX(db.trade_date) AS latest_trade_date
                FROM daily_bars db
                WHERE db.code = :code
                  AND db.code NOT REGEXP '^(43|83|87|92)'
                  AND COALESCE(db.name, '') NOT LIKE :delisted_name
                  AND db.trade_date <= :trade_date
                  AND NOT EXISTS (
                      SELECT 1
                      FROM stock_basic sb
                      WHERE sb.code = db.code
                        AND (
                            COALESCE(sb.listing_status, 'listed') <> 'listed'
                            OR sb.name LIKE :delisted_name
                        )
                  )
                GROUP BY db.code
                LIMIT 1
                """,
                {"code": exact_code, "trade_date": trade_date, "delisted_name": "%退%"},
            )
        total = 0 if candidate_df.empty else 1
        page = 1
    else:
        like_query = f"%{query}%"
        params = {
            "like_query": like_query,
            "prefix_query": f"{query}%",
            "query": query,
            "trade_date": trade_date,
            "delisted_name": "%退%",
        }
        count_df = store._read_df(
            """
            SELECT COUNT(*) AS total
            FROM stock_basic sb
            WHERE sb.name LIKE :like_query
              AND sb.market <> 'beijing'
              AND COALESCE(sb.listing_status, 'listed') = 'listed'
              AND sb.name NOT LIKE :delisted_name
              AND EXISTS (
                  SELECT 1
                  FROM daily_bars db
                  WHERE db.code = sb.code AND db.trade_date <= :trade_date
              )
            """,
            params,
        )
        total = int(count_df.iloc[0].get("total") or 0) if not count_df.empty else 0
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        params |= {"page_size": page_size, "offset": (page - 1) * page_size}
        candidate_df = store._read_df(
            """
            SELECT sb.code, sb.name, MAX(db.trade_date) AS latest_trade_date
            FROM stock_basic sb
            JOIN daily_bars db ON db.code = sb.code AND db.trade_date <= :trade_date
            WHERE sb.name LIKE :like_query
              AND sb.market <> 'beijing'
              AND COALESCE(sb.listing_status, 'listed') = 'listed'
              AND sb.name NOT LIKE :delisted_name
            GROUP BY sb.code, sb.name
            ORDER BY CASE WHEN sb.name = :query THEN 0 ELSE 1 END,
                     CASE WHEN sb.name LIKE :prefix_query THEN 0 ELSE 1 END,
                     sb.code
            LIMIT :page_size OFFSET :offset
            """,
            params,
        )

    if candidate_df.empty:
        return [], total, page

    candidates = candidate_df.to_dict("records")
    results: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in candidates:
        code = str(row.get("code") or "")
        latest_value = row.get("latest_trade_date")
        if not code or code in seen or pd.isna(latest_value):
            continue
        seen.add(code)
        results.append(
            {
                "code": code,
                "name": str(row.get("name") or ""),
                "score": 0,
                "strategy_tags": ["日K搜索"],
                "reasons": [f"最近数据日期 {latest_value}"],
                "trigger_text": "搜索结果，仅供查看走势。",
                "invalidation_text": "不构成交易建议。",
            }
        )
    return results, total, page


def parse_search_page(value: str | None) -> int:
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


def load_limit_ladder(store: MySQLStore, trade_date: date) -> list[dict[str, object]]:
    df = store._read_df(
        """
        SELECT lp.code,
               COALESCE(sb.name, mq.name, lp.code) AS name,
               lp.limit_up_days AS max_limit_up_days,
               lp.trade_date AS reached_at,
               lp.industry,
               lp.reason
        FROM limit_pool lp
        LEFT JOIN stock_basic sb ON sb.code = lp.code
        LEFT JOIN market_quotes mq ON mq.code = lp.code AND mq.trade_date = lp.trade_date
        WHERE lp.trade_date = :trade_date
          AND lp.limit_up_days >= 2
          AND (
              sb.is_main_board = 1
              OR lp.code LIKE '600%%'
              OR lp.code LIKE '601%%'
              OR lp.code LIKE '603%%'
              OR lp.code LIKE '605%%'
              OR lp.code LIKE '000%%'
              OR lp.code LIKE '001%%'
              OR lp.code LIKE '002%%'
              OR lp.code LIKE '003%%'
          )
        ORDER BY lp.limit_up_days DESC, lp.code
        """,
        {"trade_date": trade_date},
    )
    return df.to_dict("records") if not df.empty else []


def load_ladder_date_options(store: MySQLStore, report_date: date, selected_date: date) -> list[date]:
    df = store._read_df(
        """
        SELECT DISTINCT trade_date
        FROM limit_pool
        WHERE trade_date <= :report_date
        ORDER BY trade_date DESC
        LIMIT 60
        """,
        {"report_date": report_date},
    )
    dates = [pd.to_datetime(item).date() for item in df["trade_date"].dropna().tolist()] if not df.empty else []
    for item in [selected_date, report_date]:
        if item not in dates:
            dates.append(item)
    return sorted(dates, reverse=True)


def load_limit_ladder_chart(store: MySQLStore, trade_date: date) -> list[dict[str, object]]:
    date_df = store._read_df(
        "SELECT trade_date FROM trade_calendar WHERE is_open = 1 AND trade_date <= :trade_date "
        "ORDER BY trade_date DESC LIMIT 10",
        {"trade_date": trade_date},
    )
    if date_df.empty:
        date_df = store._read_df(
            "SELECT DISTINCT trade_date FROM limit_pool WHERE trade_date <= :trade_date ORDER BY trade_date DESC LIMIT 10",
            {"trade_date": trade_date},
        )
    if date_df.empty:
        return []
    dates = sorted(date_df["trade_date"].tolist())
    df = store._read_df(
        """
        SELECT lp.trade_date,
               lp.code,
               COALESCE(sb.name, mq.name, lp.code) AS name,
               lp.limit_up_days
        FROM limit_pool lp
        LEFT JOIN stock_basic sb ON sb.code = lp.code
        LEFT JOIN market_quotes mq ON mq.code = lp.code AND mq.trade_date = lp.trade_date
        WHERE lp.trade_date BETWEEN :start_date AND :trade_date
          AND lp.limit_up_days >= 2
          AND (
              sb.is_main_board = 1
              OR lp.code LIKE '600%%'
              OR lp.code LIKE '601%%'
              OR lp.code LIKE '603%%'
              OR lp.code LIKE '605%%'
              OR lp.code LIKE '000%%'
              OR lp.code LIKE '001%%'
              OR lp.code LIKE '002%%'
              OR lp.code LIKE '003%%'
          )
        ORDER BY lp.trade_date, lp.limit_up_days DESC, lp.code
        """,
        {"start_date": dates[0], "trade_date": dates[-1]},
    )
    if df.empty:
        return []
    result: list[dict[str, object]] = []
    for item_date, group in df.groupby("trade_date"):
        max_days = int(group["limit_up_days"].max())
        leaders = group[group["limit_up_days"] == max_days]
        result.append(
            {
                "trade_date": item_date,
                "max_limit_up_days": max_days,
                "names": "、".join(str(item) for item in leaders["name"].tolist()),
                "leaders": [
                    {"code": str(row.code), "name": str(row.name)}
                    for row in leaders.itertuples()
                ],
            }
        )
    return result


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


def normalize_pattern_candidate_row(row: dict[str, object]) -> dict[str, object]:
    stage = str(row.get("stage") or "")
    return {
        "code": str(row.get("code") or ""),
        "name": str(row.get("name") or ""),
        "score": int(row.get("score") or 0),
        "pattern_type": str(row.get("pattern_type") or ""),
        "stage": stage,
        "strategy_tags": ["连板平台洗盘", stage] if stage else ["连板平台洗盘"],
        "reasons": visible_pattern_reasons(parse_json_list(row.get("reasons"))),
        "trigger_text": str(row.get("trigger_text") or ""),
        "invalidation_text": str(row.get("invalidation_text") or ""),
        "close_price": optional_numeric_value(row.get("close_price")),
        "volume": optional_numeric_value(row.get("volume")),
        "turnover": optional_numeric_value(row.get("turnover")),
        "turnover_rate": optional_numeric_value(row.get("turnover_rate")),
        "total_market_cap": optional_numeric_value(row.get("total_market_cap")),
        "limit_reason_date": row.get("limit_reason_date"),
        "limit_reason": optional_text_value(row.get("limit_reason")),
    }


def optional_text_value(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def optional_numeric_value(value: object) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def visible_pattern_reasons(reasons: object) -> list[str]:
    items = reasons if isinstance(reasons, list) else parse_json_list(reasons)
    return [
        reason
        for reason in (str(item).strip() for item in items)
        if reason and not is_hidden_pattern_reason(reason)
    ]


def is_hidden_pattern_reason(reason: str) -> bool:
    compact = reason.replace(" ", "")
    return compact.startswith(("巨量:", "巨量：")) or ("最大换手" in reason and "成交额放大" in reason)


def unique_candidates_by_code(candidates: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[str] = set()
    result: list[dict[str, object]] = []
    for item in candidates:
        code = str(item.get("code") or "")
        if not code or code in seen:
            continue
        seen.add(code)
        result.append(item)
    return result


def parse_optional_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


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


def parse_pattern_min_score(value: str | None) -> int:
    try:
        return min(100, max(0, int(float(value or 0))))
    except (TypeError, ValueError):
        return 0


def parse_pattern_display_count(value: str | None) -> int:
    if value == "all":
        return 0
    try:
        count = int(float(value or PATTERN_DEFAULT_LIMIT))
    except (TypeError, ValueError):
        return PATTERN_DEFAULT_LIMIT
    return min(200, max(1, count))


def normalize_pattern_sort(value: str | None) -> str:
    allowed = {key for key, _ in PATTERN_SORT_OPTIONS}
    return value if value in allowed else PATTERN_DEFAULT_SORT


def pattern_stage_options(candidates: list[dict[str, object]]) -> list[str]:
    stages = {str(item.get("stage") or "").strip() for item in candidates}
    return sorted(stage for stage in stages if stage)


def filter_pattern_candidates(
    candidates: list[dict[str, object]],
    query: str,
    stage: str,
    min_score: int,
) -> list[dict[str, object]]:
    normalized_query = query.strip().lower()
    result: list[dict[str, object]] = []
    for item in candidates:
        code = str(item.get("code") or "")
        name = str(item.get("name") or "")
        item_stage = str(item.get("stage") or "")
        score = int(item.get("score") or 0)
        if normalized_query and normalized_query not in code.lower() and normalized_query not in name.lower():
            continue
        if stage and item_stage != stage:
            continue
        if score < min_score:
            continue
        result.append(item)
    return result


def visible_pattern_candidates(candidates: list[dict[str, object]], display_limit: int) -> list[dict[str, object]]:
    return candidates if display_limit <= 0 else candidates[:display_limit]


def sort_pattern_candidates(candidates: list[dict[str, object]], pattern_sort: str) -> list[dict[str, object]]:
    sort_config = {
        "score_desc": ("score", True),
        "score_asc": ("score", False),
        "price_asc": ("close_price", False),
        "price_desc": ("close_price", True),
        "turnover_rate_asc": ("turnover_rate", False),
        "turnover_rate_desc": ("turnover_rate", True),
        "volume_asc": ("volume", False),
        "volume_desc": ("volume", True),
        "turnover_asc": ("turnover", False),
        "turnover_desc": ("turnover", True),
        "market_cap_asc": ("total_market_cap", False),
        "market_cap_desc": ("total_market_cap", True),
    }
    field, descending = sort_config.get(pattern_sort, sort_config[PATTERN_DEFAULT_SORT])

    def sort_key(item: dict[str, object]) -> tuple[bool, float, str]:
        value = optional_numeric_value(item.get(field))
        numeric = value if value is not None else 0.0
        return value is None, -numeric if descending else numeric, str(item.get("code") or "")

    return sorted(candidates, key=sort_key)


def pattern_query_params(
    query: str,
    stage: str,
    min_score: int,
    display_limit: int,
    pattern_sort: str = PATTERN_DEFAULT_SORT,
) -> dict[str, object]:
    params: dict[str, object] = {}
    if query:
        params["pattern_query"] = query
    if stage:
        params["pattern_stage"] = stage
    if min_score:
        params["min_score"] = min_score
    if display_limit != PATTERN_DEFAULT_LIMIT:
        params["display_count"] = "all" if display_limit <= 0 else display_limit
    if pattern_sort != PATTERN_DEFAULT_SORT:
        params["pattern_sort"] = pattern_sort
    return params


def pattern_filter_url(
    chart_style: str,
    query: str,
    stage: str,
    min_score: int,
    display_limit: int,
    pattern_sort: str = PATTERN_DEFAULT_SORT,
) -> str:
    params = {"chart_style": chart_style} | pattern_query_params(query, stage, min_score, display_limit, pattern_sort)
    return "/patterns?" + urlencode(params)


def pattern_candidate_anchor(candidate: dict[str, object]) -> str:
    code = "".join(ch for ch in str(candidate.get("code") or "") if ch.isalnum())
    return f"pattern-{code or 'candidate'}"


def render_pattern_filter_form(
    chart_style: str,
    query: str,
    stage: str,
    min_score: int,
    display_limit: int,
    stage_options: list[str],
    pattern_sort: str,
) -> str:
    stage_options_html = ['<option value="">全部阶段</option>']
    for option in stage_options:
        selected = " selected" if option == stage else ""
        stage_options_html.append(f'<option value="{html.escape(option)}"{selected}>{html.escape(option)}</option>')
    limit_options = "".join(
        f'<option value="{count}"{" selected" if display_limit == count else ""}>前 {count} 只</option>'
        for count in PATTERN_LIMIT_OPTIONS
    )
    all_selected = " selected" if display_limit <= 0 else ""
    sort_input = (
        f'<input type="hidden" name="pattern_sort" value="{html.escape(pattern_sort)}">'
        if pattern_sort != PATTERN_DEFAULT_SORT
        else ""
    )
    return f"""
<form method="get" action="/patterns" class="pattern-filter-form">
  <input type="hidden" name="chart_style" value="{html.escape(chart_style)}">
  {sort_input}
  <input class="filter-input" name="pattern_query" value="{html.escape(query)}" placeholder="代码 / 名称" aria-label="筛选代码或名称">
  <select class="filter-select" name="pattern_stage" aria-label="筛选阶段">
    {''.join(stage_options_html)}
  </select>
  <label class="compact-field">最低分<input name="min_score" type="number" min="0" max="100" value="{min_score}"></label>
  <select class="filter-select" name="display_count" aria-label="显示数量">
    {limit_options}
    <option value="all"{all_selected}>全部</option>
  </select>
  <button class="search-button" type="submit">筛选</button>
  <a class="page-link reset-link" href="/patterns?chart_style={html.escape(chart_style)}">重置</a>
</form>
"""


def render_pattern_show_more(
    chart_style: str,
    filtered_candidates: list[dict[str, object]],
    visible_candidates: list[dict[str, object]],
    query: str,
    stage: str,
    min_score: int,
    display_limit: int,
    pattern_sort: str,
) -> str:
    if display_limit <= 0 and len(filtered_candidates) > PATTERN_DEFAULT_LIMIT:
        href = pattern_filter_url(
            chart_style,
            query,
            stage,
            min_score,
            PATTERN_DEFAULT_LIMIT,
            pattern_sort,
        )
        return (
            f'<div class="table-footer"><a class="page-link" href="{html.escape(href)}">'
            f"收起至 {PATTERN_DEFAULT_LIMIT} 只</a></div>"
        )
    hidden_count = len(filtered_candidates) - len(visible_candidates)
    if hidden_count <= 0:
        return ""
    href = pattern_filter_url(chart_style, query, stage, min_score, 0, pattern_sort)
    return f'<div class="table-footer"><a class="page-link" href="{html.escape(href)}">展开全部 {len(filtered_candidates)} 只</a></div>'


def render_pattern_candidate_table(
    candidates: list[dict[str, object]],
    bars_by_code: dict[str, list[dict[str, object]]] | None = None,
    chart_style: str = "candle",
    query: str = "",
    stage: str = "",
    min_score: int = 0,
    display_limit: int = PATTERN_DEFAULT_LIMIT,
    pattern_sort: str = PATTERN_DEFAULT_SORT,
) -> str:
    if not candidates:
        return ""
    rows = []
    for item in candidates:
        anchor = pattern_candidate_anchor(item)
        code = str(item.get("code") or "")
        name = str(item.get("name") or "")
        reasons_text = "; ".join(visible_pattern_reasons(item.get("reasons", [])))
        reasons = html.escape(reasons_text)
        template_id = f"hover-chart-{anchor}"
        bars = (bars_by_code or {}).get(code, [])
        hover_template = (
            f'<template id="{html.escape(template_id)}">'
            f'<div class="hover-chart-title">{html.escape(code)} {html.escape(name)}</div>'
            f'{render_price_volume_svg(bars, chart_style)}'
            f'</template>'
        )
        reason_control = (
            f"<details class=\"reason-details\"><summary>"
            f"<span class=\"reason-preview\">{reasons}</span>"
            f"<span class=\"reason-toggle\"><span class=\"open-text\">展开</span><span class=\"close-text\">收起</span></span>"
            f"</summary>"
            f"<div class=\"reason-detail-text\">{reasons}</div></details>"
            if reasons
            else ""
        )
        code_link = (
            f'<a class="stock-hover-trigger" href="#{html.escape(anchor)}" '
            f'data-chart-template="{html.escape(template_id)}">{html.escape(code)}</a>'
            f"{hover_template}"
        )
        name_link = (
            f'<a class="stock-hover-trigger" href="#{html.escape(anchor)}" '
            f'data-chart-template="{html.escape(template_id)}">{html.escape(name)}</a>'
        )
        rows.append(
            "<tr>"
            f"<td>{code_link}</td>"
            f"<td>{name_link}</td>"
            f"<td>{html.escape(str(item.get('stage') or ''))}</td>"
            f"<td><span class=\"score\">{int(item.get('score') or 0)}%</span></td>"
            f"<td>{format_price(item.get('close_price'))}</td>"
            f"<td>{format_percent(item.get('turnover_rate'))}</td>"
            f"<td>{format_volume(item.get('volume'))}</td>"
            f"<td>{format_turnover(item.get('turnover'))}</td>"
            f"<td>{format_market_cap(item.get('total_market_cap'))}</td>"
            f"<td class=\"reason-cell\">{reason_control}</td>"
            "</tr>"
        )
    sortable_headers = "".join(
        [
            "<th>代码</th><th>名称</th><th>阶段</th>",
            render_pattern_sort_header(
                "推荐值", "score_asc", "score_desc", pattern_sort, chart_style, query, stage, min_score, display_limit
            ),
            render_pattern_sort_header(
                "股价", "price_asc", "price_desc", pattern_sort, chart_style, query, stage, min_score, display_limit
            ),
            render_pattern_sort_header(
                "换手率",
                "turnover_rate_asc",
                "turnover_rate_desc",
                pattern_sort,
                chart_style,
                query,
                stage,
                min_score,
                display_limit,
            ),
            render_pattern_sort_header(
                "成交量", "volume_asc", "volume_desc", pattern_sort, chart_style, query, stage, min_score, display_limit
            ),
            render_pattern_sort_header(
                "成交额",
                "turnover_asc",
                "turnover_desc",
                pattern_sort,
                chart_style,
                query,
                stage,
                min_score,
                display_limit,
            ),
            render_pattern_sort_header(
                "总市值",
                "market_cap_asc",
                "market_cap_desc",
                pattern_sort,
                chart_style,
                query,
                stage,
                min_score,
                display_limit,
            ),
            "<th>核心依据</th>",
        ]
    )
    return (
        f"<div class=\"table-scroll\"><table class=\"pattern-table compact-pattern-table\"><tr>{sortable_headers}</tr>"
        + "".join(rows)
        + "</table></div>"
    )


def render_pattern_sort_header(
    label: str,
    ascending_key: str,
    descending_key: str,
    current_sort: str,
    chart_style: str,
    query: str,
    stage: str,
    min_score: int,
    display_limit: int,
) -> str:
    if current_sort == descending_key:
        direction = "descending"
        arrow = "↓"
        next_sort = ascending_key
    elif current_sort == ascending_key:
        direction = "ascending"
        arrow = "↑"
        next_sort = descending_key
    else:
        direction = "none"
        arrow = ""
        next_sort = descending_key
    href = pattern_filter_url(chart_style, query, stage, min_score, display_limit, next_sort)
    active_class = " active" if direction != "none" else ""
    arrow_html = f'<span class="sort-arrow" aria-hidden="true">{arrow}</span>' if arrow else ""
    return (
        f'<th aria-sort="{direction}"><a class="table-sort-link{active_class}" '
        f'href="{html.escape(href)}">{html.escape(label)}{arrow_html}</a></th>'
    )


def format_price(value: object) -> str:
    number = optional_numeric_value(value)
    return "--" if number is None or number <= 0 else f"{number:.2f}"


def format_percent(value: object) -> str:
    number = optional_numeric_value(value)
    return "--" if number is None else f"{number:.1f}%"


def format_market_cap(value: object) -> str:
    number = optional_numeric_value(value)
    if number is None or number <= 0:
        return "--"
    if number >= 100_000_000:
        return f"{number / 100_000_000:.1f}亿"
    if number >= 10_000:
        return f"{number / 10_000:.0f}万"
    return f"{number:.0f}"


def format_volume(value: object) -> str:
    number = optional_numeric_value(value)
    if number is None or number <= 0:
        return "--"
    return f"{number / 10_000:.1f}万"


def format_turnover(value: object) -> str:
    number = optional_numeric_value(value)
    if number is None or number <= 0:
        return "--"
    if number >= 100_000_000:
        return f"{number / 100_000_000:.1f}亿"
    return f"{number / 10_000:.1f}万"


def render_hover_chart_layer() -> str:
    return """
<div id="hover-chart-panel" class="hover-chart-panel" hidden></div>
<script>
(() => {
  const panel = document.getElementById('hover-chart-panel');
  if (!panel) return;
  const hidePanel = () => {
    panel.hidden = true;
    panel.innerHTML = '';
  };
  const showPanel = (trigger) => {
    const templateId = trigger.getAttribute('data-chart-template');
    const template = templateId ? document.getElementById(templateId) : null;
    if (!template) return;
    panel.innerHTML = template.innerHTML;
    panel.hidden = false;
    panel.style.visibility = 'hidden';
    const rect = trigger.getBoundingClientRect();
    const panelWidth = Math.min(440, window.innerWidth - 24);
    panel.style.width = panelWidth + 'px';
    const panelHeight = panel.offsetHeight;
    let left = rect.right + 12;
    if (left + panelWidth > window.innerWidth - 12) {
      left = Math.max(12, window.innerWidth - panelWidth - 12);
    }
    let top = rect.top - 18;
    if (top + panelHeight > window.innerHeight - 12) {
      top = Math.max(12, window.innerHeight - panelHeight - 12);
    }
    panel.style.left = left + 'px';
    panel.style.top = top + 'px';
    panel.style.visibility = 'visible';
  };
  document.querySelectorAll('.stock-hover-trigger').forEach((trigger) => {
    trigger.addEventListener('mouseenter', () => showPanel(trigger));
    trigger.addEventListener('focus', () => showPanel(trigger));
    trigger.addEventListener('mouseleave', hidePanel);
    trigger.addEventListener('blur', hidePanel);
  });
  window.addEventListener('scroll', hidePanel, { passive: true });
})();
</script>
"""


def render_candidate_chart(candidate: dict[str, object], bars: list[dict[str, object]], chart_style: str = "candle") -> str:
    tags = "".join(f"<span class=\"tag\">{html.escape(tag)}</span>" for tag in candidate["strategy_tags"])
    limit_reason = str(candidate.get("limit_reason") or "暂无公开原因")
    limit_reason_date = candidate.get("limit_reason_date")
    limit_reason_label = f"涨停原因（{limit_reason_date}）" if limit_reason_date else "涨停原因"
    return f"""
<article class="stock-card" id="{html.escape(pattern_candidate_anchor(candidate))}">
<div class="stock-head">
  <div><strong>{html.escape(str(candidate['code']))} {html.escape(str(candidate['name']))}</strong><div>{tags}</div></div>
  <span class="score">{candidate['score']}%</span>
</div>
{render_price_volume_svg(bars, chart_style)}
<div class="limit-reason-wrap" tabindex="0">
  <p class="limit-reason-note"><b>{html.escape(limit_reason_label)}：</b>{html.escape(limit_reason)}</p>
  <div class="limit-reason-tooltip" role="tooltip"><b>{html.escape(limit_reason_label)}：</b>{html.escape(limit_reason)}</div>
</div>
<p class="hint">入场：{html.escape(str(candidate['trigger_text']))}</p>
<p class="hint">失效：{html.escape(str(candidate['invalidation_text']))}</p>
</article>
"""


def normalize_chart_style(value: str | None) -> str:
    return "line" if value == "line" else "candle"


def render_chart_style_select(
    chart_style: str,
    ladder_date: date | object | None,
    stock_query: str | None = None,
    search_page: int = 1,
    route: str = "/patterns",
    extra_params: dict[str, object] | None = None,
) -> str:
    query_params: dict[str, object] = {}
    if ladder_date:
        query_params["ladder_date"] = ladder_date
    if stock_query:
        query_params["stock_query"] = stock_query
    if search_page > 1:
        query_params["search_page"] = search_page
    if extra_params:
        query_params.update(extra_params)
    query_suffix = f"&{html.escape(urlencode(query_params))}" if query_params else ""
    candle_selected = " selected" if chart_style == "candle" else ""
    line_selected = " selected" if chart_style == "line" else ""
    return f"""
<select class="chart-style-select" onchange="location.href='{html.escape(route)}?chart_style='+this.value+'{query_suffix}'" aria-label="选择K线样式">
  <option value="candle"{candle_selected}>蜡烛K线</option>
  <option value="line"{line_selected}>简化折线</option>
</select>
"""


def render_stock_search_form(stock_query: str, chart_style: str) -> str:
    return f"""
<form method="get" action="/search" class="search-form">
  <input type="hidden" name="chart_style" value="{html.escape(chart_style)}">
  <input class="search-input" name="stock_query" value="{html.escape(stock_query)}" placeholder="输入股票代码或名称，如 603011 / 美邦股份" aria-label="搜索股票代码或名称">
  <button class="search-button" type="submit">搜索日 K</button>
</form>
"""


def render_search_result_card(candidate: dict[str, object], bars: list[dict[str, object]], chart_style: str = "candle") -> str:
    latest_text = candidate["reasons"][0] if candidate.get("reasons") else ""
    return f"""
<article class="stock-card">
<div class="stock-head">
  <div><strong>{html.escape(str(candidate['code']))} {html.escape(str(candidate['name']))}</strong><div><span class="tag">日K搜索</span></div></div>
</div>
{render_price_volume_svg(bars, chart_style)}
<p class="hint">{html.escape(str(latest_text))}</p>
</article>
"""


def render_search_pagination(
    stock_query: str,
    current_page: int,
    total: int,
    chart_style: str,
    page_size: int = SEARCH_PAGE_SIZE,
) -> str:
    if not stock_query or total <= 0:
        return ""
    total_pages = max(1, (total + page_size - 1) // page_size)
    current_page = min(max(1, current_page), total_pages)
    summary = f'<span class="search-total">共 {total} 只 · 第 {current_page}/{total_pages} 页</span>'
    if total_pages == 1:
        return f'<div class="search-pagination">{summary}</div>'

    def page_href(page: int) -> str:
        params: dict[str, object] = {
            "stock_query": stock_query,
            "search_page": page,
            "chart_style": chart_style,
        }
        return f"/search?{html.escape(urlencode(params))}"

    controls: list[str] = []
    if current_page > 1:
        controls.append(f'<a class="page-link" href="{page_href(current_page - 1)}">上一页</a>')
    else:
        controls.append('<span class="page-link disabled">上一页</span>')
    previous_page = 0
    for page in pagination_pages(current_page, total_pages):
        if previous_page and page - previous_page > 1:
            controls.append('<span class="page-ellipsis">…</span>')
        if page == current_page:
            controls.append(f'<span class="page-link current" aria-current="page">{page}</span>')
        else:
            controls.append(f'<a class="page-link" href="{page_href(page)}">{page}</a>')
        previous_page = page
    if current_page < total_pages:
        controls.append(f'<a class="page-link" href="{page_href(current_page + 1)}">下一页</a>')
    else:
        controls.append('<span class="page-link disabled">下一页</span>')
    return f'<nav class="search-pagination" aria-label="股票搜索分页">{summary}{"".join(controls)}</nav>'


def pagination_pages(current_page: int, total_pages: int) -> list[int]:
    if total_pages <= 7:
        return list(range(1, total_pages + 1))
    return sorted({1, total_pages, *range(max(2, current_page - 2), min(total_pages, current_page + 2) + 1)})


def render_limit_ladder(
    items: list[dict[str, object]],
    ladder_date: date | object | None = None,
    report_date: date | object | None = None,
    date_options: list[date] | None = None,
    chart_style: str | None = None,
    stock_query: str | None = None,
    search_page: int = 1,
    route: str = "/ladder",
) -> str:
    table_head = render_limit_ladder_head(
        ladder_date,
        report_date,
        date_options or [],
        chart_style,
        stock_query,
        search_page,
        route,
    )
    if not items:
        return f"<div class=\"table-scroll\"><table class=\"ladder-table\">{table_head}</table></div><div class=\"empty-chart\">暂无 2 连板以上历史数据</div>"
    rows = render_limit_ladder_rows(items)
    table = f"<div class=\"table-scroll\"><table class=\"ladder-table\">{table_head}{rows}</table></div>"
    if len(items) <= 10:
        return table
    return (
        "<div class=\"ladder-collapse\">"
        "<input id=\"ladder-toggle\" type=\"checkbox\">"
        + table
        + f"<label for=\"ladder-toggle\"><span class=\"open-text\">展开全部 {len(items)} 只</span><span class=\"close-text\">收起</span></label>"
        + "</div>"
    )


def render_limit_ladder_head(
    ladder_date: date | object | None,
    report_date: date | object | None,
    date_options: list[date],
    chart_style: str | None = None,
    stock_query: str | None = None,
    search_page: int = 1,
    route: str = "/ladder",
) -> str:
    return (
        "<tr><th>连板</th><th>代码</th><th>名称</th><th>板块</th><th>涨停原因</th>"
        f"<th>{render_ladder_date_select(ladder_date, report_date, date_options, chart_style, stock_query, search_page, route)}</th></tr>"
    )


def render_ladder_date_select(
    ladder_date: date | object | None,
    report_date: date | object | None,
    date_options: list[date],
    chart_style: str | None = None,
    stock_query: str | None = None,
    search_page: int = 1,
    route: str = "/ladder",
) -> str:
    selected = str(ladder_date or "")
    if not date_options and selected:
        date_options = [date.fromisoformat(selected)]
    options = []
    for item in date_options:
        value = item.isoformat()
        selected_attr = " selected" if value == selected else ""
        classes = ["trade-option"]
        if value == str(report_date):
            classes.append("report-day")
        if value == selected:
            classes.append("selected-day")
        options.append(
            f"<option class=\"{' '.join(classes)}\" value=\"{value}\"{selected_attr}>{value}</option>"
        )
    query_params: dict[str, object] = {}
    if chart_style:
        query_params["chart_style"] = chart_style
    if stock_query:
        query_params["stock_query"] = stock_query
    if search_page > 1:
        query_params["search_page"] = search_page
    query_suffix = f"&{html.escape(urlencode(query_params))}" if query_params else ""
    return (
        "<select class=\"ladder-date-select\" "
        f"onchange=\"location.href='{html.escape(route)}?ladder_date='+this.value+'{query_suffix}'\" "
        f"aria-label=\"筛选连板日期\">{''.join(options)}</select>"
    )


def render_limit_ladder_rows(items: list[dict[str, object]]) -> str:
    rows = []
    for index, item in enumerate(items):
        days = int(item.get("max_limit_up_days") or 0)
        color = limit_color(days)
        row_class = " class=\"extra-row\"" if index >= 10 else ""
        industry = str(item.get("industry") or "")
        rows.append(
            f"<tr{row_class}>"
            f"<td><span class=\"ladder-badge\" style=\"background:{color};color:{contrast_color(days)}\">{days}板</span></td>"
            f"<td>{html.escape(str(item.get('code') or ''))}</td>"
            f"<td>{html.escape(str(item.get('name') or ''))}</td>"
            f"<td>{html.escape(industry or '-')}</td>"
            f"<td class=\"reason-cell\">{html.escape(limit_reason_text(item))}</td>"
            f"<td>{html.escape(str(item.get('reached_at') or ''))}</td>"
            "</tr>"
        )
    return "".join(rows)


def limit_reason_text(item: dict[str, object]) -> str:
    reason = str(item.get("reason") or "").strip()
    return reason


def render_limit_ladder_chart(items: list[dict[str, object]]) -> str:
    if len(items) < 2:
        return "<div class=\"empty-chart\">连板天梯图数据不足</div>"
    leader_groups = [chart_leaders(item) for item in items]
    max_leader_count = max((len(leaders) for leaders in leader_groups), default=1)
    width = 1120
    left, right, bottom = 140, 140, 58
    top = max(78, 30 + (max_leader_count - 1) * 16)
    height = max(420, top + 284 + bottom)
    max_days = max(int(item.get("max_limit_up_days") or 0) for item in items)
    max_days = max(2, max_days)
    min_days = 2
    plot_w = width - left - right
    plot_h = height - top - bottom
    x_step = plot_w / max(len(items) - 1, 1)

    def x_at(index: int) -> float:
        return left + index * x_step

    def y_at(days: int) -> float:
        return top + (max_days - days) / max(max_days - min_days, 1) * plot_h

    points = []
    labels = []
    for index, item in enumerate(items):
        days = int(item.get("max_limit_up_days") or 0)
        x = x_at(index)
        y = y_at(days)
        points.append(f"{x:.1f},{y:.1f}")
        color = limit_color(days)
        leaders = leader_groups[index]
        label_svg = []
        start_y = max(16, y - 14 - (len(leaders) - 1) * 16)
        for label_index, leader in enumerate(leaders):
            text = truncate_label(chart_label_text(leader), 6)
            label_y = start_y + label_index * 16
            fill = limit_text_color(days, label_index)
            label_svg.append(
                f"<text x=\"{x:.1f}\" y=\"{label_y:.1f}\" font-size=\"9\" "
                f"font-weight=\"600\" fill=\"{fill}\" text-anchor=\"middle\">{html.escape(text)}</text>"
            )
        labels.append(
            f"<circle cx=\"{x:.1f}\" cy=\"{y:.1f}\" r=\"5\" fill=\"{color}\"/>"
            + "".join(label_svg)
        )

    grid = []
    for days in range(min_days, max_days + 1):
        y = y_at(days)
        grid.append(
            f"<line x1=\"{left}\" y1=\"{y:.1f}\" x2=\"{width - right}\" y2=\"{y:.1f}\" stroke=\"#e5e7eb\"/>"
            f"<text x=\"12\" y=\"{y + 4:.1f}\" font-size=\"12\" fill=\"#667085\">{days}板</text>"
        )
    date_labels = []
    label_step = max(1, len(items) // 6)
    for index, item in enumerate(items):
        if index % label_step != 0 and index != len(items) - 1:
            continue
        x = x_at(index)
        date_labels.append(
            f"<text x=\"{x:.1f}\" y=\"{height - 20}\" font-size=\"11\" fill=\"#667085\" text-anchor=\"middle\">{html.escape(str(item.get('trade_date'))[5:])}</text>"
        )
    return f"""
<div class="ladder-chart-wrap">
<svg viewBox="0 0 {width} {height}" role="img" aria-label="10日连板天梯图">
<rect width="{width}" height="{height}" fill="#fbfcff" rx="12"/>
{''.join(grid)}
<polyline points="{' '.join(points)}" fill="none" stroke="#dc2626" stroke-width="2.4"/>
{''.join(labels)}
{''.join(date_labels)}
</svg>
</div>
"""


def limit_color(days: int) -> str:
    palette = {
        2: "#fee2e2",
        3: "#fecaca",
        4: "#fca5a5",
        5: "#f87171",
        6: "#ef4444",
        7: "#dc2626",
        8: "#b91c1c",
        9: "#991b1b",
        10: "#7f1d1d",
    }
    return palette.get(min(max(days, 2), 10), "#7f1d1d")


def limit_text_color(days: int, offset: int) -> str:
    palettes = {
        2: ["#ef4444", "#dc2626", "#b91c1c"],
        3: ["#dc2626", "#b91c1c", "#991b1b"],
        4: ["#b91c1c", "#991b1b", "#7f1d1d"],
        5: ["#dc2626", "#b91c1c", "#991b1b"],
        6: ["#b91c1c", "#991b1b", "#7f1d1d"],
        7: ["#991b1b", "#7f1d1d", "#651515"],
        8: ["#7f1d1d", "#651515", "#450a0a"],
        9: ["#651515", "#450a0a", "#7f1d1d"],
        10: ["#450a0a", "#651515", "#7f1d1d"],
    }
    values = palettes.get(min(max(days, 2), 10), palettes[10])
    return values[offset % len(values)]


def contrast_color(days: int) -> str:
    return "#172033" if days <= 4 else "#ffffff"


def chart_leaders(item: dict[str, object]) -> list[dict[str, str]]:
    leaders = item.get("leaders")
    if isinstance(leaders, list) and leaders:
        return [
            {"code": str(leader.get("code") or ""), "name": str(leader.get("name") or "")}
            for leader in leaders
            if isinstance(leader, dict)
        ]
    names = [part.strip() for part in str(item.get("names") or "").split("、") if part.strip()]
    return [{"code": "", "name": name} for name in names] or [{"code": "", "name": ""}]


def chart_label_text(leader: dict[str, str]) -> str:
    return leader.get("name", "")


def truncate_label(value: str, max_chars: int) -> str:
    return value if len(value) <= max_chars else value[:max_chars] + "…"


def render_price_volume_svg(bars: list[dict[str, object]], chart_style: str = "candle") -> str:
    if len(bars) < 2:
        return "<div class=\"empty-chart\">日线数据不足</div>"
    if chart_style == "candle":
        return render_candle_volume_svg(bars)
    closes = [float(item.get("close_price") or 0) for item in bars]
    volumes = chart_volume_series(bars)
    width, height = 520, 220
    price_top, price_bottom = 14, 138
    volume_top, volume_bottom = 155, 210
    min_price, max_price = min(closes), max(closes)
    volume_scale = chart_volume_scale(volumes)
    peak_volume = max(volumes, default=0)
    price_range = max(max_price - min_price, 0.01)
    step = width / max(max(len(closes), 80) - 1, 1)
    points = []
    bars_svg = []
    for index, close in enumerate(closes):
        x = index * step
        y = price_bottom - ((close - min_price) / price_range) * (price_bottom - price_top)
        points.append(f"{x:.1f},{y:.1f}")
        volume = volumes[index] if index < len(volumes) else 0
        bar_h = chart_volume_height(volume, volume_scale, volume_bottom - volume_top)
        up = index == 0 or close >= closes[index - 1]
        color = volume_bar_color(up, volume > 0 and volume == peak_volume)
        opacity = "0.95" if volume > 0 and volume == peak_volume else "0.55"
        bars_svg.append(f"<rect x=\"{max(0, x - 2):.1f}\" y=\"{volume_bottom - bar_h:.1f}\" width=\"4\" height=\"{bar_h:.1f}\" fill=\"{color}\" opacity=\"{opacity}\"/>")
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


def render_candle_volume_svg(bars: list[dict[str, object]]) -> str:
    volumes = chart_volume_series(bars)
    ohlc = []
    previous_close = 0.0
    for index, item in enumerate(bars):
        close = numeric_value(item.get("close_price"))
        open_price = numeric_value(item.get("open_price"))
        if open_price <= 0:
            open_price = previous_close if previous_close > 0 else close
        high = numeric_value(item.get("high_price"))
        low = numeric_value(item.get("low_price"))
        if high <= 0:
            high = max(open_price, close)
        if low <= 0:
            low = min(open_price, close)
        ohlc.append(
            {
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volumes[index] if index < len(volumes) else 0,
            }
        )
        if close > 0:
            previous_close = close
    width, height = 520, 220
    price_top, price_bottom = 14, 138
    volume_top, volume_bottom = 155, 210
    min_price = min(item["low"] for item in ohlc)
    max_price = max(item["high"] for item in ohlc)
    volume_scale = chart_volume_scale([item["volume"] for item in ohlc])
    peak_volume = max((item["volume"] for item in ohlc), default=0)
    price_range = max(max_price - min_price, 0.01)
    step = width / max(len(ohlc), 80)
    candle_w = max(3, min(8, step * 0.58))
    candles = []
    volumes = []

    def y_at(price: float) -> float:
        return price_bottom - ((price - min_price) / price_range) * (price_bottom - price_top)

    for index, item in enumerate(ohlc):
        x = index * step + step / 2
        up = item["close"] >= item["open"]
        color = price_color(up)
        high_y = y_at(item["high"])
        low_y = y_at(item["low"])
        open_y = y_at(item["open"])
        close_y = y_at(item["close"])
        body_y = min(open_y, close_y)
        body_h = max(abs(close_y - open_y), 1.5)
        volume_h = chart_volume_height(item["volume"], volume_scale, volume_bottom - volume_top)
        volume_color = volume_bar_color(up, item["volume"] > 0 and item["volume"] == peak_volume)
        volume_opacity = "0.95" if item["volume"] > 0 and item["volume"] == peak_volume else "0.45"
        candles.append(
            f"<line x1=\"{x:.1f}\" y1=\"{high_y:.1f}\" x2=\"{x:.1f}\" y2=\"{low_y:.1f}\" stroke=\"{color}\" stroke-width=\"1.2\"/>"
            f"<rect x=\"{x - candle_w / 2:.1f}\" y=\"{body_y:.1f}\" width=\"{candle_w:.1f}\" height=\"{body_h:.1f}\" fill=\"{color}\" opacity=\"0.78\"/>"
        )
        volumes.append(
            f"<rect x=\"{x - candle_w / 2:.1f}\" y=\"{volume_bottom - volume_h:.1f}\" width=\"{candle_w:.1f}\" height=\"{volume_h:.1f}\" fill=\"{volume_color}\" opacity=\"{volume_opacity}\"/>"
        )
    last = ohlc[-1]["close"]
    first = ohlc[0]["close"]
    change = (last / first - 1) * 100 if first else 0
    return f"""
<svg viewBox="0 0 {width} {height}" role="img" aria-label="蜡烛K线图和成交量">
<rect width="{width}" height="{height}" fill="#fbfcff" rx="10"/>
<line x1="0" y1="{price_bottom}" x2="{width}" y2="{price_bottom}" stroke="#e5e7eb"/>
<line x1="0" y1="{volume_top}" x2="{width}" y2="{volume_top}" stroke="#e5e7eb"/>
{''.join(candles)}
{''.join(volumes)}
<text x="10" y="28" fill="#475467" font-size="13">收盘 {last:.2f} / 区间 {change:.1f}%</text>
<text x="10" y="152" fill="#667085" font-size="12">成交量</text>
</svg>
"""


def chart_volume_series(bars: list[dict[str, object]]) -> list[float]:
    volume_values = [normalized_explicit_volume(item) for item in bars]
    turnover_values = [numeric_value(item.get("turnover")) for item in bars]
    if positive_count(volume_values) == 0:
        return turnover_values
    fallback_multiplier = legacy_turnover_volume_multiplier(volume_values, turnover_values)
    return [
        volume if volume > 0 else turnover * fallback_multiplier
        for volume, turnover in zip(volume_values, turnover_values)
    ]


def normalized_explicit_volume(item: dict[str, object]) -> float:
    volume = numeric_value(item.get("volume"))
    if volume <= 0:
        return 0.0
    turnover = numeric_value(item.get("turnover"))
    close = numeric_value(item.get("close_price"))
    if turnover > 0 and close > 0:
        implied_unit = turnover / (close * volume)
        if 20 <= implied_unit <= 200:
            return volume * 100
    return volume


def legacy_turnover_volume_multiplier(volume_values: list[float], turnover_values: list[float]) -> float:
    explicit = median_positive(volume_values)
    fallback = median_positive(
        [
            turnover
            for volume, turnover in zip(volume_values, turnover_values)
            if volume <= 0 and turnover > 0
        ]
    )
    if explicit <= 0 or fallback <= 0:
        return 1.0
    # Older daily-bar rows may store historical volume in hands inside `turnover`.
    return 100.0 if fallback * 20 < explicit else 1.0


def chart_volume_value(item: dict[str, object]) -> float:
    for key in ("volume", "turnover"):
        value = numeric_value(item.get(key))
        if value > 0:
            return value
    return 0.0


def numeric_value(value: object) -> float:
    if value is None:
        return 0.0
    try:
        if pd.isna(value):
            return 0.0
    except TypeError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def positive_count(values: list[float]) -> int:
    return sum(1 for value in values if value > 0)


def median_positive(values: list[float]) -> float:
    positives = sorted(value for value in values if value > 0)
    if not positives:
        return 0.0
    middle = len(positives) // 2
    if len(positives) % 2:
        return positives[middle]
    return (positives[middle - 1] + positives[middle]) / 2


def chart_volume_scale(values: list[float]) -> float:
    positives = sorted(value for value in values if value > 0)
    if not positives:
        return 0.0
    return positives[-1]


def chart_volume_height(value: float, scale: float, max_height: float) -> float:
    if value <= 0 or scale <= 0:
        return 0.0
    return min(value, scale) / scale * max_height


def price_color(up: bool) -> str:
    return "#dc2626" if up else "#16a34a"


def volume_bar_color(up: bool, highlighted: bool = False) -> str:
    if highlighted:
        return "#991b1b" if up else "#166534"
    return price_color(up)


def base_style() -> str:
    return """
<style>
:root{--canvas:#f4f1e9;--panel:#fffdf8;--panel-strong:#fff;--ink:#14233a;--muted:#687386;--line:#d9d7cf;--line-strong:#c6c3b8;--navy:#183b66;--blue:#245f9e;--blue-soft:#e9f1f8;--green:#176b52;--green-soft:#e7f3ed;--amber:#9b5b13;--amber-soft:#fbefdc;--red:#ae342b;--red-soft:#f8e8e5;--shadow:0 16px 44px rgba(31,42,55,.08)}
*{box-sizing:border-box}
body{font-family:'Microsoft YaHei UI','Noto Sans SC','Source Han Sans SC',sans-serif;margin:0;background:radial-gradient(circle at 8% 0%,rgba(36,95,158,.10),transparent 28%),radial-gradient(circle at 92% 10%,rgba(155,91,19,.08),transparent 24%),var(--canvas);color:var(--ink);font-variant-numeric:tabular-nums}
main{max-width:1680px;margin:0 auto;padding:22px 28px 44px}
.top{position:sticky;top:12px;z-index:20;display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:22px;padding:14px 16px;border:1px solid rgba(198,195,184,.88);border-radius:18px;background:rgba(255,253,248,.88);box-shadow:0 12px 34px rgba(31,42,55,.09);backdrop-filter:blur(16px)}
.eyebrow{display:block;margin-bottom:4px;color:var(--blue);font-size:10px;font-weight:800;letter-spacing:.14em}h1{font-size:28px;line-height:1.12;letter-spacing:-.025em;margin:0 0 4px}h2{font-size:18px;letter-spacing:-.01em;margin:0 0 15px}p{margin:0 0 8px}.hint,.status{font-size:12px;line-height:1.55;color:var(--muted)}
section,.stock-card{background:rgba(255,253,248,.95);border:1px solid var(--line);border-radius:18px;padding:22px;box-shadow:var(--shadow)}section{margin-bottom:16px}
.actions{display:flex;flex-wrap:wrap;gap:9px;align-items:center}.top>.actions{flex-wrap:nowrap;overflow-x:auto;padding:3px}.link-button{display:inline-flex;align-items:center;justify-content:center;min-height:36px;white-space:nowrap;border:1px solid transparent;border-radius:9px;background:transparent;color:#3d4c61;text-decoration:none;padding:8px 11px;font-size:12px;font-weight:700}.link-button:hover{background:var(--blue-soft);color:var(--blue)}.link-button.active{border-color:#b8ccdf;background:var(--blue-soft);color:var(--navy);box-shadow:inset 0 0 0 1px rgba(36,95,158,.06)}
.section-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:14px}.section-head h2{margin:0}.section-controls{display:flex;align-items:center;justify-content:flex-end;gap:9px;flex-wrap:wrap}.inline-action-form{margin:0}.section-controls .search-button{min-height:36px;border-radius:9px;padding:5px 12px}.chart-style-select,.ladder-date-select,.filter-select{height:36px;border:1px solid var(--line-strong);border-radius:9px;background:var(--panel-strong);color:var(--navy);font:inherit;font-size:12px;font-weight:800;padding:5px 10px;outline:none}.chart-style-select:focus,.ladder-date-select:focus,.filter-select:focus{border-color:var(--blue);box-shadow:0 0 0 3px rgba(36,95,158,.12)}.filter-panel{padding:16px}.pattern-filter-form{display:grid;grid-template-columns:minmax(180px,1.3fr) minmax(140px,.8fr) minmax(96px,.5fr) minmax(120px,.6fr) auto auto;gap:10px;align-items:end}.filter-input,.compact-field input{height:36px;border:1px solid var(--line-strong);border-radius:9px;background:var(--panel-strong);color:var(--ink);font:inherit;font-size:12px;font-weight:700;padding:5px 10px;outline:none}.filter-input:focus,.compact-field input:focus{border-color:var(--blue);box-shadow:0 0 0 3px rgba(36,95,158,.12)}.compact-field{gap:4px;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.06em}.pattern-filter-form .search-button{height:36px;min-height:36px;border-radius:9px;padding:5px 14px}.reset-link{height:36px}.table-footer{display:flex;justify-content:center;margin-top:12px}.table-action,.reason-details summary{display:inline-flex;align-items:center;justify-content:center;min-height:28px;border:1px solid #c8d7e5;border-radius:8px;background:var(--blue-soft);color:var(--navy);padding:4px 8px;text-decoration:none;font-size:11px;font-weight:800;cursor:pointer}.table-action:hover,.reason-details summary:hover{border-color:var(--blue);color:var(--blue)}.reason-details{display:inline-block}.reason-details summary{list-style:none}.reason-details summary::-webkit-details-marker{display:none}.reason-detail-text{max-width:540px;margin-top:8px;white-space:normal;line-height:1.55;color:var(--muted)}
.strategy-hero,.search-hero{overflow:hidden;background:linear-gradient(115deg,rgba(251,239,220,.86),rgba(255,253,248,.96) 58%,rgba(233,241,248,.92));border-color:#d9c9af}.strategy-hero{display:flex;align-items:flex-end;justify-content:space-between;gap:28px}.strategy-copy{min-width:260px}.hero-rail{display:flex;flex:1;flex-direction:column;align-items:flex-end;gap:14px;margin-left:auto}.metric-strip{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:8px}.metric-strip span{display:flex;align-items:baseline;gap:4px;white-space:nowrap;border:1px solid rgba(155,91,19,.18);border-radius:999px;background:rgba(255,253,248,.72);padding:7px 10px;color:var(--muted);font-size:11px}.metric-strip b{color:var(--ink);font-size:16px}
.search-hero{background:linear-gradient(115deg,rgba(233,241,248,.96),rgba(255,253,248,.96) 62%,rgba(251,239,220,.72));border-color:#bfd0df}.search-hero .search-form{margin-bottom:0}.search-form{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 14px}.search-input{flex:1 1 320px;min-height:46px;border:1px solid var(--line-strong);border-radius:11px;padding:10px 13px;font:inherit;font-size:14px;color:var(--ink);background:var(--panel-strong);outline:none}.search-input:focus{box-shadow:0 0 0 3px rgba(36,95,158,.13);border-color:var(--blue)}.search-button{min-height:46px;border:1px solid var(--blue);border-radius:11px;background:var(--navy);color:#fff;padding:9px 20px;font:inherit;font-size:13px;font-weight:800;cursor:pointer}.search-button:hover{background:var(--blue)}.empty-search{min-height:250px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;border:1px dashed #bdc8d4;border-radius:14px;background:rgba(244,241,233,.52);color:var(--muted)}.empty-search strong{font-size:18px;color:var(--ink);margin-bottom:8px}.empty-search p{max-width:520px}.search-pagination{display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:7px;margin:18px 0 0}.search-total{color:var(--muted);font-size:11px;margin-right:5px}.page-link{display:inline-flex;align-items:center;justify-content:center;min-width:34px;height:34px;border:1px solid var(--line);border-radius:9px;background:var(--panel-strong);color:var(--blue);text-decoration:none;padding:0 9px;font-size:12px}.page-link:hover{border-color:var(--blue);background:var(--blue-soft)}.page-link.current{border-color:var(--navy);background:var(--navy);color:#fff;font-weight:800}.page-link.disabled{color:#9a9faa;background:#f1f0ec;cursor:not-allowed}.page-ellipsis{color:#98a2b3;padding:0 2px}
.chart-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.stock-card{position:relative;overflow:hidden;scroll-margin-top:110px;padding:16px;background:var(--panel-strong);transition:transform .16s ease,box-shadow .16s ease}.stock-card:before{content:'';position:absolute;inset:0 0 auto;height:3px;background:linear-gradient(90deg,var(--navy),var(--blue),#b98033)}.stock-card:hover{transform:translateY(-2px);box-shadow:0 20px 50px rgba(31,42,55,.12)}.stock-head{display:flex;justify-content:space-between;gap:12px;margin-bottom:10px}.stock-head strong{font-size:16px;letter-spacing:.01em}.score{display:inline-flex;align-items:center;align-self:flex-start;border-radius:999px;background:var(--red-soft);padding:5px 9px;font-size:12px;font-weight:800;color:var(--red)}.tag{display:inline-block;border:1px solid #c8d7e5;background:var(--blue-soft);color:var(--navy);border-radius:999px;padding:3px 8px;margin:6px 5px 0 0;font-size:10px;font-weight:700}.limit-reason-note{margin:2px 0 10px;padding:9px 10px;border-left:3px solid var(--red);background:var(--red-soft);color:#5f2b27;font-size:12px;line-height:1.55}.limit-reason-note b{color:var(--red)}
.limit-reason-wrap{position:relative;margin:2px 0 10px;border-left:3px solid var(--red);background:var(--red-soft);padding:9px 10px;outline:none}.limit-reason-wrap .limit-reason-note{display:-webkit-box;max-height:36px;margin:0;padding:0;border:0;background:transparent;line-height:18px;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}.limit-reason-tooltip{position:absolute;left:0;right:0;bottom:calc(100% + 7px);z-index:40;display:none;border:1px solid #d8a49e;border-radius:10px;background:#fffaf8;color:#5f2b27;padding:10px 12px;box-shadow:0 16px 36px rgba(31,42,55,.18);font-size:12px;line-height:1.55;white-space:normal}.limit-reason-tooltip b{color:var(--red)}.limit-reason-wrap:hover .limit-reason-tooltip,.limit-reason-wrap:focus .limit-reason-tooltip,.limit-reason-wrap:focus-within .limit-reason-tooltip{display:block}.stock-card:hover,.stock-card:focus-within{overflow:visible;z-index:30}
.table-scroll{width:100%;overflow-x:auto;border:1px solid var(--line);border-radius:13px;background:var(--panel-strong)}.pattern-table,.ladder-table{width:100%;min-width:820px;border-collapse:separate;border-spacing:0;margin:0;font-size:12px}.compact-pattern-table{min-width:680px}.pattern-table th,.pattern-table td,.ladder-table th,.ladder-table td{border-bottom:1px solid #ebe8e0;padding:10px 12px;text-align:left;vertical-align:middle}.pattern-table th,.ladder-table th{position:sticky;top:0;z-index:2;background:#f3f1eb;color:#465267;font-size:10px;font-weight:800;letter-spacing:.06em;text-transform:uppercase}.pattern-table tr:last-child td,.ladder-table tr:last-child td{border-bottom:0}.pattern-table tbody tr:hover td,.ladder-table tbody tr:hover td{background:#faf8f2}.compact-pattern-table td{white-space:nowrap}.compact-pattern-table td:nth-child(1){width:72px}.compact-pattern-table td:nth-child(2){width:120px}.compact-pattern-table td:nth-child(3){width:92px}.compact-pattern-table td:nth-child(4){width:84px}.compact-pattern-table td:last-child{width:172px}.row-actions{display:flex;align-items:center;gap:7px}.ladder-date-select option{font-weight:700;color:var(--navy);background:#fff}.ladder-date-select option.report-day{color:#991b1b;background:#fee2e2}.ladder-date-select option.selected-day{color:#9b5b13;background:#fbefdc}.ladder-badge{display:inline-flex;align-items:center;justify-content:center;min-width:44px;border-radius:999px;padding:4px 8px;font-weight:800;font-size:10px}.reason-cell{max-width:380px;line-height:1.5}.ladder-collapse input{display:none}.ladder-collapse .extra-row{display:none}.ladder-collapse input:checked + .table-scroll .extra-row{display:table-row}.ladder-collapse label{cursor:pointer;color:var(--red);font-size:12px;font-weight:800;margin:10px 2px 0;display:inline-flex}.ladder-collapse .close-text{display:none}.ladder-collapse input:checked ~ label .open-text{display:none}.ladder-collapse input:checked ~ label .close-text{display:inline}
.stock-hover-trigger{color:var(--navy);font-weight:800;text-decoration:none;border-bottom:1px dashed rgba(36,95,158,.34)}.stock-hover-trigger:hover{color:var(--blue);border-bottom-color:var(--blue)}.hover-chart-panel{position:fixed;z-index:80;padding:12px;border:1px solid var(--line-strong);border-radius:13px;background:rgba(255,253,248,.98);box-shadow:0 22px 60px rgba(31,42,55,.18);pointer-events:none}.hover-chart-title{margin:0 0 8px;color:var(--navy);font-size:12px;font-weight:900}.hover-chart-panel svg{display:block;width:100%;margin:0}.compact-pattern-table{min-width:1420px}.compact-pattern-table th{padding:6px 10px}.compact-pattern-table td{padding:3px 10px}.compact-pattern-table .score{padding:3px 7px;font-size:11px}.compact-pattern-table .reason-details summary{min-height:22px}.compact-pattern-table .reason-toggle{min-height:22px;padding:2px 7px;font-size:10px}.compact-pattern-table td:nth-child(10){width:auto}.table-sort-link{display:inline-flex;align-items:center;gap:4px;color:inherit;text-decoration:none;white-space:nowrap}.table-sort-link:hover,.table-sort-link:focus-visible{color:var(--blue)}.table-sort-link.active{color:var(--navy)}.sort-arrow{font-size:12px;line-height:1;color:var(--red)}.compact-pattern-table .reason-cell{max-width:none;min-width:360px}.reason-details{display:block;width:100%}.reason-details summary{display:flex;align-items:center;gap:8px;width:100%;max-width:100%;padding:0;border:0;background:transparent;color:var(--ink);cursor:pointer}.reason-details summary:hover{color:var(--blue)}.reason-preview{display:block;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}.reason-toggle{display:inline-flex;align-items:center;justify-content:center;min-height:26px;flex:0 0 auto;border:1px solid #c8d7e5;border-radius:8px;background:var(--blue-soft);color:var(--navy);padding:3px 8px;font-size:11px;font-weight:800}.reason-details .close-text{display:none}.reason-details[open] .open-text{display:none}.reason-details[open] .close-text{display:inline}.reason-details[open] .reason-detail-text{display:block}.reason-detail-text{display:none;max-width:720px;margin-top:8px;white-space:normal;line-height:1.55;color:var(--muted)}
.summary-panel .table-scroll{overflow-x:hidden}.compact-pattern-table{min-width:0;table-layout:fixed}.compact-pattern-table th,.compact-pattern-table td{padding-left:6px;padding-right:6px}.compact-pattern-table th:nth-child(1),.compact-pattern-table td:nth-child(1){width:68px}.compact-pattern-table th:nth-child(2),.compact-pattern-table td:nth-child(2){width:78px}.compact-pattern-table th:nth-child(3),.compact-pattern-table td:nth-child(3){width:72px}.compact-pattern-table th:nth-child(4),.compact-pattern-table td:nth-child(4){width:68px}.compact-pattern-table th:nth-child(5),.compact-pattern-table td:nth-child(5){width:58px}.compact-pattern-table th:nth-child(6),.compact-pattern-table td:nth-child(6){width:62px}.compact-pattern-table th:nth-child(7),.compact-pattern-table td:nth-child(7){width:70px}.compact-pattern-table th:nth-child(8),.compact-pattern-table td:nth-child(8){width:70px}.compact-pattern-table th:nth-child(9),.compact-pattern-table td:nth-child(9){width:76px}.compact-pattern-table td:nth-child(10){width:auto;white-space:normal}.compact-pattern-table .reason-cell{min-width:0}.compact-pattern-table .reason-details summary{align-items:flex-start;pointer-events:none}.compact-pattern-table .reason-preview{overflow:visible;text-overflow:clip;white-space:normal;overflow-wrap:anywhere;line-height:1.35}.compact-pattern-table .reason-toggle,.compact-pattern-table .reason-detail-text{display:none!important}
.ladder-chart-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:13px;background:var(--panel-strong);padding:8px}svg{width:100%;height:auto;margin:4px 0 10px}.stock-card svg{border:1px solid #ece9e1;border-radius:12px;background:#fbfaf7}.empty-chart{height:220px;display:grid;place-items:center;background:#f6f4ee;border:1px dashed var(--line-strong);border-radius:13px;color:var(--muted)}
@media(max-width:1200px){.chart-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:960px){.top{position:static;align-items:flex-start;flex-direction:column}.top>.actions{width:100%}.strategy-hero{align-items:flex-start;flex-direction:column}.hero-rail{width:100%;align-items:flex-start;margin-left:0}.section-controls{justify-content:flex-start}.pattern-filter-form{grid-template-columns:repeat(2,minmax(0,1fr))}.metric-strip{justify-content:flex-start}.chart-grid{grid-template-columns:1fr}.stock-card{scroll-margin-top:20px}}
@media(max-width:600px){main{padding:12px}.top{padding:12px;margin-bottom:14px}section,.stock-card{padding:17px;border-radius:15px}h1{font-size:24px}.section-head{align-items:flex-start;flex-direction:column}.section-controls,.inline-action-form,.chart-style-select{width:100%}.pattern-filter-form{grid-template-columns:1fr}.filter-input,.filter-select,.compact-field input,.pattern-filter-form .search-button,.reset-link{width:100%}.search-form{flex-direction:column}.search-button{width:100%}.metric-strip span{flex:1;justify-content:center}.stock-card:hover{transform:none}}
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
