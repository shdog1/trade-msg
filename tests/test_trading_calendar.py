from __future__ import annotations

from datetime import date, datetime, time
import unittest
from unittest.mock import patch

from src.cli import should_skip_scheduled_run
from src.trading_calendar import latest_closed_trade_date


class TradingCalendarTest(unittest.TestCase):
    def test_before_ready_time_uses_previous_trade_date(self) -> None:
        trade_dates = {date(2026, 5, 29), date(2026, 6, 1)}

        result = latest_closed_trade_date(
            datetime(2026, 6, 1, 8, 59),
            trade_dates=trade_dates,
            data_ready_time=time(9, 0),
        )

        self.assertEqual(result, date(2026, 5, 29))

    def test_weekend_uses_previous_friday(self) -> None:
        trade_dates = {date(2026, 5, 29), date(2026, 6, 1)}

        result = latest_closed_trade_date(
            datetime(2026, 5, 30, 0, 5),
            trade_dates=trade_dates,
            data_ready_time=time(9, 0),
        )

        self.assertEqual(result, date(2026, 5, 29))

    def test_after_9am_uses_same_trade_date(self) -> None:
        trade_dates = {date(2026, 5, 29)}

        result = latest_closed_trade_date(
            datetime(2026, 5, 29, 9, 1),
            trade_dates=trade_dates,
            data_ready_time=time(9, 0),
        )

        self.assertEqual(result, date(2026, 5, 29))

    def test_scheduled_run_skips_non_trading_day_from_database_calendar(self) -> None:
        store = FakeCalendarStore({date(2026, 5, 29), date(2026, 6, 1)})
        settings = FakeSettings()

        with patch("src.cli.now_in_timezone", return_value=datetime(2026, 5, 30, 18, 0)):
            self.assertTrue(should_skip_scheduled_run(store, settings))

    def test_scheduled_run_allows_trading_day_from_database_calendar(self) -> None:
        store = FakeCalendarStore({date(2026, 5, 29), date(2026, 6, 1)})
        settings = FakeSettings()

        with patch("src.cli.now_in_timezone", return_value=datetime(2026, 6, 1, 18, 0)):
            self.assertFalse(should_skip_scheduled_run(store, settings))


class FakeSettings:
    raw = {"app": {"timezone": "Asia/Shanghai", "skip_non_trading_day": True}}


class FakeCalendarStore:
    def __init__(self, dates: set[date]):
        self.dates = dates

    def list_trade_dates(self) -> set[date]:
        return self.dates

    def persist_trade_calendar(self, trade_dates: set[date]) -> int:
        self.dates = trade_dates
        return len(trade_dates)


if __name__ == "__main__":
    unittest.main()
