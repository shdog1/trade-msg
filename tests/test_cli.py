from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import Mock, patch

import pandas as pd

from src.cli import backfill_daily_bars, daily_update_config, refresh_limit_up_reasons, run_daily_job
from src.config import Settings
from src.market_data import MarketData


class CliTest(unittest.TestCase):
    def test_limit_reasons_cover_every_stock_in_daily_limit_pool(self) -> None:
        trade_date = date(2026, 6, 26)
        store = Mock()
        store.list_limit_pool_codes.return_value = ["600001", "600002"]
        store.missing_limit_reason_codes.return_value = ["600001", "600002"]
        store.persist_limit_up_reasons.return_value = 2
        provider = Mock()
        provider.fetch_limit_up_reasons.return_value = [
            {"as_of_date": trade_date, "code": "600001", "reason": "原因A"},
            {"as_of_date": trade_date, "code": "600002", "reason": "原因B"},
        ]

        with patch("src.cli.AkshareMarketProvider", return_value=provider):
            count = refresh_limit_up_reasons(store, trade_date)

        self.assertEqual(count, 2)
        provider.fetch_limit_up_reasons.assert_called_once_with(["600001", "600002"], trade_date)
        store.sync_limit_up_reasons_to_pool.assert_called_once_with(trade_date)
    def test_daily_update_config_defaults(self) -> None:
        config = daily_update_config(Settings(raw={}))

        self.assertEqual(config["daily_bar_days"], 10)
        self.assertEqual(config["daily_bar_sleep"], 0.2)
        self.assertFalse(config["daily_bar_include_all"])
        self.assertEqual(config["limit_pool_days"], 1)
        self.assertEqual(config["limit_pool_sleep"], 0.5)

    def test_daily_update_config_reads_overrides(self) -> None:
        config = daily_update_config(
            Settings(
                raw={
                    "daily_update": {
                        "daily_bar_days": 3,
                        "daily_bar_sleep": 0,
                        "daily_bar_include_all": True,
                        "limit_pool_days": 20,
                        "limit_pool_sleep": 1.2,
                    }
                }
            )
        )

        self.assertEqual(config["daily_bar_days"], 3)
        self.assertEqual(config["daily_bar_sleep"], 0.0)
        self.assertTrue(config["daily_bar_include_all"])
        self.assertEqual(config["limit_pool_days"], 20)
        self.assertEqual(config["limit_pool_sleep"], 1.2)

    def test_windows_task_script_uses_daily_job(self) -> None:
        content = __import__("pathlib").Path("scripts/install_windows_task.ps1").read_text(encoding="utf-8")

        self.assertIn("--daily-job --send --scheduled", content)
        self.assertIn("ExecutionTimeLimitMinutes", content)

    def test_daily_job_batches_daily_bars_from_spot(self) -> None:
        trade_date = date(2026, 6, 8)
        spot = pd.DataFrame(
            [
                {
                    "code": "600001",
                    "name": "Sample",
                    "close": 10.8,
                    "change_pct": 4.2,
                    "turnover": 500000000,
                }
            ]
        )
        data = MarketData(
            spot=spot,
            hot_rank=pd.DataFrame(),
            limit_pool=pd.DataFrame(),
            indexes=pd.DataFrame(),
            industries=pd.DataFrame(),
            concepts=pd.DataFrame(),
            trade_date=trade_date,
            warnings=[],
        )
        store = Mock()
        store.persist_daily_bars_from_spot.return_value = 1
        store.record_fetch_run.return_value = None

        with (
            patch("src.cli.fetch_market_snapshot", return_value=data),
            patch("src.cli.backfill_daily_bars") as backfill_daily_bars,
            patch("src.cli.backfill_limit_pool", return_value=0) as backfill_limit_pool,
            patch("src.cli.build_render_and_notify", return_value=0),
        ):
            result = run_daily_job(store, Settings(raw={}), trade_date, send=False, dry_run=True)

        self.assertEqual(result, 0)
        backfill_daily_bars.assert_not_called()
        self.assertEqual(backfill_limit_pool.call_args.kwargs["days"], 1)
        args = store.persist_daily_bars_from_spot.call_args.args
        self.assertEqual(args[0], trade_date)
        self.assertIs(args[1], spot)

    def test_backfill_specific_stock_refreshes_existing_bars(self) -> None:
        trade_date = date(2026, 6, 8)
        store = Mock()
        store.list_trade_dates.return_value = [trade_date]
        store.latest_daily_bar_date.return_value = trade_date
        store.list_daily_bar_dates.return_value = {trade_date}
        store.persist_daily_bars.return_value = 1
        store.record_fetch_run.return_value = None
        provider = Mock()
        provider.fetch_daily_bars.return_value = (pd.DataFrame([{"date": trade_date, "close": 10.8}]), "eastmoney")

        with (
            patch("src.cli.AkshareMarketProvider", return_value=provider),
            patch("src.cli.sleep", return_value=None),
        ):
            result = backfill_daily_bars(
                store,
                Settings(raw={}),
                trade_date,
                250,
                ["001359"],
                0,
                include_all=False,
            )

        self.assertEqual(result, 0)
        provider.fetch_daily_bars.assert_called_once()
        call = provider.fetch_daily_bars.call_args.args
        self.assertEqual(call[0], "001359")
        self.assertLess(call[1], trade_date)
        self.assertEqual(call[2], trade_date)


if __name__ == "__main__":
    unittest.main()
