from __future__ import annotations

from .analysis import format_amount
from .models import Candidate, Recap


def render_markdown(recap: Recap, title_prefix: str) -> tuple[str, str]:
    date_text = recap.market.trade_date.isoformat()
    title = f"{title_prefix} {date_text}"

    lines = [
        f"# {title}",
        "",
        "> Research recap only. Scores are watch-list priority, not investment advice.",
        "",
        "## Market Overview",
        f"- Main-board sample: {recap.market.total_count}",
        f"- Up / down: {recap.market.up_count} / {recap.market.down_count}",
        f"- Limit-up / limit-down: {recap.market.limit_up_count} / {recap.market.limit_down_count}",
        f"- Main-board turnover: {format_amount(recap.market.total_turnover)}",
        f"- Average change: {recap.market.average_change_pct:.2f}%",
        "",
    ]

    if recap.market.notes:
        lines.extend(["## Data Notes", *[f"- {note}" for note in recap.market.notes], ""])
    if recap.warnings:
        lines.extend(["## Source Notes", *[f"- {warning}" for warning in recap.warnings], ""])

    lines.extend(["## Short-Term Watch Candidates", ""])
    if not recap.candidates:
        lines.append("No main-board leader candidates matched the current rules today.")
    else:
        for index, candidate in enumerate(recap.candidates, start=1):
            lines.extend(render_candidate(index, candidate))

    lines.extend(
        [
            "",
            "## Tomorrow Discipline",
            "- Check market mood first, then sector ladder, then individual trigger.",
            "- Do not chase if the trigger is not met.",
            "- If invalidated, wait for a new setup instead of averaging down.",
        ]
    )

    return title, "\n".join(lines).strip() + "\n"


def render_candidate(index: int, candidate: Candidate) -> list[str]:
    rank = f"hot rank {candidate.hot_rank}" if candidate.hot_rank else "hot rank unavailable"
    limit = f"{candidate.limit_up_days} limit-up strength" if candidate.limit_up_days else "not in limit-up pool"
    return [
        f"### {index}. {candidate.name} ({candidate.code}) - watch score {candidate.score}%",
        f"- Tags: {', '.join(candidate.strategy_tags)}",
        f"- Close / change: {candidate.close:.2f} / {candidate.change_pct:.2f}%",
        f"- Turnover: {format_amount(candidate.turnover)}; {rank}; {limit}",
        f"- Trigger: {candidate.trigger}",
        f"- Invalidation: {candidate.invalidation}",
        f"- Reasons: {'; '.join(candidate.reasons)}",
        "",
    ]

