from __future__ import annotations

from html import escape

from .analysis import TEXT, format_amount
from .models import Candidate, HotTopic, IndexSnapshot, Recap


LABELS = {
    "disclaimer": "\u4ec5\u7528\u4e8e\u590d\u76d8\u7814\u7a76\uff0c\u4e0d\u6784\u6210\u6295\u8d44\u5efa\u8bae\u3002\u63a8\u8350\u503c\u4ee3\u8868\u89c2\u5bdf\u4f18\u5148\u7ea7\u3002",
    "market": "\u5e02\u573a\u6982\u89c8",
    "indexes": "\u6307\u6570",
    "hot": "\u70ed\u70b9\u4e0e\u9f99\u5934",
    "opportunity": "\u77ed\u7ebf\u673a\u4f1a",
    "notes": "\u6570\u636e\u63d0\u793a",
    "discipline": "\u660e\u65e5\u6267\u884c\u7eaa\u5f8b",
    "none": "\u6682\u65e0\u7b26\u5408\u5f53\u524d\u89c4\u5219\u7684\u5019\u9009\u3002",
}


def render_report(recap: Recap, title_prefix: str) -> tuple[str, str, str]:
    date_text = recap.market.trade_date.isoformat()
    title = f"{title_prefix} {date_text}"
    html = render_html(recap, title)
    text = render_text(recap, title)
    return title, html, text


def render_html(recap: Recap, title: str) -> str:
    sections = [
        "<!doctype html><html><head><meta charset=\"utf-8\">",
        "<style>",
        "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Arial,sans-serif;color:#222;line-height:1.55;margin:0;padding:18px;background:#f6f7f9}",
        ".wrap{max-width:860px;margin:0 auto;background:#fff;padding:20px;border:1px solid #e6e8eb}",
        "h1{font-size:22px;margin:0 0 8px}h2{font-size:17px;margin:22px 0 10px;border-left:4px solid #2f6fed;padding-left:8px}",
        "table{width:100%;border-collapse:collapse;margin:8px 0 14px}th,td{border:1px solid #e6e8eb;padding:7px 8px;text-align:left;font-size:13px}th{background:#f1f4f8}",
        ".muted{color:#666;font-size:13px}.score{font-weight:700;color:#d14}.tag{display:inline-block;background:#eef4ff;color:#1f5fbf;padding:2px 6px;margin:1px 3px 1px 0;border-radius:3px;font-size:12px}",
        ".small{font-size:12px;color:#666}.empty{color:#777;padding:8px 0}",
        "</style></head><body><div class=\"wrap\">",
        f"<h1>{escape(title)}</h1>",
        f"<p class=\"muted\">{LABELS['disclaimer']}</p>",
        render_market(recap),
        render_hot(recap),
        render_opportunities(recap),
        render_notes(recap),
        render_discipline(),
        "</div></body></html>",
    ]
    return "\n".join(sections)


def render_market(recap: Recap) -> str:
    market = recap.market
    index_html = render_indexes(market.indexes)
    return f"""
<h2>{LABELS['market']}</h2>
<table>
<tr><th>市场情绪</th><th>上涨/下跌</th><th>涨停/跌停</th><th>主板成交额</th><th>平均涨跌幅</th></tr>
<tr><td>{escape(market.sentiment)}</td><td>{market.up_count}/{market.down_count}</td><td>{market.limit_up_count}/{market.limit_down_count}</td><td>{format_amount(market.total_turnover)}</td><td>{market.average_change_pct:.2f}%</td></tr>
</table>
{index_html}
"""


def render_indexes(indexes: list[IndexSnapshot]) -> str:
    if not indexes:
        return "<p class=\"small\">指数数据暂缺。</p>"
    rows = "".join(
        f"<tr><td>{escape(item.name)}</td><td>{fmt_num(item.close)}</td><td>{fmt_pct(item.change_pct)}</td></tr>"
        for item in indexes
    )
    return f"<h3>{LABELS['indexes']}</h3><table><tr><th>指数</th><th>点位</th><th>涨跌幅</th></tr>{rows}</table>"


def render_hot(recap: Recap) -> str:
    leaders = render_candidate_table(recap.limit_leaders, compact=True)
    industries = render_topic_list(recap.industries)
    concepts = render_topic_list(recap.concepts)
    max_board = max([item.limit_up_days or 0 for item in recap.limit_leaders], default=0)
    return f"""
<h2>{LABELS['hot']}</h2>
<table>
<tr><th>连板高度</th><th>行业热度</th><th>概念热度</th></tr>
<tr><td>{max_board if max_board else '暂缺'}</td><td>{industries}</td><td>{concepts}</td></tr>
</table>
<h3>涨停池/龙头线索</h3>
{leaders}
"""


def render_topic_list(items: list[HotTopic]) -> str:
    if not items:
        return "暂缺"
    return "<br>".join(f"{escape(item.name)} {fmt_pct(item.change_pct)}" for item in items)


def render_opportunities(recap: Recap) -> str:
    groups = [
        (TEXT["rebound"], [item for item in recap.candidates if TEXT["rebound"] in item.strategy_tags]),
        (TEXT["pullback"], [item for item in recap.candidates if TEXT["pullback"] in item.strategy_tags]),
        (TEXT["second_wave"], [item for item in recap.candidates if TEXT["second_wave"] in item.strategy_tags]),
    ]
    blocks = [f"<h2>{LABELS['opportunity']}</h2>"]
    for label, items in groups:
        blocks.append(f"<h3>{escape(label)}</h3>")
        blocks.append(render_candidate_table(items))
    return "\n".join(blocks)


def render_candidate_table(candidates: list[Candidate], compact: bool = False) -> str:
    if not candidates:
        return f"<div class=\"empty\">{LABELS['none']}</div>"
    if compact:
        rows = "".join(
            f"<tr><td>{escape(item.code)}</td><td>{escape(item.name)}</td><td>{item.limit_up_days or '-'}</td><td>{item.hot_rank or '-'}</td><td>{item.score}%</td></tr>"
            for item in candidates[:5]
        )
        return f"<table><tr><th>代码</th><th>名称</th><th>连板</th><th>人气</th><th>推荐值</th></tr>{rows}</table>"

    rows = []
    for item in candidates:
        tags = "".join(f"<span class=\"tag\">{escape(tag)}</span>" for tag in item.strategy_tags)
        rows.append(
            "<tr>"
            f"<td>{escape(item.code)}<br>{escape(item.name)}</td>"
            f"<td class=\"score\">{item.score}%</td>"
            f"<td>{tags}</td>"
            f"<td>{item.close:.2f}<br>{item.change_pct:.2f}%</td>"
            f"<td>{escape(item.trigger)}</td>"
            f"<td>{escape(item.invalidation)}</td>"
            f"<td>{escape('; '.join(item.reasons))}</td>"
            "</tr>"
        )
    return (
        "<table><tr><th>股票</th><th>推荐值</th><th>策略标签</th><th>收盘/涨跌</th>"
        "<th>入场观察条件</th><th>失效条件</th><th>核心依据</th></tr>"
        + "".join(rows)
        + "</table>"
    )


def render_notes(recap: Recap) -> str:
    notes = recap.market.notes + recap.warnings
    if not notes:
        return ""
    rows = "".join(f"<li>{escape(note)}</li>" for note in notes)
    return f"<h2>{LABELS['notes']}</h2><ul>{rows}</ul>"


def render_discipline() -> str:
    return f"""
<h2>{LABELS['discipline']}</h2>
<ul>
<li>先看市场情绪，再看板块梯队，最后看个股触发。</li>
<li>未触发条件不追，触发后仍需用仓位控制验证。</li>
<li>单票观察失效后不补仓摊低，等待下一次结构重新出现。</li>
</ul>
"""


def render_text(recap: Recap, title: str) -> str:
    lines = [
        title,
        LABELS["disclaimer"],
        "",
        f"{LABELS['market']}: 情绪{recap.market.sentiment}，上涨/下跌 {recap.market.up_count}/{recap.market.down_count}，涨停/跌停 {recap.market.limit_up_count}/{recap.market.limit_down_count}，成交额 {format_amount(recap.market.total_turnover)}。",
        "",
        LABELS["opportunity"],
    ]
    for item in recap.candidates:
        lines.append(
            f"- {item.code} {item.name}: {item.score}% | {'/'.join(item.strategy_tags)} | {item.trigger}"
        )
    return "\n".join(lines) + "\n"


def fmt_num(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def fmt_pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}%"

