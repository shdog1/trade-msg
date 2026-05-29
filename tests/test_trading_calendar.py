from __future__ import annotations

from datetime import date, datetime, time
import unittest

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


if __name__ == "__main__":
    unittest.main()
