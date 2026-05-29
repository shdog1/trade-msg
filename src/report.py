from __future__ import annotations

from .analysis import format_amount
from .models import Candidate, Recap


def render_markdown(recap: Recap, title_prefix: str) -> tuple[str, str]:
    date_text = recap.market.trade_date.isoformat()
    title = f"{title_prefix} {date_text}"

    lines = [
        f"# {title}",
        "",
        "> 仅用于复盘研究，不构成投资建议。推荐值代表观察优先级，不代表确定性收益。",
        "",
        "## 市场概览",
        f"- 主板样本：{recap.market.total_count} 只",
        f"- 上涨/下跌：{recap.market.up_count} / {recap.market.down_count}",
        f"- 涨停/跌停：{recap.market.limit_up_count} / {recap.market.limit_down_count}",
        f"- 主板成交额：{format_amount(recap.market.total_turnover)}",
        f"- 平均涨跌幅：{recap.market.average_change_pct:.2f}%",
        "",
    ]

    if recap.market.notes:
        lines.extend(["## 数据提示", *[f"- {note}" for note in recap.market.notes], ""])
    if recap.warnings:
        lines.extend(["## 采集提示", *[f"- {warning}" for warning in recap.warnings], ""])

    lines.extend(["## 短线观察候选", ""])
    if not recap.candidates:
        lines.append("今日未筛出满足初版规则的主板龙头观察候选。")
    else:
        for index, candidate in enumerate(recap.candidates, start=1):
            lines.extend(render_candidate(index, candidate))

    lines.extend(
        [
            "",
            "## 明日执行纪律",
            "- 先看市场情绪，再看板块梯队，最后看个股触发。",
            "- 未触发条件不追，触发后仍需用仓位控制验证。",
            "- 单票观察失效后不补仓摊低，等下一次结构重新出现。",
        ]
    )

    return title, "\n".join(lines).strip() + "\n"


def render_candidate(index: int, candidate: Candidate) -> list[str]:
    rank = f"人气 {candidate.hot_rank}" if candidate.hot_rank else "人气暂无"
    limit = f"{candidate.limit_up_days}板" if candidate.limit_up_days else "非涨停池"
    lines = [
        f"### {index}. {candidate.name} ({candidate.code}) - 推荐值 {candidate.score}%",
        f"- 标签：{'、'.join(candidate.strategy_tags)}",
        f"- 收盘/涨跌幅：{candidate.close:.2f} / {candidate.change_pct:.2f}%",
        f"- 成交额：{format_amount(candidate.turnover)}，{rank}，{limit}",
        f"- 观察触发：{candidate.trigger}",
        f"- 失效条件：{candidate.invalidation}",
        f"- 依据：{'；'.join(candidate.reasons)}",
        "",
    ]
    return lines

