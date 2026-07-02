from __future__ import annotations

from datetime import date
import unittest

import pandas as pd

from src.analysis import (
    TEXT,
    build_history_features,
    build_recap,
    extend_attack_window,
    filter_main_board,
    limit_platform_config,
    normalize_spot,
)
from src.market_data import MarketData
from src.report import render_report


CONFIG = {
    "market": {
        "main_board_prefixes": ["600", "601", "603", "605", "000", "001", "002", "003"],
        "exclude_name_keywords": ["ST", "*ST", "退"],
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

    def test_limit_platform_zero_max_candidates_means_no_cap(self) -> None:
        config = {"pattern": {"limit_platform": {"max_candidates": 0}}}

        self.assertEqual(limit_platform_config(config)["max_candidates"], 0)

    def test_attack_window_stops_when_new_intraday_high_closes_down(self) -> None:
        bars = pd.DataFrame(
            [
                {"trade_date": pd.Timestamp("2026-06-09"), "high": 6.40, "change_pct": 10.0},
                {"trade_date": pd.Timestamp("2026-06-10"), "high": 7.04, "change_pct": 8.4},
                {"trade_date": pd.Timestamp("2026-06-11"), "high": 7.05, "change_pct": -9.9},
                {"trade_date": pd.Timestamp("2026-06-12"), "high": 6.60, "change_pct": 3.0},
                {"trade_date": pd.Timestamp("2026-06-15"), "high": 6.69, "change_pct": 1.2},
                {"trade_date": pd.Timestamp("2026-06-16"), "high": 7.17, "change_pct": 9.8},
            ]
        )
        attack = {
            "attack_end_index": 0,
            "attack_end_date": pd.Timestamp("2026-06-09").date(),
            "attack_high": 6.40,
        }
        cfg = {"attack_extension_days": 2, "min_platform_days": 3}

        extended = extend_attack_window(bars, attack, len(bars) - 1, cfg)

        self.assertEqual(extended["attack_end_index"], 1)
        self.assertEqual(extended["attack_end_date"], pd.Timestamp("2026-06-10").date())
        self.assertEqual(extended["attack_high"], 7.04)

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

    def test_build_recap_suppresses_legacy_strategy_candidates(self) -> None:
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
        self.assertEqual(recap.candidates, [])
        self.assertGreaterEqual(len(recap.limit_leaders), 1)

    def test_history_features_calculate_trend_metrics(self) -> None:
        bars = make_bars("600001", [10 + index * 0.2 for index in range(25)])

        features = build_history_features(bars, date(2026, 5, 29))["600001"]

        self.assertTrue(features["has_history"])
        self.assertIsNotNone(features["return_20d_pct"])
        self.assertIsNotNone(features["drawdown_from_20d_high_pct"])
        self.assertGreater(features["avg_turnover_5d"], 0)
        self.assertTrue(features["above_ma5"])

    def test_history_driven_legacy_strategy_tags_are_not_reported(self) -> None:
        data = MarketData(
            spot=pd.DataFrame(
                [
                    spot_row("600001", "SampleA", 13.2, 3.2, 900_000_000, 6.0, 1.8),
                    spot_row("600002", "SampleB", 12.2, -1.0, 850_000_000, 5.5, 1.1),
                    spot_row("600003", "SampleC", 15.0, 2.2, 900_000_000, 7.0, 1.4),
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
        self.assertEqual(recap.candidates, [])

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

        self.assertEqual(recap.candidates, [])

    def test_limit_platform_pattern_selects_recovered_high(self) -> None:
        data = MarketData(
            spot=pd.DataFrame([spot_row("002421", "达实智能", 17.5, 3.0, 900_000_000, 18.0, 1.5)]),
            hot_rank=pd.DataFrame(),
            limit_pool=pd.DataFrame(),
            indexes=pd.DataFrame(),
            industries=pd.DataFrame(),
            concepts=pd.DataFrame(),
            trade_date=date(2026, 5, 29),
            warnings=[],
            daily_bars=make_limit_platform_bars("002421", recovered=True),
        )

        recap = build_recap(data, CONFIG)

        self.assertEqual(len(recap.limit_platform_candidates), 1)
        candidate = recap.limit_platform_candidates[0]
        self.assertEqual(candidate.raw["stage"], TEXT["platform_confirm"])
        self.assertIn("7天6板", candidate.raw["attack_label"])
        joined_reasons = "；".join(candidate.reasons)
        self.assertNotIn("巨量", joined_reasons)
        self.assertNotIn("最大换手", joined_reasons)
        self.assertNotIn("成交额放大", joined_reasons)

    def test_limit_platform_pattern_keeps_watch_stage_before_breakout(self) -> None:
        data = MarketData(
            spot=pd.DataFrame([spot_row("002421", "达实智能", 16.0, -1.0, 700_000_000, 12.0, 1.2)]),
            hot_rank=pd.DataFrame(),
            limit_pool=pd.DataFrame(),
            indexes=pd.DataFrame(),
            industries=pd.DataFrame(),
            concepts=pd.DataFrame(),
            trade_date=date(2026, 5, 29),
            warnings=[],
            daily_bars=make_limit_platform_bars("002421", recovered=False),
        )

        recap = build_recap(data, CONFIG)

        self.assertEqual(recap.limit_platform_candidates[0].raw["stage"], TEXT["platform_watch"])
        self.assertLessEqual(recap.limit_platform_candidates[0].score, 78)

    def test_limit_platform_intraday_breakout_without_recovered_close_stays_watch(self) -> None:
        bars = make_limit_platform_bars("002421", recovered=False)
        bars.loc[bars.index[-1], "high"] = 18.0
        bars.loc[bars.index[-1], "amplitude_pct"] = 15.0
        data = MarketData(
            spot=pd.DataFrame([spot_row("002421", "达实智能", 16.0, -1.0, 700_000_000, 12.0, 1.2)]),
            hot_rank=pd.DataFrame(),
            limit_pool=pd.DataFrame(),
            indexes=pd.DataFrame(),
            industries=pd.DataFrame(),
            concepts=pd.DataFrame(),
            trade_date=date(2026, 5, 29),
            warnings=[],
            daily_bars=bars,
        )

        recap = build_recap(data, CONFIG)

        candidate = recap.limit_platform_candidates[0]
        self.assertEqual(candidate.raw["stage"], TEXT["platform_watch"])
        self.assertIn("收盘站上", candidate.trigger)

    def test_limit_platform_pattern_rejects_deep_drawdown(self) -> None:
        bars = make_limit_platform_bars("002421", recovered=False)
        bars.loc[bars.index[-2], "low"] = 12.0
        bars.loc[bars.index[-2], "amplitude_pct"] = 28.0
        data = MarketData(
            spot=pd.DataFrame([spot_row("002421", "达实智能", 16.0, -1.0, 700_000_000, 12.0, 1.2)]),
            hot_rank=pd.DataFrame(),
            limit_pool=pd.DataFrame(),
            indexes=pd.DataFrame(),
            industries=pd.DataFrame(),
            concepts=pd.DataFrame(),
            trade_date=date(2026, 5, 29),
            warnings=[],
            daily_bars=bars,
        )

        recap = build_recap(data, CONFIG)

        self.assertEqual(recap.limit_platform_candidates, [])

    def test_limit_platform_pattern_rejects_close_below_prior_platform_low(self) -> None:
        bars = make_limit_platform_bars("002421", recovered=False)
        bars.loc[bars.index[-1], "close"] = 14.2
        bars.loc[bars.index[-1], "low"] = 14.0
        bars.loc[bars.index[-1], "high"] = 15.5
        bars.loc[bars.index[-1], "amplitude_pct"] = 10.0
        data = MarketData(
            spot=pd.DataFrame([spot_row("002421", "破位样本", 14.2, -11.2, 700_000_000, 12.0, 1.2)]),
            hot_rank=pd.DataFrame(),
            limit_pool=pd.DataFrame(),
            indexes=pd.DataFrame(),
            industries=pd.DataFrame(),
            concepts=pd.DataFrame(),
            trade_date=date(2026, 5, 29),
            warnings=[],
            daily_bars=bars,
        )

        recap = build_recap(data, CONFIG)

        self.assertEqual(recap.limit_platform_candidates, [])

    def test_limit_platform_pattern_derives_missing_change_and_amplitude(self) -> None:
        bars = make_limit_platform_bars("002421", recovered=True)
        bars["change_pct"] = None
        bars["amplitude_pct"] = None
        data = MarketData(
            spot=pd.DataFrame([spot_row("002421", "达实智能", 17.5, 3.0, 900_000_000, 18.0, 1.5)]),
            hot_rank=pd.DataFrame(),
            limit_pool=pd.DataFrame(),
            indexes=pd.DataFrame(),
            industries=pd.DataFrame(),
            concepts=pd.DataFrame(),
            trade_date=date(2026, 5, 29),
            warnings=[],
            daily_bars=bars,
        )

        recap = build_recap(data, CONFIG)

        self.assertEqual(recap.limit_platform_candidates[0].raw["stage"], TEXT["platform_confirm"])

    def test_limit_platform_pattern_selects_bbk_style_four_day_three_board(self) -> None:
        data = MarketData(
            spot=pd.DataFrame([spot_row("002251", "步步高", 4.86, 9.95, 1_638_000_000, 0.0, 1.4)]),
            hot_rank=pd.DataFrame(),
            limit_pool=pd.DataFrame(),
            indexes=pd.DataFrame(),
            industries=pd.DataFrame(),
            concepts=pd.DataFrame(),
            trade_date=date(2026, 6, 5),
            warnings=[],
            daily_bars=make_bbk_like_bars("002251"),
        )

        recap = build_recap(data, CONFIG)

        self.assertEqual(recap.limit_platform_candidates[0].raw["stage"], TEXT["platform_watch"])
        self.assertIn("4天3板", recap.limit_platform_candidates[0].raw["attack_label"])

    def test_limit_platform_pattern_rejects_when_most_attack_gain_is_retraced(self) -> None:
        bars = make_bbk_like_bars("002251")
        bars.loc[bars.index[-1], "low"] = 3.76
        bars.loc[bars.index[-1], "amplitude_pct"] = 25.2
        data = MarketData(
            spot=pd.DataFrame([spot_row("002251", "步步高", 4.86, 9.95, 1_638_000_000, 0.0, 1.4)]),
            hot_rank=pd.DataFrame(),
            limit_pool=pd.DataFrame(),
            indexes=pd.DataFrame(),
            industries=pd.DataFrame(),
            concepts=pd.DataFrame(),
            trade_date=date(2026, 6, 5),
            warnings=[],
            daily_bars=bars,
        )

        recap = build_recap(data, CONFIG)

        self.assertEqual(recap.limit_platform_candidates, [])

    def test_limit_platform_pattern_rejects_stale_long_platform(self) -> None:
        data = MarketData(
            spot=pd.DataFrame([spot_row("600156", "华升股份", 9.56, -1.0, 220_000_000, 0.0, 1.0)]),
            hot_rank=pd.DataFrame(),
            limit_pool=pd.DataFrame(),
            indexes=pd.DataFrame(),
            industries=pd.DataFrame(),
            concepts=pd.DataFrame(),
            trade_date=date(2026, 6, 5),
            warnings=[],
            daily_bars=make_stale_platform_bars("600156"),
        )

        recap = build_recap(data, CONFIG)

        self.assertEqual(recap.limit_platform_candidates, [])

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
        self.assertNotIn("\u77ed\u7ebf\u673a\u4f1a", html)
        self.assertNotIn("\u9f99\u5934\u53cd\u5f39", html)
        self.assertNotIn("\u9f99\u5934\u4f4e\u5438", html)
        self.assertNotIn("\u9f99\u5934\u4e8c\u6ce2", html)
        self.assertIn("连板平台洗盘观察", html)
        self.assertIn("龙头样本", html)
        self.assertNotIn("龙头样本", text)
        self.assertIn("连板平台洗盘观察", text)

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


def make_limit_platform_bars(code: str, recovered: bool = True) -> pd.DataFrame:
    baseline = [10.0] * 20
    attack = [11.0, 12.1, 13.31, 12.85, 14.14, 15.55, 17.10]
    platform = [15.6, 16.5, 15.8, 17.5 if recovered else 16.0]
    closes = baseline + attack + platform
    dates = pd.bdate_range(end="2026-05-29", periods=len(closes))
    rows = []
    previous = closes[0]
    for index, close in enumerate(closes):
        change_pct = 0.0 if index == 0 else (close / previous - 1) * 100
        is_attack = 20 <= index < 27
        is_platform = index >= 27
        high = close * (1.015 if not is_platform else 1.08)
        low = close * (0.985 if not is_platform else 0.92)
        turnover = 120_000_000
        turnover_rate = 5.0
        amplitude = (high - low) / close * 100
        if is_attack:
            turnover = 900_000_000 if index == 23 else 500_000_000
            turnover_rate = 50.0 if index == 23 else 22.0
        if is_platform:
            turnover = 650_000_000
            turnover_rate = 18.0
        rows.append(
            {
                "trade_date": dates[index].date(),
                "code": code,
                "open": close * 0.98,
                "high": high,
                "low": low,
                "close": close,
                "change_pct": change_pct,
                "turnover": turnover,
                "turnover_rate": turnover_rate,
                "amplitude_pct": amplitude,
            }
        )
        previous = close
    return pd.DataFrame(rows)


def make_bbk_like_bars(code: str) -> pd.DataFrame:
    rows = make_bars(code, [3.8] * 20)
    extra = [
        ("2026-05-26", 3.75, 4.15, 3.74, 4.15, 1_213_542),
        ("2026-05-27", 4.08, 4.57, 4.02, 4.57, 2_852_145),
        ("2026-05-28", 4.53, 4.80, 4.41, 4.44, 4_211_169),
        ("2026-05-29", 4.45, 4.88, 4.32, 4.88, 3_364_286),
        ("2026-06-01", 4.61, 5.20, 4.61, 5.00, 4_893_200),
        ("2026-06-02", 4.85, 4.99, 4.68, 4.88, 3_677_198),
        ("2026-06-03", 4.75, 4.96, 4.56, 4.84, 3_608_467),
        ("2026-06-04", 4.80, 4.91, 4.36, 4.42, 3_438_547),
        ("2026-06-05", 4.46, 4.86, 4.44, 4.86, 3_431_819),
    ]
    previous = 3.77
    rows = rows.iloc[:20].copy()
    rows["turnover"] = 500_000
    additions = []
    for item_date, open_price, high, low, close, turnover in extra:
        additions.append(
            {
                "trade_date": pd.Timestamp(item_date).date(),
                "code": code,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "change_pct": (close / previous - 1) * 100,
                "turnover": turnover,
                "turnover_rate": None,
                "amplitude_pct": (high - low) / previous * 100,
            }
        )
        previous = close
    return pd.concat([rows, pd.DataFrame(additions)], ignore_index=True)


def make_stale_platform_bars(code: str) -> pd.DataFrame:
    baseline = [8.0] * 20
    attack = [8.8, 9.7, 10.67, 10.2, 11.2, 12.3, 13.5]
    platform = [12.5, 13.1, 12.2, 13.3, 12.1, 11.7, 11.3, 11.0, 10.8, 10.6, 10.5, 10.3, 10.2, 10.1, 10.0, 9.9]
    closes = baseline + attack + platform
    dates = pd.bdate_range(end="2026-06-05", periods=len(closes))
    rows = []
    previous = closes[0]
    for index, close in enumerate(closes):
        high = close * 1.06
        low = close * 0.94
        rows.append(
            {
                "trade_date": dates[index].date(),
                "code": code,
                "open": close,
                "high": high,
                "low": low,
                "close": close,
                "change_pct": 0.0 if index == 0 else (close / previous - 1) * 100,
                "turnover": 800_000_000 if index >= 20 else 100_000_000,
                "turnover_rate": None,
                "amplitude_pct": (high - low) / previous * 100 if previous else 0,
            }
        )
        previous = close
    return pd.DataFrame(rows)


if __name__ == "__main__":
    unittest.main()
