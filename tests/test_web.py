from __future__ import annotations

import unittest

from src.web import (
    build_backfill_command,
    build_backfill_limit_pool_command,
    limit_color,
    render_limit_ladder,
    render_limit_ladder_chart,
    render_price_volume_svg,
    split_codes,
    update_config,
)


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

    def test_backfill_limit_pool_command_uses_days_and_sleep(self) -> None:
        command = build_backfill_limit_pool_command(
            {
                "limit_pool_days": ["90"],
                "limit_pool_sleep": ["1.0"],
            }
        )

        self.assertIn("--backfill-limit-pool-days", command)
        self.assertIn("90", command)
        self.assertIn("--limit-pool-sleep", command)
        self.assertIn("1.0", command)

    def test_price_volume_svg_renders_close_and_volume(self) -> None:
        svg = render_price_volume_svg(
            [
                {"close_price": 10, "turnover": 100},
                {"close_price": 11, "turnover": 180},
                {"close_price": 10.5, "turnover": 120},
            ]
        )

        self.assertIn("<svg", svg)
        self.assertIn("10.50", svg)
        self.assertIn("<rect", svg)

    def test_limit_ladder_renders_highest_streak_rows(self) -> None:
        content = render_limit_ladder(
            [
                {"code": "600001", "name": "Sample A", "max_limit_up_days": 5, "reached_at": "2026-05-29"},
                {"code": "000001", "name": "Sample B", "max_limit_up_days": 3, "reached_at": "2026-05-20"},
            ]
        )

        self.assertIn("600001", content)
        self.assertIn("5", content)
        self.assertIn("ladder-table", content)
        self.assertIn("ladder-badge", content)

    def test_limit_ladder_uses_red_depth_palette(self) -> None:
        self.assertEqual(limit_color(2), "#fee2e2")
        self.assertEqual(limit_color(10), "#7f1d1d")

    def test_limit_ladder_chart_renders_connected_points(self) -> None:
        content = render_limit_ladder_chart(
            [
                {"trade_date": "2026-05-27", "max_limit_up_days": 3, "names": "A"},
                {"trade_date": "2026-05-28", "max_limit_up_days": 5, "names": "B"},
                {"trade_date": "2026-05-29", "max_limit_up_days": 4, "names": "C"},
            ]
        )

        self.assertIn("<polyline", content)
        self.assertIn("5板", content)
        self.assertIn("90日连板天梯图", content)

    def test_limit_ladder_chart_stacks_multiple_leaders_above_point(self) -> None:
        content = render_limit_ladder_chart(
            [
                {"trade_date": "2026-05-26", "max_limit_up_days": 3, "names": "A"},
                {
                    "trade_date": "2026-05-27",
                    "max_limit_up_days": 8,
                    "leaders": [
                        {"code": "600001", "name": "一号股份"},
                        {"code": "600002", "name": "二号股份"},
                        {"code": "600003", "name": "三号股份"},
                    ],
                },
                {"trade_date": "2026-05-28", "max_limit_up_days": 4, "names": "C"},
            ]
        )

        self.assertIn("一号股份", content)
        self.assertIn("二号股份", content)
        self.assertIn("三号股份", content)
        self.assertNotIn("<rect x=", content)
        self.assertIn('font-size="9"', content)


if __name__ == "__main__":
    unittest.main()
