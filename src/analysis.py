from __future__ import annotations

import re
from datetime import date
from typing import Any

import pandas as pd

from .market_data import MarketData
from .models import Candidate, HotTopic, IndexSnapshot, MarketSnapshot, Recap


TEXT = {
    "no_main_board": "\u4e3b\u677f\u8fc7\u6ee4\u540e\u65e0\u53ef\u7528\u884c\u60c5\u6570\u636e\u3002",
    "sentiment_hot": "\u504f\u5f3a",
    "sentiment_warm": "\u4fee\u590d",
    "sentiment_mixed": "\u9707\u8361",
    "sentiment_cold": "\u504f\u5f31",
    "volume": "\u653e\u91cf\u627f\u63a5",
    "strategy": "\u7b56\u7565\u5339\u914d",
    "hot_rank": "\u4eba\u6c14\u6392\u540d",
    "limit_strength": "\u8fde\u677f/\u6da8\u505c\u5f3a\u5ea6",
    "turnover": "\u6210\u4ea4\u989d",
    "history_missing": "\u5386\u53f2\u6837\u672c\u4e0d\u8db3\uff0c\u6309\u5f53\u65e5\u5f3a\u5ea6\u515c\u5e95",
    "return_20d": "\u8fd120\u65e5\u6da8\u5e45",
    "drawdown": "\u8ddd20\u65e5\u524d\u9ad8\u56de\u64a4",
    "ma_state": "\u5747\u7ebf\u72b6\u6001",
    "turnover_change": "\u6210\u4ea4\u989d\u53d8\u5316",
    "limit_platform": "\u8fde\u677f\u5e73\u53f0\u6d17\u76d8",
    "platform_watch": "\u5e73\u53f0\u89c2\u5bdf",
    "platform_confirm": "\u6536\u590d\u786e\u8ba4",
    "attack_strength": "\u524d\u6bb5\u5f3a\u5ea6",
    "huge_volume": "\u5de8\u91cf\u786e\u8ba4",
    "platform_quality": "\u5e73\u53f0\u8d28\u91cf",
    "drawdown_control": "\u56de\u64a4\u63a7\u5236",
    "stage_confirm": "\u5f53\u524d\u9636\u6bb5",
}

COLUMN_ALIASES = {
    "code": ["\u4ee3\u7801", "\u80a1\u7968\u4ee3\u7801", "symbol", "code"],
    "name": ["\u540d\u79f0", "\u80a1\u7968\u7b80\u79f0", "name"],
    "open": ["\u4eca\u5f00", "\u5f00\u76d8", "open"],
    "high": ["\u6700\u9ad8", "high"],
    "low": ["\u6700\u4f4e", "low"],
    "close": ["\u6700\u65b0\u4ef7", "\u6536\u76d8", "trade", "close"],
    "change_pct": ["\u6da8\u8dcc\u5e45", "\u6da8\u5e45", "changepercent", "change_pct"],
    "volume": ["\u6210\u4ea4\u91cf", "volume"],
    "turnover": ["\u6210\u4ea4\u989d", "amount", "turnover"],
    "volume_ratio": ["\u91cf\u6bd4", "volume_ratio"],
    "amplitude_pct": ["\u632f\u5e45", "amplitude"],
    "turnover_rate": ["\u6362\u624b\u7387", "turnoverratio", "turnover_rate"],
    "total_market_cap": ["\u603b\u5e02\u503c", "total_market_cap", "market_cap"],
    "hot_rank": ["\u6392\u540d", "\u5f53\u524d\u6392\u540d", "hot_rank"],
    "limit_up_days": ["\u8fde\u677f\u6570", "\u8fde\u7eed\u6da8\u505c", "limit_up_days"],
}

TOPIC_ALIASES = {
    "name": ["\u540d\u79f0", "\u677f\u5757\u540d\u79f0", "name"],
    "change_pct": ["\u6da8\u8dcc\u5e45", "\u6da8\u5e45", "change_pct"],
    "turnover": ["\u6210\u4ea4\u989d", "amount", "turnover"],
}

INDEX_NAMES = {
    "\u4e0a\u8bc1\u6307\u6570",
    "\u6df1\u8bc1\u6210\u6307",
    "\u521b\u4e1a\u677f\u6307",
    "\u6caa\u6df1300",
    "\u4e2d\u8bc1500",
}


def build_recap(data: MarketData, config: dict[str, Any]) -> Recap:
    spot = filter_main_board(normalize_spot(data.spot), config)
    hot_map = normalize_hot_rank(data.hot_rank)
    limit_map = normalize_limit_pool(data.limit_pool)
    history_map = build_history_features(data.daily_bars, data.trade_date)

    market = summarize_market(spot, data.trade_date, data.indexes)
    limit_platform_candidates = build_limit_platform_candidates(spot, data.daily_bars, data.trade_date, config)
    limit_leaders = build_limit_leaders(spot, hot_map, limit_map, market, config, history_map)
    industries = normalize_topics(data.industries, top_n=5)
    concepts = normalize_topics(data.concepts, top_n=5)
    return Recap(
        market=market,
        candidates=[],
        limit_platform_candidates=limit_platform_candidates,
        industries=industries,
        concepts=concepts,
        limit_leaders=limit_leaders,
        warnings=data.warnings,
    )


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
    for col in [
        "open",
        "high",
        "low",
        "close",
        "change_pct",
        "volume",
        "turnover",
        "volume_ratio",
        "amplitude_pct",
        "turnover_rate",
        "total_market_cap",
    ]:
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


def summarize_market(df: pd.DataFrame, trade_date: date, index_df: pd.DataFrame) -> MarketSnapshot:
    total = len(df)
    up = int((df["change_pct"] > 0).sum())
    down = int((df["change_pct"] < 0).sum())
    limit_up = int((df["change_pct"] >= 9.8).sum())
    limit_down = int((df["change_pct"] <= -9.8).sum())
    turnover = float(df["turnover"].sum())
    avg_change = float(df["change_pct"].mean()) if total else 0.0
    notes: list[str] = []
    if total == 0:
        notes.append(TEXT["no_main_board"])
    return MarketSnapshot(
        trade_date=trade_date,
        total_count=total,
        up_count=up,
        down_count=down,
        limit_up_count=limit_up,
        limit_down_count=limit_down,
        total_turnover=turnover,
        average_change_pct=avg_change,
        sentiment=market_sentiment(total, up, limit_up, limit_down, avg_change),
        indexes=normalize_indexes(index_df),
        notes=notes,
    )


def normalize_indexes(df: pd.DataFrame) -> list[IndexSnapshot]:
    if df.empty:
        return []
    name_col = next((name for name in COLUMN_ALIASES["name"] if name in df.columns), None)
    close_col = next((name for name in COLUMN_ALIASES["close"] if name in df.columns), None)
    change_col = next((name for name in COLUMN_ALIASES["change_pct"] if name in df.columns), None)
    if not name_col:
        return []
    rows: list[IndexSnapshot] = []
    for item in df.to_dict("records"):
        name = str(item.get(name_col, ""))
        if name not in INDEX_NAMES:
            continue
        rows.append(
            IndexSnapshot(
                name=name,
                close=_optional_float(item.get(close_col)) if close_col else None,
                change_pct=_optional_float(item.get(change_col)) if change_col else None,
            )
        )
    return rows[:5]


def normalize_topics(df: pd.DataFrame, top_n: int) -> list[HotTopic]:
    if df.empty:
        return []
    name_col = next((name for name in TOPIC_ALIASES["name"] if name in df.columns), None)
    change_col = next((name for name in TOPIC_ALIASES["change_pct"] if name in df.columns), None)
    turnover_col = next((name for name in TOPIC_ALIASES["turnover"] if name in df.columns), None)
    if not name_col or not change_col:
        return []
    work = df.copy()
    work["_change"] = pd.to_numeric(work[change_col], errors="coerce")
    work = work.dropna(subset=["_change"]).sort_values("_change", ascending=False)
    result: list[HotTopic] = []
    for row in work.head(top_n).to_dict("records"):
        result.append(
            HotTopic(
                name=str(row.get(name_col, "")),
                change_pct=_optional_float(row.get(change_col)),
                turnover=_optional_float(row.get(turnover_col)) if turnover_col else None,
            )
        )
    return result


def market_sentiment(total: int, up: int, limit_up: int, limit_down: int, avg_change: float) -> str:
    breadth = up / total if total else 0
    if breadth >= 0.62 and limit_up >= 45 and avg_change > 0:
        return TEXT["sentiment_hot"]
    if breadth >= 0.52 and avg_change >= -0.2:
        return TEXT["sentiment_warm"]
    if breadth >= 0.42:
        return TEXT["sentiment_mixed"]
    if limit_down > max(8, limit_up * 0.4):
        return TEXT["sentiment_cold"]
    return TEXT["sentiment_cold"]


def build_history_features(df: pd.DataFrame, trade_date: date) -> dict[str, dict[str, Any]]:
    if df.empty or "code" not in df.columns:
        return {}

    work = df.copy()
    work["trade_date"] = pd.to_datetime(work["trade_date"], errors="coerce")
    work = work.dropna(subset=["trade_date", "code"])
    work = work[work["trade_date"].dt.date <= trade_date]
    for column in ["open", "high", "low", "close", "change_pct", "turnover", "turnover_rate", "amplitude_pct"]:
        if column in work.columns:
            work[column] = pd.to_numeric(work[column], errors="coerce")
    if work.empty:
        return {}

    result: dict[str, dict[str, Any]] = {}
    for code, group in work.groupby("code"):
        bars = group.sort_values("trade_date").dropna(subset=["close"]).tail(80)
        if bars.empty:
            continue
        closes = bars["close"].astype(float)
        highs = bars["high"].fillna(bars["close"]).astype(float) if "high" in bars else closes
        turnovers = bars["turnover"].astype(float) if "turnover" in bars else pd.Series(dtype=float)
        changes = bars["change_pct"].astype(float) if "change_pct" in bars else pd.Series(dtype=float)
        current_close = float(closes.iloc[-1])
        high_10d = float(highs.tail(10).max()) if len(highs) >= 10 else None
        high_20d = float(highs.tail(20).max()) if len(highs) >= 20 else None
        high_60d = float(highs.tail(60).max()) if len(highs) >= 20 else high_20d
        ma5 = moving_average(closes, 5)
        ma10 = moving_average(closes, 10)
        ma20 = moving_average(closes, 20)
        avg_turnover_5d = moving_average(turnovers, 5)
        avg_turnover_20d = moving_average(turnovers, 20)
        return_5d = window_return(closes, 5)
        return_10d = window_return(closes, 10)
        return_20d = window_return(closes, 20)
        return_60d = window_return(closes, 60)
        drawdown_20 = pct_change(current_close, high_20d) if high_20d else None
        drawdown_60 = pct_change(current_close, high_60d) if high_60d else None
        turnover_ratio = safe_ratio(avg_turnover_5d, avg_turnover_20d)
        limit_count_20d = int((changes.tail(20) >= 9.8).sum()) if not changes.empty else 0
        has_history = len(bars) >= 20
        previous_strength = bool(
            has_history
            and (
                (return_20d is not None and return_20d >= 12)
                or (return_20d is not None and return_20d >= 10 and limit_count_20d == 0)
                or (return_60d is not None and return_60d >= 25)
                or limit_count_20d >= 1
            )
        )
        result[str(code)] = {
            "has_history": has_history,
            "bars_count": len(bars),
            "return_5d_pct": return_5d,
            "return_10d_pct": return_10d,
            "return_20d_pct": return_20d,
            "return_60d_pct": return_60d,
            "high_10d": high_10d,
            "high_20d": high_20d,
            "high_60d": high_60d,
            "drawdown_from_20d_high_pct": drawdown_20,
            "drawdown_from_60d_high_pct": drawdown_60,
            "ma5": ma5,
            "ma10": ma10,
            "ma20": ma20,
            "avg_turnover_5d": avg_turnover_5d,
            "avg_turnover_20d": avg_turnover_20d,
            "turnover_ratio_5d": turnover_ratio,
            "limit_up_count_20d": limit_count_20d,
            "above_ma5": current_close >= ma5 if ma5 else False,
            "above_ma10": current_close >= ma10 if ma10 else False,
            "above_ma20": current_close >= ma20 if ma20 else False,
            "near_ma10": near_level(current_close, ma10, 0.04),
            "near_ma20": near_level(current_close, ma20, 0.05),
            "volume_confirm": turnover_ratio is not None and turnover_ratio >= 1.05,
            "breakout_10d": high_10d is not None and current_close >= high_10d * 0.99,
            "breakout_20d": high_20d is not None and current_close >= high_20d * 0.98,
            "previous_strength": previous_strength,
            "controlled_range": has_history and drawdown_20 is not None and -20 <= drawdown_20 <= -3,
            "first_wave": has_history and (
                (return_60d is not None and return_60d >= 25)
                or (return_20d is not None and return_20d >= 18)
                or limit_count_20d >= 2
            ),
        }
    return result


def moving_average(series: pd.Series, window: int) -> float | None:
    series = series.dropna()
    if len(series) < window:
        return None
    return float(series.tail(window).mean())


def window_return(series: pd.Series, window: int) -> float | None:
    series = series.dropna()
    if len(series) <= window:
        return None
    base = float(series.iloc[-window - 1])
    if base == 0:
        return None
    return (float(series.iloc[-1]) / base - 1) * 100


def pct_change(value: float, base: float | None) -> float | None:
    if base in (None, 0):
        return None
    return (value / float(base) - 1) * 100


def safe_ratio(value: float | None, base: float | None) -> float | None:
    if value is None or base in (None, 0):
        return None
    return value / base


def near_level(value: float, level: float | None, tolerance: float) -> bool:
    if level in (None, 0):
        return False
    return abs(value / float(level) - 1) <= tolerance


def build_limit_leaders(
    df: pd.DataFrame,
    hot_map: dict[str, int],
    limit_map: dict[str, int],
    market: MarketSnapshot,
    config: dict[str, Any],
    history_map: dict[str, dict[str, Any]] | None = None,
) -> list[Candidate]:
    if not limit_map:
        return []
    rows = df[df["code"].isin(limit_map.keys())].copy()
    if rows.empty:
        return []
    return score_candidates(rows, hot_map, limit_map, market, config, history_map)[:5]


def build_limit_platform_candidates(
    spot: pd.DataFrame,
    daily_bars: pd.DataFrame,
    trade_date: date,
    config: dict[str, Any],
) -> list[Candidate]:
    cfg = limit_platform_config(config)
    if not cfg["enabled"] or daily_bars.empty or spot.empty:
        return []

    work = prepare_pattern_bars(daily_bars, trade_date)
    if work.empty:
        return []

    spot_rows = {str(row["code"]): row for row in spot.to_dict("records")}
    candidates: list[Candidate] = []
    for code, bars in work[work["code"].isin(spot_rows.keys())].groupby("code"):
        match = find_limit_platform_match(bars, spot_rows[str(code)], cfg, trade_date)
        if not match:
            continue
        candidates.append(limit_platform_candidate(str(code), spot_rows[str(code)], match))

    candidates = sorted(candidates, key=lambda item: item.score, reverse=True)
    max_candidates = int(cfg["max_candidates"])
    return candidates if max_candidates <= 0 else candidates[:max_candidates]


def limit_platform_config(config: dict[str, Any]) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "enabled": True,
        "max_candidates": 0,
        "search_window_days": 45,
        "attack_min_window": 4,
        "attack_max_window": 10,
        "attack_extension_days": 2,
        "min_attack_return_pct": 20.0,
        "min_platform_days": 3,
        "max_platform_days": 15,
        "min_platform_avg_amplitude_pct": 8.0,
        "max_platform_drawdown_pct": 25.0,
        "min_close_to_prior_platform_low_ratio": 1.0,
        "max_attack_gain_retracement_pct": 80.0,
        "huge_turnover_rate_pct": 25.0,
        "huge_turnover_multiple": 2.5,
        "limit_up_threshold_pct": 9.8,
        "touch_limit_threshold_pct": 9.5,
        "confirm_close_ratio": 1.0,
    }
    raw = (config.get("pattern", {}) or {}).get("limit_platform", {}) or {}
    merged = defaults | raw
    merged["enabled"] = bool(merged.get("enabled", True))
    raw_max_candidates = merged.get("max_candidates")
    if raw_max_candidates in (None, ""):
        raw_max_candidates = defaults["max_candidates"]
    merged["max_candidates"] = max(0, int(float(raw_max_candidates)))
    for key in [
        "search_window_days",
        "attack_min_window",
        "attack_max_window",
        "attack_extension_days",
        "min_platform_days",
        "max_platform_days",
    ]:
        if key == "max_platform_days" and float(merged.get(key) or 0) <= 0:
            merged[key] = 0
        else:
            merged[key] = max(1, int(float(merged.get(key) or defaults[key])))
    for key in [
        "min_attack_return_pct",
        "min_platform_avg_amplitude_pct",
        "max_platform_drawdown_pct",
        "min_close_to_prior_platform_low_ratio",
        "max_attack_gain_retracement_pct",
        "huge_turnover_rate_pct",
        "huge_turnover_multiple",
        "limit_up_threshold_pct",
        "touch_limit_threshold_pct",
        "confirm_close_ratio",
    ]:
        merged[key] = float(merged.get(key) or defaults[key])
    return merged


def prepare_pattern_bars(df: pd.DataFrame, trade_date: date) -> pd.DataFrame:
    work = df.copy()
    if "trade_date" not in work.columns or "code" not in work.columns:
        return pd.DataFrame()
    work["trade_date"] = pd.to_datetime(work["trade_date"], errors="coerce")
    work = work.dropna(subset=["trade_date", "code"])
    work = work[work["trade_date"].dt.date <= trade_date]
    for column in ["open", "high", "low", "close", "change_pct", "turnover", "turnover_rate", "amplitude_pct"]:
        if column in work.columns:
            work[column] = pd.to_numeric(work[column], errors="coerce")
    work = work.dropna(subset=["close"]).sort_values(["code", "trade_date"])
    return fill_derived_bar_fields(work)


def fill_derived_bar_fields(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    if "high" not in work.columns:
        work["high"] = work["close"]
    if "low" not in work.columns:
        work["low"] = work["close"]
    work["high"] = work["high"].fillna(work["close"])
    work["low"] = work["low"].fillna(work["close"])
    if "change_pct" not in work.columns:
        work["change_pct"] = pd.NA
    if "amplitude_pct" not in work.columns:
        work["amplitude_pct"] = pd.NA

    filled: list[pd.DataFrame] = []
    for _, group in work.groupby("code", sort=False):
        group = group.sort_values("trade_date").copy()
        previous_close = group["close"].shift(1)
        derived_change = (group["close"] / previous_close - 1) * 100
        derived_amplitude = (group["high"] - group["low"]) / previous_close.replace(0, pd.NA) * 100
        group["change_pct"] = group["change_pct"].fillna(derived_change)
        group["amplitude_pct"] = group["amplitude_pct"].fillna(derived_amplitude)
        filled.append(group)
    return pd.concat(filled, ignore_index=True) if filled else work


def find_limit_platform_match(
    group: pd.DataFrame,
    spot_row: dict[str, Any],
    cfg: dict[str, Any],
    trade_date: date,
) -> dict[str, Any] | None:
    bars = group.sort_values("trade_date").tail(int(cfg["search_window_days"]) + 25).copy()
    if bars.empty or bars.iloc[-1]["trade_date"].date() != trade_date:
        return None
    bars["high"] = bars["high"].fillna(bars["close"]) if "high" in bars else bars["close"]
    bars["low"] = bars["low"].fillna(bars["close"]) if "low" in bars else bars["close"]
    bars["turnover"] = bars["turnover"].fillna(0) if "turnover" in bars else 0
    fallback_amplitude = (bars["high"] - bars["low"]) / bars["close"].replace(0, pd.NA) * 100
    if "amplitude_pct" not in bars:
        bars["amplitude_pct"] = fallback_amplitude
    else:
        bars["amplitude_pct"] = bars["amplitude_pct"].fillna(fallback_amplitude)

    search_start = max(0, len(bars) - int(cfg["search_window_days"]))
    min_platform_days = int(cfg["min_platform_days"])
    best: dict[str, Any] | None = None
    latest_index = len(bars) - 1
    last_attack_end = latest_index - min_platform_days
    for end in range(search_start + int(cfg["attack_min_window"]) - 1, last_attack_end + 1):
        for length in range(int(cfg["attack_min_window"]), int(cfg["attack_max_window"]) + 1):
            start = end - length + 1
            if start < search_start or start < 0:
                continue
            attack = evaluate_attack_window(bars, start, end, cfg)
            if not attack:
                continue
            attack = extend_attack_window(bars, attack, latest_index, cfg)
            platform = evaluate_platform_window(
                bars,
                int(attack["attack_end_index"]) + 1,
                latest_index,
                attack,
                cfg,
            )
            if not platform:
                continue
            match = attack | platform
            match["score_parts"] = limit_platform_score_parts(match, cfg)
            score = sum(match["score_parts"].values())
            if match["stage"] == TEXT["platform_watch"]:
                score = min(score, 78)
            match["score"] = max(0, min(100, round(score)))
            if best is None or limit_platform_rank(match) > limit_platform_rank(best):
                best = match
    return best


def evaluate_attack_window(
    bars: pd.DataFrame,
    start: int,
    end: int,
    cfg: dict[str, Any],
) -> dict[str, Any] | None:
    window = bars.iloc[start : end + 1]
    base_close = _optional_float(bars.iloc[start - 1]["close"]) if start > 0 else _optional_float(window.iloc[0].get("open"))
    end_close = _optional_float(window.iloc[-1]["close"])
    attack_return = pct_change(end_close, base_close) if end_close is not None else None
    if attack_return is None or attack_return < float(cfg["min_attack_return_pct"]):
        return None

    changes = pd.to_numeric(window["change_pct"], errors="coerce").fillna(0)
    limit_flags = [bool(item >= float(cfg["limit_up_threshold_pct"])) for item in changes.tolist()]
    if not limit_flags[0] and float(changes.iloc[0]) < 5:
        return None
    if not limit_flags[-1] and float(changes.iloc[-1]) < 5:
        return None
    limit_count = int(sum(limit_flags))
    max_streak = max_true_streak(limit_flags)
    window_len = len(window)
    if not attack_pattern_passes(window_len, limit_count, max_streak):
        return None

    attack_high = float(pd.to_numeric(window["high"], errors="coerce").fillna(window["close"]).max())
    return {
        "attack_start_index": start,
        "attack_end_index": end,
        "attack_start_date": window.iloc[0]["trade_date"].date(),
        "attack_end_date": window.iloc[-1]["trade_date"].date(),
        "attack_window_days": window_len,
        "attack_limit_count": limit_count,
        "attack_max_streak": max_streak,
        "attack_label": attack_label(window_len, limit_count, max_streak),
        "attack_return_pct": attack_return,
        "attack_base_close": base_close,
        "attack_high": attack_high,
    }


def attack_pattern_passes(window_len: int, limit_count: int, max_streak: int) -> bool:
    return bool(
        max_streak >= 3
        or (window_len <= 4 and limit_count >= 2)
        or (window_len <= 5 and limit_count >= 3)
        or (window_len <= 7 and limit_count >= 4)
        or (window_len <= 10 and limit_count >= 5)
    )


def attack_label(window_len: int, limit_count: int, max_streak: int) -> str:
    label = f"{window_len}\u5929{limit_count}\u677f"
    if max_streak >= 3:
        label += f"\uff0c\u6700\u9ad8{max_streak}\u8fde\u677f"
    return label


def extend_attack_window(
    bars: pd.DataFrame,
    attack: dict[str, Any],
    latest_index: int,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    end = int(attack["attack_end_index"])
    max_extension_days = int(cfg.get("attack_extension_days") or 0)
    max_end = min(
        end + max_extension_days,
        latest_index - int(cfg["min_platform_days"]),
    )
    attack_high = float(attack["attack_high"])
    extension_end = end
    for index in range(end + 1, max_end + 1):
        row = bars.iloc[index]
        high = _optional_float(row.get("high"))
        change_pct = _optional_float(row.get("change_pct"))
        if high is None or high < attack_high or change_pct is None or change_pct <= 0:
            break
        attack_high = max(attack_high, high)
        extension_end = index
    if extension_end == end:
        return attack
    extended = attack.copy()
    extended["attack_end_index"] = extension_end
    extended["attack_end_date"] = bars.iloc[extension_end]["trade_date"].date()
    extended["attack_high"] = attack_high
    extended["attack_extension_days"] = extension_end - end
    return extended


def max_true_streak(values: list[bool]) -> int:
    current = 0
    best = 0
    for value in values:
        current = current + 1 if value else 0
        best = max(best, current)
    return best


def evaluate_platform_window(
    bars: pd.DataFrame,
    start: int,
    end: int,
    attack: dict[str, Any],
    cfg: dict[str, Any],
) -> dict[str, Any] | None:
    platform = bars.iloc[start : end + 1]
    if len(platform) < int(cfg["min_platform_days"]):
        return None
    if int(cfg["max_platform_days"]) > 0 and len(platform) > int(cfg["max_platform_days"]):
        return None
    first_platform_change = _optional_float(platform.iloc[0].get("change_pct")) or 0.0
    if first_platform_change >= float(cfg["limit_up_threshold_pct"]):
        return None
    avg_amplitude = float(pd.to_numeric(platform["amplitude_pct"], errors="coerce").dropna().mean())
    early_avg_amplitude = float(
        pd.to_numeric(platform.head(int(cfg["min_platform_days"]))["amplitude_pct"], errors="coerce").dropna().mean()
    )
    if pd.isna(avg_amplitude) or avg_amplitude < float(cfg["min_platform_avg_amplitude_pct"]):
        return None
    if pd.isna(early_avg_amplitude) or early_avg_amplitude < float(cfg["min_platform_avg_amplitude_pct"]):
        return None

    attack_high = float(attack["attack_high"])
    current = platform.iloc[-1]
    current_close = _optional_float(current.get("close")) or 0
    current_high = _optional_float(current.get("high")) or current_close
    prior_platform = platform.iloc[:-1]
    prior_platform_low = (
        float(pd.to_numeric(prior_platform["low"], errors="coerce").fillna(prior_platform["close"]).min())
        if not prior_platform.empty
        else None
    )
    if (
        prior_platform_low is not None
        and current_close < prior_platform_low * float(cfg["min_close_to_prior_platform_low_ratio"])
    ):
        return None
    platform_low = float(pd.to_numeric(platform["low"], errors="coerce").fillna(platform["close"]).min())
    max_drawdown = max(0.0, (attack_high - platform_low) / attack_high * 100) if attack_high else 0.0
    if max_drawdown > float(cfg["max_platform_drawdown_pct"]):
        return None
    attack_base_close = float(attack["attack_base_close"])
    attack_gain = attack_high - attack_base_close
    gain_retracement = max(0.0, (attack_high - platform_low) / attack_gain * 100) if attack_gain > 0 else 0.0
    if gain_retracement > float(cfg["max_attack_gain_retracement_pct"]):
        return None

    combined = bars.iloc[int(attack["attack_start_index"]) : end + 1]
    max_turnover_rate = optional_series_max(combined.get("turnover_rate"))
    baseline_turnover = baseline_turnover_amount(bars, int(attack["attack_start_index"]))
    max_turnover = optional_series_max(combined.get("turnover"))
    avg_platform_turnover = optional_series_mean(platform.get("turnover"))
    turnover_multiple = safe_ratio(max_turnover, baseline_turnover)
    platform_turnover_multiple = safe_ratio(avg_platform_turnover, baseline_turnover)
    huge_by_rate = max_turnover_rate is not None and max_turnover_rate >= float(cfg["huge_turnover_rate_pct"])
    huge_by_amount = turnover_multiple is not None and turnover_multiple >= float(cfg["huge_turnover_multiple"])
    if not (huge_by_rate or huge_by_amount):
        return None

    confirm_price = attack_high * float(cfg["confirm_close_ratio"])
    stage = TEXT["platform_confirm"] if current_close >= confirm_price else TEXT["platform_watch"]
    touch_count = count_touch_limit_days(bars, start, end, float(cfg["touch_limit_threshold_pct"]))
    return {
        "platform_start_date": platform.iloc[0]["trade_date"].date(),
        "platform_days": len(platform),
        "platform_avg_amplitude_pct": avg_amplitude,
        "max_drawdown_pct": max_drawdown,
        "attack_gain_retracement_pct": gain_retracement,
        "max_turnover_rate_pct": max_turnover_rate,
        "turnover_multiple": turnover_multiple,
        "platform_turnover_multiple": platform_turnover_multiple,
        "platform_support_price": prior_platform_low if prior_platform_low is not None else platform_low,
        "touch_limit_count": touch_count,
        "stage": stage,
        "current_close": current_close,
        "current_high": current_high,
    }


def limit_platform_rank(match: dict[str, Any]) -> tuple[int, int, int, float]:
    return (
        int(match.get("score") or 0),
        int(match.get("attack_limit_count") or 0),
        int(match.get("attack_window_days") or 0),
        float(match.get("attack_return_pct") or 0),
    )


def optional_series_max(series: Any) -> float | None:
    if series is None:
        return None
    values = pd.to_numeric(series, errors="coerce").dropna()
    return None if values.empty else float(values.max())


def optional_series_mean(series: Any) -> float | None:
    if series is None:
        return None
    values = pd.to_numeric(series, errors="coerce").dropna()
    return None if values.empty else float(values.mean())


def baseline_turnover_amount(bars: pd.DataFrame, attack_start: int) -> float | None:
    before = pd.to_numeric(bars.iloc[max(0, attack_start - 20) : attack_start].get("turnover"), errors="coerce").dropna()
    if before.empty:
        before = pd.to_numeric(bars.get("turnover"), errors="coerce").dropna()
    if before.empty:
        return None
    value = float(before.mean())
    return value if value > 0 else None


def count_touch_limit_days(bars: pd.DataFrame, start: int, end: int, threshold_pct: float) -> int:
    count = 0
    for index in range(start, end + 1):
        if index <= 0:
            continue
        previous_close = _optional_float(bars.iloc[index - 1].get("close"))
        high = _optional_float(bars.iloc[index].get("high"))
        low = _optional_float(bars.iloc[index].get("low"))
        if not previous_close:
            continue
        touched_up = high is not None and pct_change(high, previous_close) >= threshold_pct
        touched_down = low is not None and pct_change(low, previous_close) <= -threshold_pct
        if touched_up or touched_down:
            count += 1
    return count


def limit_platform_score_parts(match: dict[str, Any], cfg: dict[str, Any]) -> dict[str, int]:
    attack_score = min(
        30,
        8
        + int(match["attack_limit_count"]) * 3
        + int(match["attack_max_streak"]) * 3
        + round(float(match["attack_return_pct"]) / 5),
    )
    rate_score = (
        float(match["max_turnover_rate_pct"]) / float(cfg["huge_turnover_rate_pct"]) * 16
        if match.get("max_turnover_rate_pct") is not None
        else 0
    )
    amount_score = (
        float(match["turnover_multiple"]) / float(cfg["huge_turnover_multiple"]) * 16
        if match.get("turnover_multiple") is not None
        else 0
    )
    volume_score = min(20, round(max(rate_score, amount_score)))
    platform_volume_bonus = max(0, min(5, round(((match.get("platform_turnover_multiple") or 1) - 1) * 4)))
    platform_score = min(
        20,
        round(float(match["platform_avg_amplitude_pct"]) / float(cfg["min_platform_avg_amplitude_pct"]) * 10)
        + platform_volume_bonus
        + min(3, int(match["touch_limit_count"])),
    )
    drawdown = float(match["max_drawdown_pct"])
    if 8 <= drawdown <= 18:
        drawdown_score = 15
    elif drawdown < 8:
        drawdown_score = max(8, round(15 - (8 - drawdown) * 0.7))
    else:
        drawdown_score = max(6, round(15 - (drawdown - 18) / max(float(cfg["max_platform_drawdown_pct"]) - 18, 1) * 9))
    stage_score = 15 if match["stage"] == TEXT["platform_confirm"] else 8
    return {
        "attack_strength": int(attack_score),
        "huge_volume": int(volume_score),
        "platform_quality": int(platform_score),
        "drawdown_control": int(drawdown_score),
        "stage_confirm": int(stage_score),
    }


def limit_platform_candidate(code: str, row: dict[str, Any], match: dict[str, Any]) -> Candidate:
    close = _optional_float(row.get("close")) or float(match["current_close"])
    change_pct = _optional_float(row.get("change_pct")) or 0.0
    turnover = _optional_float(row.get("turnover")) or 0.0
    tags = [TEXT["limit_platform"], str(match["stage"])]
    return Candidate(
        code=code,
        name=str(row.get("name") or code),
        close=close,
        change_pct=change_pct,
        turnover=turnover,
        volume_ratio=_optional_float(row.get("volume_ratio")),
        amplitude_pct=_optional_float(row.get("amplitude_pct")),
        hot_rank=None,
        limit_up_days=int(match.get("attack_max_streak") or 0) or None,
        strategy_tags=tags,
        score=int(match["score"]),
        score_parts=match["score_parts"],
        trigger=build_limit_platform_trigger(match),
        invalidation=build_limit_platform_invalidation(match),
        reasons=build_limit_platform_reasons(match),
        raw={"pattern_type": "limit_platform_wash", **match},
    )


def build_limit_platform_trigger(match: dict[str, Any]) -> str:
    attack_high = float(match["attack_high"])
    if match["stage"] == TEXT["platform_confirm"]:
        return f"\u6536\u590d\u524d\u6bb5\u9ad8\u70b9 {attack_high:.2f} \u9644\u8fd1\uff0c\u6b21\u65e5\u4e0d\u8dcc\u56de\u5e73\u53f0\u4e2d\u8f74\u4e14\u653e\u91cf\u627f\u63a5\u518d\u89c2\u5bdf\u3002"
    return f"\u5e73\u53f0\u7ee7\u7eed\u653e\u91cf\u9707\u8361\uff0c\u6536\u76d8\u7ad9\u4e0a {attack_high:.2f} \u540e\u8f6c\u5165\u786e\u8ba4\u89c2\u5bdf\u3002"


def build_limit_platform_invalidation(match: dict[str, Any]) -> str:
    attack_high = float(match["attack_high"])
    invalid_price = float(
        match.get("platform_support_price")
        or attack_high * (1 - float(match["max_drawdown_pct"]) / 100)
    )
    return f"\u8dcc\u7834\u5e73\u53f0\u4f4e\u70b9 {invalid_price:.2f} \u6216\u8ddd\u524d\u6bb5\u9ad8\u70b9\u56de\u64a4\u6269\u5927\u81f3 25% \u4ee5\u4e0a\uff0c\u5219\u89c2\u5bdf\u5931\u6548\u3002"


def build_limit_platform_reasons(match: dict[str, Any]) -> list[str]:
    return [
        f"\u524d\u6bb5\u5f62\u6001: {match['attack_label']}\uff0c\u533a\u95f4\u6da8\u5e45 {format_pct(match['attack_return_pct'])}",
        f"\u5e73\u53f0: {match['platform_days']}\u65e5\u9707\u8361\uff0c\u65e5\u5747\u632f\u5e45 {format_pct(match['platform_avg_amplitude_pct'])}\uff0c\u6700\u5927\u56de\u64a4 {format_pct(-float(match['max_drawdown_pct']))}\uff0c\u524d\u6bb5\u6da8\u5e45\u56de\u5410 {format_pct(match['attack_gain_retracement_pct'])}",
        f"\u6478\u677f\u5929\u6570: {match['touch_limit_count']}\uff0c\u9636\u6bb5: {match['stage']}",
    ]


def score_candidates(
    df: pd.DataFrame,
    hot_map: dict[str, int],
    limit_map: dict[str, int],
    market: MarketSnapshot,
    config: dict[str, Any],
    history_map: dict[str, dict[str, Any]] | None = None,
) -> list[Candidate]:
    min_turnover = float(config.get("market", {}).get("min_turnover_amount", 300_000_000))
    max_candidates = int(config.get("market", {}).get("max_candidates", 8))

    pool = df.loc[df["turnover"] >= min_turnover].copy()
    if pool.empty:
        pool = df.sort_values("turnover", ascending=False).head(max_candidates * 3).copy()

    candidates: list[Candidate] = []
    for row in pool.to_dict("records"):
        code = str(row["code"])
        hot_rank = hot_map.get(code)
        limit_days = limit_map.get(code)
        history = (history_map or {}).get(code, {"has_history": False})
        tags = strategy_tags(row, hot_rank, limit_days, history)
        if not tags:
            continue

        parts = score_parts(row, hot_rank, limit_days, market, tags, history)
        score = weighted_score(parts)
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
                trigger=build_trigger(row, tags, history),
                invalidation=build_invalidation(row, history),
                reasons=build_reasons(row, hot_rank, limit_days, tags, history),
                raw=row,
            )
        )

    return sorted(candidates, key=lambda item: item.score, reverse=True)[:max_candidates]


DEFAULT_SCORING_WEIGHTS = {
    "market_environment": 0.15,
    "leader_strength": 0.25,
    "historical_shape": 0.30,
    "intraday_confirmation": 0.20,
    "liquidity_risk": 0.10,
}


def weighted_score(parts: dict[str, int]) -> int:
    max_scores = {
        "market_environment": 15,
        "leader_strength": 25,
        "historical_shape": 30,
        "intraday_confirmation": 20,
        "liquidity_risk": 10,
    }
    score = 0.0
    for key, max_score in max_scores.items():
        raw = max(0, min(max_score, parts.get(key, 0)))
        score += (raw / max_score) * DEFAULT_SCORING_WEIGHTS[key] * 100
    return max(0, min(100, round(score)))


def strategy_tags(
    row: dict[str, Any],
    hot_rank: int | None,
    limit_days: int | None,
    history: dict[str, Any] | None = None,
) -> list[str]:
    change = float(row.get("change_pct") or 0)
    turnover_rate = _optional_float(row.get("turnover_rate"))
    volume_ratio = _optional_float(row.get("volume_ratio"))
    turnover = float(row.get("turnover") or 0)
    tags: list[str] = []

    is_hot = hot_rank is not None and hot_rank <= 80
    is_limit_leader = limit_days is not None and limit_days >= 1
    is_liquid = turnover >= 300_000_000
    history = history or {}
    has_history = bool(history.get("has_history"))
    if is_liquid and is_limit_leader:
        tags.append(TEXT["limit_strength"])
    if is_liquid and is_hot:
        tags.append(TEXT["hot_rank"])
    if has_history and history.get("volume_confirm") and change >= 0:
        tags.append(TEXT["volume"])
    if turnover_rate and volume_ratio and turnover_rate >= 5 and volume_ratio >= 1.2 and 0 <= change <= 7:
        tags.append(TEXT["volume"])
    return list(dict.fromkeys(tags))


def score_parts(
    row: dict[str, Any],
    hot_rank: int | None,
    limit_days: int | None,
    market: MarketSnapshot,
    tags: list[str],
    history: dict[str, Any] | None = None,
) -> dict[str, int]:
    breadth = market.up_count / market.total_count if market.total_count else 0
    market_score = min(15, max(0, round(5 + breadth * 8 + market.limit_up_count / 30)))

    leader = 5
    if hot_rank is not None:
        leader += max(0, 14 - hot_rank // 8)
    if limit_days is not None:
        leader += min(11, 5 + limit_days * 3)
    if hot_rank is None and limit_days is None:
        leader += min(12, round(float(row.get("turnover") or 0) / 500_000_000 * 3))
    leader = min(25, leader)

    history = history or {}
    if history.get("has_history"):
        shape = 6
        shape += 6 if history.get("previous_strength") else 0
        shape += 5 if history.get("controlled_range") else 0
        shape += 5 if history.get("above_ma10") else 0
        shape += 4 if history.get("volume_confirm") else 0
        shape += 5 if history.get("breakout_10d") or history.get("breakout_20d") else 0
        shape = min(30, shape)
    else:
        shape = 6

    change = float(row.get("change_pct") or 0)
    volume_ratio = _optional_float(row.get("volume_ratio")) or 0
    amplitude = _optional_float(row.get("amplitude_pct")) or 0
    intraday = 8 + len(tags) * 3 + min(4, volume_ratio)
    intraday -= max(0, change - 7)
    intraday -= amplitude * 0.15
    intraday = max(0, min(20, round(intraday)))

    turnover = float(row.get("turnover") or 0)
    liquidity = min(10, round(turnover / 500_000_000 * 4 + 4))

    return {
        "market_environment": market_score,
        "leader_strength": leader,
        "historical_shape": shape,
        "intraday_confirmation": intraday,
        "liquidity_risk": liquidity,
    }


def build_reasons(
    row: dict[str, Any],
    hot_rank: int | None,
    limit_days: int | None,
    tags: list[str],
    history: dict[str, Any] | None = None,
) -> list[str]:
    reasons = [f"{TEXT['strategy']}: {'/'.join(tags)}"]
    if hot_rank is not None:
        reasons.append(f"{TEXT['hot_rank']}: {hot_rank}")
    if limit_days is not None:
        reasons.append(f"{TEXT['limit_strength']}: {limit_days}")
    reasons.append(f"{TEXT['turnover']}: {format_amount(float(row.get('turnover') or 0))}")
    history = history or {}
    if history.get("has_history"):
        reasons.append(f"{TEXT['return_20d']}: {format_pct(history.get('return_20d_pct'))}")
        reasons.append(f"{TEXT['drawdown']}: {format_pct(history.get('drawdown_from_20d_high_pct'))}")
        reasons.append(f"{TEXT['ma_state']}: {ma_state_text(history)}")
        reasons.append(f"{TEXT['turnover_change']}: {format_ratio(history.get('turnover_ratio_5d'))}")
    else:
        reasons.append(TEXT["history_missing"])
    return reasons


def build_trigger(row: dict[str, Any], tags: list[str], history: dict[str, Any] | None = None) -> str:
    close = float(row["close"])
    return f"\u6b21\u65e5\u9ad8\u5f00\u4e0d\u8d85 3%\uff0c\u653e\u91cf\u7a81\u7834 {close * 1.02:.2f} \u540e\u89c2\u5bdf\u3002"


def build_invalidation(row: dict[str, Any], history: dict[str, Any] | None = None) -> str:
    close = float(row["close"])
    ma20 = _optional_float((history or {}).get("ma20"))
    anchor = ma20 if ma20 else close * 0.95
    return f"\u8dcc\u7834 {anchor:.2f} \u6216\u677f\u5757\u6838\u5fc3\u80a1\u660e\u663e\u8d70\u5f31\uff0c\u5219\u89c2\u5bdf\u5931\u6548\u3002"


def format_amount(amount: float) -> str:
    if amount >= 100_000_000:
        return f"{amount / 100_000_000:.1f}\u4ebf"
    if amount >= 10_000:
        return f"{amount / 10_000:.0f}\u4e07"
    return f"{amount:.0f}"


def format_pct(value: Any) -> str:
    number = _optional_float(value)
    return "\u6682\u7f3a" if number is None else f"{number:.1f}%"


def format_ratio(value: Any) -> str:
    number = _optional_float(value)
    return "\u6682\u7f3a" if number is None else f"{number:.2f}x"


def ma_state_text(history: dict[str, Any]) -> str:
    states = []
    if history.get("above_ma5"):
        states.append("\u7ad95\u65e5\u7ebf")
    if history.get("above_ma10"):
        states.append("\u7ad910\u65e5\u7ebf")
    if history.get("above_ma20"):
        states.append("\u7ad920\u65e5\u7ebf")
    return "/".join(states) if states else "\u5747\u7ebf\u4e0b\u65b9"


def _optional_float(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
