from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd


def get_timezone(name: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return None


def now_in_timezone(name: str) -> datetime:
    timezone = get_timezone(name)
    if timezone:
        return datetime.now(timezone)
    return datetime.now()


def latest_closed_trade_date(
    now: datetime,
    trade_dates: set[date] | None = None,
    close_ready_time: time = time(18, 0),
) -> date:
    calendar = trade_dates or fallback_trade_dates(now.date())
    current = now.date()
    if current in calendar and now.time() >= close_ready_time:
        return current

    cursor = current - timedelta(days=1)
    while cursor not in calendar:
        cursor -= timedelta(days=1)
    return cursor


def fallback_trade_dates(anchor: date) -> set[date]:
    start = anchor - timedelta(days=370)
    end = anchor + timedelta(days=30)
    return {
        start + timedelta(days=offset)
        for offset in range((end - start).days + 1)
        if (start + timedelta(days=offset)).weekday() < 5
    }


@lru_cache(maxsize=1)
def load_trade_dates_from_akshare() -> set[date]:
    import akshare as ak

    df = ak.tool_trade_date_hist_sina()
    if not isinstance(df, pd.DataFrame) or df.empty:
        return set()

    date_col = next((col for col in df.columns if "date" in str(col).lower() or "\u65e5\u671f" in str(col)), df.columns[0])
    result: set[date] = set()
    for value in df[date_col].dropna().tolist():
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            continue
        result.add(parsed.date())
    return result


def resolve_trade_date_from_config(config: dict[str, Any]) -> date:
    app = config.get("app", {})
    timezone = str(app.get("timezone", "Asia/Shanghai"))
    close_time_text = str(app.get("data_ready_time", app.get("report_time", "18:00")))
    close_ready_time = parse_time(close_time_text)
    now = now_in_timezone(timezone)

    try:
        trade_dates = load_trade_dates_from_akshare()
    except Exception:  # noqa: BLE001 - calendar is helpful but not mandatory.
        trade_dates = fallback_trade_dates(now.date())
    if not trade_dates:
        trade_dates = fallback_trade_dates(now.date())

    return latest_closed_trade_date(now, trade_dates, close_ready_time)


def parse_time(value: str) -> time:
    hour_text, minute_text = value.split(":", maxsplit=1)
    return time(hour=int(hour_text), minute=int(minute_text))

