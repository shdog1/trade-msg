from __future__ import annotations

import sys
import types
import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from src.market_data import (
    DAILY_BAR_ADJUST,
    AkshareMarketProvider,
    merge_missing_spot_metrics,
    normalize_tencent_spot_metrics,
    parse_limit_up_reason_history,
)


class MarketDataProviderTest(unittest.TestCase):
    def test_limit_up_reason_history_parses_actual_limit_events(self) -> None:
        history = parse_limit_up_reason_history(
            """
            <h3>涨停原因</h3>
            <table>
              <tr><th>日期</th><th>时间</th><th>事件</th><th>原因</th></tr>
              <tr><td>2026-06-20</td><td>09:30</td><td>涨停</td><td>机器人 · 获得大额订单</td></tr>
              <tr><td>2026-06-18</td><td>--</td><td>超涨10%</td><td>行业上涨</td></tr>
            </table>
            """
        )

        self.assertEqual(history[0]["event_type"], "涨停")
        self.assertEqual(history[0]["reason"], "机器人 · 获得大额订单")

    def test_limit_up_reason_history_prefers_full_title_text(self) -> None:
        history = parse_limit_up_reason_history(
            """
            <h3>涨停原因</h3>
            <table>
              <tr><th>日期</th><th>时间</th><th>事件</th><th>原因</th></tr>
              <tr>
                <td>2026-06-26</td><td>09:30</td><td>涨停</td>
                <td title="PCB+覆铜板 · 完整涨停原因">PCB+覆铜板 · 完整涨...</td>
              </tr>
            </table>
            """
        )

        self.assertEqual(history[0]["reason"], "PCB+覆铜板 · 完整涨停原因")

    def test_limit_reason_fetch_uses_exact_trade_date(self) -> None:
        response = __import__("unittest.mock").mock.Mock()
        response.text = """
        <h3>涨停原因</h3><table>
          <tr><th>日期</th><th>时间</th><th>事件</th><th>原因</th></tr>
          <tr><td>2026-06-25</td><td>09:30</td><td>涨停</td><td>旧原因</td></tr>
        </table>
        """
        response.raise_for_status.return_value = None
        with patch("src.market_data.requests.get", return_value=response):
            row = AkshareMarketProvider._fetch_limit_up_reason("600001", date(2026, 6, 26))

        self.assertIsNotNone(row)
        self.assertIsNone(row["reason"])

    def test_tencent_metrics_are_normalized_to_quote_units(self) -> None:
        metrics = normalize_tencent_spot_metrics(
            pd.DataFrame(
                [
                    {
                        "code": "sh600519",
                        "volume": "50066.00",
                        "turnover": "592201",
                        "hsl": "0.40",
                        "lb": "0.94",
                        "zf": "2.61",
                        "zsz": "14608.83",
                    }
                ]
            )
        ).iloc[0]

        self.assertEqual(metrics["代码"], "600519")
        self.assertEqual(metrics["成交量"], 5_006_600)
        self.assertEqual(metrics["成交额"], 5_922_010_000)
        self.assertEqual(metrics["换手率"], 0.4)
        self.assertEqual(metrics["总市值"], 1_460_883_000_000)

    def test_missing_spot_metrics_are_supplemented_without_overwriting_existing_values(self) -> None:
        spot = pd.DataFrame(
            [
                {"代码": "sh600519", "成交量": 123, "成交额": 456},
                {"代码": "000001", "成交量": None, "成交额": None},
            ]
        )
        metrics = pd.DataFrame(
            [
                {"代码": "600519", "成交量": 999, "成交额": 888, "换手率": 0.4, "总市值": 1000},
                {"代码": "000001", "成交量": 777, "成交额": 666, "换手率": 1.2, "总市值": 2000},
            ]
        )

        result = merge_missing_spot_metrics(spot, metrics)

        self.assertEqual(result.iloc[0]["成交量"], 123)
        self.assertEqual(result.iloc[0]["成交额"], 456)
        self.assertEqual(result.iloc[1]["成交量"], 777)
        self.assertEqual(result.iloc[1]["换手率"], 1.2)
        self.assertEqual(result.iloc[1]["总市值"], 2000)

    def test_fetch_daily_bars_requests_forward_adjusted_data_from_all_sources(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []
        fake_akshare = types.ModuleType("akshare")

        def eastmoney(**kwargs: object) -> pd.DataFrame:
            calls.append(("eastmoney", kwargs))
            return pd.DataFrame()

        def tencent(**kwargs: object) -> pd.DataFrame:
            calls.append(("tencent", kwargs))
            return pd.DataFrame()

        def sina(**kwargs: object) -> pd.DataFrame:
            calls.append(("sina", kwargs))
            return pd.DataFrame([{"date": "2026-06-24", "close": 10.8}])

        fake_akshare.stock_zh_a_hist = eastmoney
        fake_akshare.stock_zh_a_hist_tx = tencent
        fake_akshare.stock_zh_a_daily = sina

        with (
            patch.dict(sys.modules, {"akshare": fake_akshare}),
            patch("src.market_data.sleep", return_value=None),
        ):
            df, source = AkshareMarketProvider().fetch_daily_bars(
                "603843",
                date(2026, 6, 20),
                date(2026, 6, 24),
            )

        self.assertFalse(df.empty)
        self.assertEqual(source, "sina")
        self.assertEqual([name for name, _ in calls], ["eastmoney", "tencent", "sina"])
        self.assertTrue(all(kwargs["adjust"] == DAILY_BAR_ADJUST for _, kwargs in calls))
        self.assertEqual(calls[0][1]["symbol"], "603843")
        self.assertEqual(calls[1][1]["symbol"], "sh603843")
        self.assertEqual(calls[2][1]["symbol"], "sh603843")


if __name__ == "__main__":
    unittest.main()
