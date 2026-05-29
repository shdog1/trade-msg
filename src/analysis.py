from __future__ import annotations

import re
from datetime import date
from typing import Any

import pandas as pd

from .market_data import MarketData
from .models import Candidate, MarketSnapshot, Recap


COLUMN_ALIASES = {
    "code": ["\u4ee3\u7801", "\u80a1\u7968\u4ee3\u7801", "symbol", "code"],
    "name": ["\u540d\u79f0", "\u80a1\u7968\u7b80\u79f0", "name"],
    "close": ["\u6700\u65b0\u4ef7", "\u6536\u76d8", "trade", "close"],
    "change_pct": ["\u6da8\u8dcc\u5e45", "\u6da8\u5e45", "changepercent", "change_pct"],
    "turnover": ["\u6210\u4ea4\u989d", "amount", "turnover"],
    "volume_ratio": ["\u91cf\u6bd4", "volume_ratio"],
    "amplitude_pct": ["\u632f\u5e45", "amplitude"],
    "turnover_rate": ["\u6362\u624b\u7387", "turnoverratio", "turnover_rate"],
    "hot_rank": ["\u6392\u540d", "\u5f53\u524d\u6392\u540d", "hot_rank"],
    "limit_up_days": ["\u8fde\u677f\u6570", "\u8fde\u7eed\u6da8\u505c", "limit_up_days"],
}


def build_recap(data: MarketData, config: dict[str, Any]) -> Recap:
    spot = normalize_spot(data.spot)
    spot = filter_main_board(spot, config)
    hot_map = normalize_hot_rank(data.hot_rank)
    limit_map = normalize_limit_pool(data.limit_pool)

    market = summarize_market(spot, data.trade_date)
    candidates = score_candidates(spot, hot_map, limit_map, market, config)
    return Recap(market=market, candidates=candidates, warnings=data.warnings)


def normalize_spot(df: pd.DataFrame) -> pd.DataFrame:
    normalized = pd.DataFrame()
    for target, aliases in COLUMN_ALIASES.items():
        source = next((name for name in aliases if name in df.columns), None)
        if source:
            normalized[target] = df[source]

    required = {"code", "name", "close", "change_pct", "turnover"}
    missing = required - set(normalized.columns)
    if missing:
        raise ValueError(f"Spot data is missing required columns: {sorted(missing)}")

    normalized["code"] = normalized["code"].map(normalize_stock_code)
    normalized["name"] = normalized["name"].astype(str)
    for col in ["close", "change_pct", "turnover", "volume_ratio", "amplitude_pct", "turnover_rate"]:
        if col in normalized.columns:
            normalized[col] = pd.to_numeric(normalized[col], errors="coerce")
    return normalized.dropna(subset=["code", "close", "change_pct", "turnover"])


def normalize_stock_code(value: Any) -> str | None:
    match = re.search(r"(\d{6})", str(value))
    return match.group(1) if match else None


def filter_main_board(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    market_cfg = config.get("market", {})
    prefixes = tuple(str(prefix) for prefix in market_cfg.get("main_board_prefixes", []))
    excludes = [str(item).upper() for item in market_cfg.get("exclude_name_keywords", [])]

    mask = df["code"].str.startswith(prefixes)
    names = df["name"].str.upper()
    for keyword in excludes:
        mask &= ~names.str.contains(keyword, regex=False)
    return df.loc[mask].copy()


def normalize_hot_rank(df: pd.DataFrame) -> dict[str, int]:
    if df.empty:
        return {}
    normalized = pd.DataFrame()
    for target in ["code", "hot_rank"]:
        source = next((name for name in COLUMN_ALIASES[target] if name in df.columns), None)
        if source:
            normalized[target] = df[source]
    if {"code", "hot_rank"} - set(normalized.columns):
        return {}
    normalized["code"] = normalized["code"].map(normalize_stock_code)
    normalized["hot_rank"] = pd.to_numeric(normalized["hot_rank"], errors="coerce")
    return {
        row.code: int(row.hot_rank)
        for row in normalized.dropna(subset=["code", "hot_rank"]).itertuples()
    }


def normalize_limit_pool(df: pd.DataFrame) -> dict[str, int]:
    if df.empty:
        return {}
    code_col = next((name for name in COLUMN_ALIASES["code"] if name in df.columns), None)
    days_col = next((name for name in COLUMN_ALIASES["limit_up_days"] if name in df.columns), None)
    if code_col is None:
        return {}
    result: dict[str, int] = {}
    for row in df.to_dict("records"):
        code = normalize_stock_code(row.get(code_col, ""))
        if not code:
            continue
        raw_days = row.get(days_col, 1) if days_col else 1
        try:
            result[code] = max(1, int(raw_days))
        except (TypeError, ValueError):
            result[code] = 1
    return result


def summarize_market(df: pd.DataFrame, trade_date: date) -> MarketSnapshot:
    total = len(df)
    up = int((df["change_pct"] > 0).sum())
    down = int((df["change_pct"] < 0).sum())
    limit_up = int((df["change_pct"] >= 9.8).sum())
    limit_down = int((df["change_pct"] <= -9.8).sum())
    turnover = float(df["turnover"].sum())
    avg_change = float(df["change_pct"].mean()) if total else 0.0
    notes: list[str] = []
    if total == 0:
        notes.append("No usable main-board quote rows after filtering.")
    return MarketSnapshot(
        trade_date=trade_date,
        total_count=total,
        up_count=up,
        down_count=down,
        limit_up_count=limit_up,
        limit_down_count=limit_down,
        total_turnover=turnover,
        average_change_pct=avg_change,
        notes=notes,
    )


def score_candidates(
    df: pd.DataFrame,
    hot_map: dict[str, int],
    limit_map: dict[str, int],
    market: MarketSnapshot,
    config: dict[str, Any],
) -> list[Candidate]:
    min_turnover = float(config.get("market", {}).get("min_turnover_amount", 300_000_000))
    max_candidates = int(config.get("market", {}).get("max_candidates", 8))

    pool = df.loc[df["turnover"] >= min_turnover].copy()
    if pool.empty:
        pool = df.sort_values("turnover", ascending=False).head(max_candidates * 2).copy()

    candidates: list[Candidate] = []
    for row in pool.to_dict("records"):
        code = str(row["code"])
        hot_rank = hot_map.get(code)
        limit_days = limit_map.get(code)
        tags = strategy_tags(row, hot_rank, limit_days)
        if not tags:
            continue

        parts = score_parts(row, hot_rank, limit_days, market, tags)
        score = max(0, min(100, round(sum(parts.values()))))
        candidates.append(
            Candidate(
                code=code,
                name=str(row["name"]),
                close=float(row["close"]),
                change_pct=float(row["change_pct"]),
                turnover=float(row["turnover"]),
                volume_ratio=_optional_float(row.get("volume_ratio")),
                amplitude_pct=_optional_float(row.get("amplitude_pct")),
                hot_rank=hot_rank,
                limit_up_days=limit_days,
                strategy_tags=tags,
                score=score,
                score_parts=parts,
                trigger=build_trigger(row, tags),
                invalidation=build_invalidation(row),
                reasons=build_reasons(row, hot_rank, limit_days, tags),
                raw=row,
            )
        )

    return sorted(candidates, key=lambda item: item.score, reverse=True)[:max_candidates]


def strategy_tags(row: dict[str, Any], hot_rank: int | None, limit_days: int | None) -> list[str]:
    change = float(row.get("change_pct") or 0)
    turnover_rate = _optional_float(row.get("turnover_rate"))
    volume_ratio = _optional_float(row.get("volume_ratio"))
    tags: list[str] = []

    is_hot = hot_rank is not None and hot_rank <= 80
    is_limit_leader = limit_days is not None and limit_days >= 1
    is_liquid = float(row.get("turnover") or 0) >= 300_000_000

    # The hot-rank and limit-up sources can fail independently; keep a pure-data fallback.
    if not is_hot and not is_limit_leader:
        is_hot = is_liquid and (turnover_rate or 0) >= 3

    if is_liquid and (is_hot or is_limit_leader) and -4 <= change <= 3:
        tags.append("leader pullback watch")
    if is_liquid and (is_hot or is_limit_leader) and 3 < change < 9.8:
        tags.append("leader rebound watch")
    if is_liquid and is_limit_leader and limit_days >= 2:
        tags.append("leader second-wave watch")
    if turnover_rate and volume_ratio and turnover_rate >= 5 and volume_ratio >= 1.2 and 0 <= change <= 7:
        tags.append("volume support watch")
    return tags


def score_parts(
    row: dict[str, Any],
    hot_rank: int | None,
    limit_days: int | None,
    market: MarketSnapshot,
    tags: list[str],
) -> dict[str, int]:
    breadth = market.up_count / market.total_count if market.total_count else 0
    market_score = min(20, max(0, round(8 + breadth * 10 + market.limit_up_count / 20)))

    leader = 5
    if hot_rank is not None:
        leader += max(0, 14 - hot_rank // 8)
    if limit_days is not None:
        leader += min(11, 5 + limit_days * 3)
    if hot_rank is None and limit_days is None:
        leader += min(12, round(float(row.get("turnover") or 0) / 500_000_000 * 3))
    leader = min(25, leader)

    strategy = min(25, 10 + len(tags) * 5)
    change = float(row.get("change_pct") or 0)
    amplitude = _optional_float(row.get("amplitude_pct")) or 0
    reward_risk = max(0, min(20, round(15 - max(0, change - 5) - amplitude * 0.2)))

    turnover = float(row.get("turnover") or 0)
    liquidity = min(10, round(turnover / 500_000_000 * 4 + 4))

    return {
        "market_environment": market_score,
        "leader_strength": leader,
        "strategy_fit": strategy,
        "reward_risk": reward_risk,
        "liquidity_risk": liquidity,
    }


def build_reasons(
    row: dict[str, Any],
    hot_rank: int | None,
    limit_days: int | None,
    tags: list[str],
) -> list[str]:
    reasons = [f"strategy: {', '.join(tags)}"]
    if hot_rank is not None:
        reasons.append(f"hot rank: {hot_rank}")
    if limit_days is not None:
        reasons.append(f"limit-up strength: {limit_days}")
    reasons.append(f"turnover: {format_amount(float(row.get('turnover') or 0))}")
    return reasons


def build_trigger(row: dict[str, Any], tags: list[str]) -> str:
    close = float(row["close"])
    if any("pullback" in tag for tag in tags):
        return f"Watch only if it holds above {close * 0.97:.2f} and reclaims intraday VWAP."
    if any("second-wave" in tag for tag in tags):
        return f"Watch breakout above {close * 1.03:.2f} with sector strength still active."
    return f"Watch breakout above {close * 1.02:.2f}; avoid chasing if gap-up is above 3%."


def build_invalidation(row: dict[str, Any]) -> str:
    close = float(row["close"])
    return f"Invalid below {close * 0.95:.2f} or if sector leaders weaken sharply."


def format_amount(amount: float) -> str:
    if amount >= 100_000_000:
        return f"{amount / 100_000_000:.1f}B CNY"
    if amount >= 10_000:
        return f"{amount / 10_000:.0f}W CNY"
    return f"{amount:.0f}"


def _optional_float(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None

