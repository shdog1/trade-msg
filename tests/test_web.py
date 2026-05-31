from __future__ import annotations

import unittest

from src.web import build_backfill_command, split_codes, update_config


class WebConsoleTest(unittest.TestCase):
    def test_update_config_converts_yi_and_percent_inputs(self) -> None:
        config = {"app": {}, "market": {}, "scoring": {}}
        form = {
            "report_time": ["18:30"],
            "data_ready_time": ["09:00"],
            "skip_non_trading_day": ["on"],
            "max_candidates": ["10"],
            "min_turnover_yi": ["3.5"],
            "market_environment": ["15"],
            "leader_strength": ["25"],
            "historical_shape": ["30"],
            "intraday_confirmation": ["20"],
            "liquidity_risk": ["10"],
        }

        update_config(config, form)

        self.assertEqual(config["market"]["min_turnover_amount"], 350_000_000)
        self.assertEqual(config["scoring"]["historical_shape"], 0.30)
        self.assertEqual(config["app"]["report_time"], "18:30")

    def test_split_codes_accepts_comma_space_and_newline(self) -> None:
        self.assertEqual(split_codes("600001, 000001\n002001"), ["600001", "000001", "002001"])

    def test_backfill_command_includes_multiple_stock_codes(self) -> None:
        command = build_backfill_command(
            {
                "backfill_days": ["250"],
                "backfill_sleep": ["1.5"],
                "backfill_stocks": ["600001,000001"],
            }
        )

        self.assertIn("--backfill-stock", command)
        self.assertIn("600001", command)
        self.assertIn("000001", command)


if __name__ == "__main__":
    unittest.main()
