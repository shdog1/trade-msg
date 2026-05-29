from __future__ import annotations

from datetime import date
import unittest

import pandas as pd

from src.analysis import build_recap, filter_main_board, normalize_spot
from src.market_data import MarketData
from src.report import render_report


CONFIG = {
    "market": {
        "main_board_prefixes": ["600", "601", "603", "605", "000", "001", "002", "003"],
        "exclude_name_keywords": ["ST", "*ST", "退"],
        "min_turnover_amount": 100_000_000,
        "max_candidates": 3,
    }
}


class AnalysisTest(unittest.TestCase):
    def test_filter_main_board_excludes_non_main_and_st(self) -> None:
        df = normalize_spot(
            pd.DataFrame(
                [
                    {"代码": "600001", "名称": "主板A", "最新价": 10, "涨跌幅": 1, "成交额": 2},
                    {"代码": "300001", "名称": "创业A", "最新价": 10, "涨跌幅": 1, "成交额": 2},
                    {"代码": "000001", "名称": "ST样本", "最新价": 10, "涨跌幅": 1, "成交额": 2},
                ]
            )
        )

        filtered = filter_main_board(df, CONFIG)

        self.assertEqual(filtered["code"].tolist(), ["600001"])

    def test_normalize_spot_strips_exchange_prefix(self) -> None:
        df = normalize_spot(
            pd.DataFrame(
                [
                    {"代码": "sh600001", "名称": "主板A", "最新价": 10, "涨跌幅": 1, "成交额": 2},
                    {"代码": "sz000001", "名称": "主板B", "最新价": 9, "涨跌幅": -1, "成交额": 3},
                ]
            )
        )

        self.assertEqual(df["code"].tolist(), ["600001", "000001"])

    def test_build_recap_scores_candidates(self) -> None:
        data = MarketData(
            spot=pd.DataFrame(
                [
                    {
                        "代码": "600001",
                        "名称": "龙头样本",
                        "最新价": 12.3,
                        "涨跌幅": 4.2,
                        "成交额": 800_000_000,
                        "量比": 1.6,
                        "振幅": 6.1,
                        "换手率": 8.2,
                    },
                    {
                        "代码": "002001",
                        "名称": "低吸样本",
                        "最新价": 8.8,
                        "涨跌幅": -2.1,
                        "成交额": 500_000_000,
                        "量比": 1.1,
                        "振幅": 4.0,
                        "换手率": 5.0,
                    },
                ]
            ),
            hot_rank=pd.DataFrame(
                [
                    {"代码": "600001", "排名": 10},
                    {"代码": "002001", "排名": 30},
                ]
            ),
        limit_pool=pd.DataFrame([{"代码": "600001", "连板数": 2}]),
        indexes=pd.DataFrame(),
        industries=pd.DataFrame(),
        concepts=pd.DataFrame(),
        trade_date=date(2026, 5, 29),
        warnings=[],
    )

        recap = build_recap(data, CONFIG)

        self.assertEqual(recap.market.total_count, 2)
        self.assertGreaterEqual(len(recap.candidates), 1)
        self.assertGreaterEqual(recap.candidates[0].score, 0)
        self.assertLessEqual(recap.candidates[0].score, 100)
        self.assertTrue(any("龙头" in tag for tag in recap.candidates[0].strategy_tags))

    def test_render_report_outputs_html_sections(self) -> None:
        data = MarketData(
            spot=pd.DataFrame(
                [
                    {
                        "代码": "600001",
                        "名称": "龙头样本",
                        "最新价": 12.3,
                        "涨跌幅": 4.2,
                        "成交额": 800_000_000,
                        "量比": 1.6,
                        "振幅": 6.1,
                        "换手率": 8.2,
                    }
                ]
            ),
            hot_rank=pd.DataFrame([{"代码": "600001", "排名": 10}]),
            limit_pool=pd.DataFrame([{"代码": "600001", "连板数": 2}]),
            indexes=pd.DataFrame(),
            industries=pd.DataFrame(),
            concepts=pd.DataFrame(),
            trade_date=date(2026, 5, 29),
            warnings=[],
        )
        recap = build_recap(data, CONFIG)

        _, html, text = render_report(recap, "复盘")

        self.assertIn("<!doctype html>", html)
        self.assertIn("市场概览", html)
        self.assertIn("热点与龙头", html)
        self.assertIn("短线机会", html)
        self.assertIn("龙头样本", html)
        self.assertIn("龙头样本", text)


if __name__ == "__main__":
    unittest.main()
