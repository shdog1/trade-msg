from __future__ import annotations

from datetime import date
import unittest

import pandas as pd

from src.analysis import TEXT, build_history_features, build_recap, filter_main_board, normalize_spot
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

    def test_history_features_calculate_trend_metrics(self) -> None:
        bars = make_bars("600001", [10 + index * 0.2 for index in range(25)])

        features = build_history_features(bars, date(2026, 5, 29))["600001"]

        self.assertTrue(features["has_history"])
        self.assertIsNotNone(features["return_20d_pct"])
        self.assertIsNotNone(features["drawdown_from_20d_high_pct"])
        self.assertGreater(features["avg_turnover_5d"], 0)
        self.assertTrue(features["above_ma5"])

    def test_history_driven_strategy_tags_are_reported(self) -> None:
        data = MarketData(
            spot=pd.DataFrame(
                [
                    spot_row("600001", "Rebound", 13.2, 3.2, 900_000_000, 6.0, 1.8),
                    spot_row("600002", "Pullback", 12.2, -1.0, 850_000_000, 5.5, 1.1),
                    spot_row("600003", "Second", 15.0, 2.2, 900_000_000, 7.0, 1.4),
                ]
            ),
            hot_rank=pd.DataFrame(
                [
                    {"code": "600001", "hot_rank": 5},
                    {"code": "600002", "hot_rank": 8},
                    {"code": "600003", "hot_rank": 6},
                ]
            ),
            limit_pool=pd.DataFrame([{"code": "600003", "limit_up_days": 2}]),
            indexes=pd.DataFrame(),
            industries=pd.DataFrame(),
            concepts=pd.DataFrame(),
            trade_date=date(2026, 5, 29),
            warnings=[],
            daily_bars=pd.concat(
                [
                    make_bars("600001", [9, 9.4, 9.8, 10.1, 10.5, 10.8, 11.0, 11.3, 11.6, 11.9, 12.2, 12.3, 12.1, 11.9, 11.7, 11.8, 12.0, 12.2, 12.4, 12.6, 12.8, 12.9, 13.0, 13.1, 13.2]),
                    make_bars("600002", [9, 9.5, 10, 10.5, 11, 11.5, 12, 12.5, 13, 13.5, 14, 14.5, 14.2, 13.9, 13.5, 13.0, 12.8, 12.6, 12.4, 12.2, 12.1, 12.0, 12.1, 12.2, 12.2]),
                    make_bars("600003", [8, 8.4, 8.8, 9.2, 9.6, 10.2, 10.8, 11.5, 12.2, 13, 14, 15, 16, 15, 14, 13.5, 13.2, 13.4, 13.7, 14, 14.3, 14.5, 14.7, 14.9, 15.0]),
                ],
                ignore_index=True,
            ),
        )

        recap = build_recap(data, CONFIG)
        tags = {item.code: item.strategy_tags for item in recap.candidates}

        self.assertIn(TEXT["rebound"], tags["600001"])
        self.assertIn(TEXT["pullback"], tags["600002"])
        self.assertIn(TEXT["second_wave"], tags["600003"])
        self.assertTrue(any("20" in reason for item in recap.candidates for reason in item.reasons))

    def test_missing_daily_bars_uses_fallback_with_lower_history_score(self) -> None:
        data = MarketData(
            spot=pd.DataFrame([spot_row("600001", "Fallback", 12.3, 4.2, 800_000_000, 8.2, 1.6)]),
            hot_rank=pd.DataFrame([{"code": "600001", "hot_rank": 10}]),
            limit_pool=pd.DataFrame([{"code": "600001", "limit_up_days": 2}]),
            indexes=pd.DataFrame(),
            industries=pd.DataFrame(),
            concepts=pd.DataFrame(),
            trade_date=date(2026, 5, 29),
            warnings=[],
        )

        recap = build_recap(data, CONFIG)

        self.assertEqual(recap.candidates[0].score_parts["historical_shape"], 6)
        self.assertTrue(any(TEXT["history_missing"] in reason for reason in recap.candidates[0].reasons))

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

def spot_row(
    code: str,
    name: str,
    close: float,
    change_pct: float,
    turnover: float,
    turnover_rate: float,
    volume_ratio: float,
) -> dict[str, object]:
    return {
        "code": code,
        "name": name,
        "close": close,
        "change_pct": change_pct,
        "turnover": turnover,
        "turnover_rate": turnover_rate,
        "volume_ratio": volume_ratio,
        "amplitude_pct": 4.0,
    }


def make_bars(code: str, closes: list[float]) -> pd.DataFrame:
    start = pd.Timestamp("2026-04-27")
    rows = []
    previous = closes[0]
    for index, close in enumerate(closes):
        change_pct = 0.0 if index == 0 else (close / previous - 1) * 100
        rows.append(
            {
                "trade_date": (start + pd.Timedelta(days=index)).date(),
                "code": code,
                "open": close * 0.99,
                "high": close * 1.01,
                "low": close * 0.98,
                "close": close,
                "change_pct": change_pct,
                "turnover": 500_000_000 + index * 20_000_000,
                "turnover_rate": 5.0,
                "amplitude_pct": 4.0,
            }
        )
        previous = close
    return pd.DataFrame(rows)


if __name__ == "__main__":
    unittest.main()
