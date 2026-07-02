from __future__ import annotations

import os
import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from src.database import (
    INDEX_COLUMNS,
    SCHEMA_SQL,
    UNIQUE_KEY_COLUMNS,
    MySQLConfig,
    candidate_to_row,
    daily_bar_rows,
    daily_bar_rows_from_spot,
    database_safe_value,
    derive_limit_pool_rows,
    limit_pool_rows,
    merge_daily_bars_with_quotes,
    pattern_candidate_to_row,
    stock_basic_rows,
)
from src.models import Candidate
from src.market_data import to_exchange_symbol


class DatabaseTest(unittest.TestCase):
    def test_database_safe_value_converts_nan_to_none(self) -> None:
        self.assertIsNone(database_safe_value(float("nan")))
        self.assertIsNone(database_safe_value(pd.NA))
        self.assertEqual(database_safe_value(6.5), 6.5)

    def test_mysql_config_reads_environment(self) -> None:
        env = {
            "MYSQL_HOST": "localhost",
            "MYSQL_PORT": "3307",
            "MYSQL_USER": "trade",
            "MYSQL_PASSWORD": "secret",
            "MYSQL_DATABASE": "trade_msg_test",
            "MYSQL_CHARSET": "utf8mb4",
        }
        with patch.dict(os.environ, env, clear=False):
            config = MySQLConfig.from_env({"database": {"auto_create_database": False}})

        self.assertEqual(config.host, "localhost")
        self.assertEqual(config.port, 3307)
        self.assertEqual(config.user, "trade")
        self.assertEqual(config.password, "secret")
        self.assertEqual(config.database, "trade_msg_test")
        self.assertFalse(config.auto_create_database)
        self.assertIn("mysql+pymysql://", config.url())

    def test_candidate_to_row_serializes_structured_fields(self) -> None:
        candidate = Candidate(
            code="600001",
            name="样本",
            close=10.0,
            change_pct=2.0,
            turnover=500_000_000,
            volume_ratio=1.2,
            amplitude_pct=4.0,
            hot_rank=10,
            limit_up_days=2,
            strategy_tags=["放量承接"],
            score=75,
            score_parts={"strategy_fit": 20},
            trigger="突破观察",
            invalidation="跌破失效",
            reasons=["成交额较高"],
        )

        row = candidate_to_row(__import__("datetime").date(2026, 5, 29), candidate)

        self.assertEqual(row["code"], "600001")
        self.assertIn("放量承接", row["strategy_tags"])
        self.assertIn("成交额较高", row["reasons"])

    def test_pattern_candidate_to_row_serializes_pattern_fields(self) -> None:
        candidate = Candidate(
            code="002421",
            name="达实智能",
            close=17.2,
            change_pct=3.0,
            turnover=900_000_000,
            volume_ratio=1.5,
            amplitude_pct=8.0,
            hot_rank=None,
            limit_up_days=6,
            strategy_tags=["连板平台洗盘", "收复确认"],
            score=88,
            score_parts={"attack_strength": 30},
            trigger="收复前高观察",
            invalidation="跌破平台失效",
            reasons=["前段形态: 7天6板"],
            raw={"pattern_type": "limit_platform_wash", "stage": "收复确认"},
        )

        row = pattern_candidate_to_row(__import__("datetime").date(2026, 5, 29), candidate)

        self.assertEqual(row["pattern_type"], "limit_platform_wash")
        self.assertEqual(row["stage"], "收复确认")
        self.assertIn("7天6板", row["reasons"])

    def test_schema_uses_surrogate_primary_keys_and_business_unique_keys(self) -> None:
        schema = "\n".join(SCHEMA_SQL)

        for table in UNIQUE_KEY_COLUMNS:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", schema)
            self.assertIn("id BIGINT AUTO_INCREMENT PRIMARY KEY", schema)
            self.assertIn(f"uk_{table}_business", schema)

    def test_recommended_indexes_are_declared(self) -> None:
        schema = "\n".join(SCHEMA_SQL)

        for indexes in INDEX_COLUMNS.values():
            for index_name in indexes:
                self.assertIn(index_name, schema)

    def test_market_quotes_store_volume_and_total_market_cap(self) -> None:
        schema = "\n".join(SCHEMA_SQL)

        self.assertIn("volume DOUBLE NULL", schema)
        self.assertIn("total_market_cap DOUBLE NULL", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS limit_up_reasons", schema)

    def test_stock_basic_rows_marks_main_board_and_st(self) -> None:
        rows = stock_basic_rows(
            pd.DataFrame(
                [
                    {"code": "600001", "name": "样本A"},
                    {"code": "300001", "name": "创业A"},
                    {"code": "000001", "name": "ST样本"},
                ]
            )
        )

        by_code = {row["code"]: row for row in rows}
        self.assertEqual(by_code["600001"]["is_main_board"], 1)
        self.assertEqual(by_code["300001"]["is_main_board"], 0)
        self.assertEqual(by_code["000001"]["is_st"], 1)

    def test_daily_bar_rows_normalizes_hist_columns(self) -> None:
        rows = daily_bar_rows(
            pd.DataFrame(
                [
                    {
                        "日期": "2026-05-29",
                        "开盘": 10,
                        "最高": 11,
                        "最低": 9,
                        "收盘": 10.5,
                        "涨跌幅": 2.1,
                        "成交量": 1000,
                        "成交额": 2000,
                        "换手率": 3.2,
                        "振幅": 4.1,
                    }
                ]
            ),
            "600001",
            "akshare",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "600001")
        self.assertEqual(rows[0]["close_price"], 10.5)

    def test_daily_bar_rows_normalizes_english_columns(self) -> None:
        rows = daily_bar_rows(
            pd.DataFrame(
                [
                    {
                        "date": "2026-05-29",
                        "open": 10,
                        "high": 11,
                        "low": 9,
                        "close": 10.5,
                        "volume": 1000,
                        "amount": 2000,
                        "turnover": 3.2,
                    }
                ]
            ),
            "000001",
            "akshare",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "000001")
        self.assertEqual(rows[0]["close_price"], 10.5)

    def test_daily_bar_rows_from_spot_maps_batch_quote_fields(self) -> None:
        rows = daily_bar_rows_from_spot(
            pd.DataFrame(
                [
                    {
                        "\u4ee3\u7801": "600001",
                        "\u540d\u79f0": "\u6837\u672cA",
                        "\u4eca\u5f00": 10.1,
                        "\u6700\u9ad8": 11.0,
                        "\u6700\u4f4e": 9.8,
                        "\u6700\u65b0\u4ef7": 10.8,
                        "\u6da8\u8dcc\u5e45": 4.2,
                        "\u6210\u4ea4\u91cf": 123456,
                        "\u6210\u4ea4\u989d": 500000000,
                        "\u6362\u624b\u7387": 6.5,
                        "\u632f\u5e45": 11.2,
                    }
                ]
            ),
            date(2026, 6, 8),
            "akshare",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trade_date"], date(2026, 6, 8))
        self.assertEqual(rows[0]["code"], "600001")
        self.assertEqual(rows[0]["name"], "\u6837\u672cA")
        self.assertEqual(rows[0]["open_price"], 10.1)
        self.assertEqual(rows[0]["high_price"], 11.0)
        self.assertEqual(rows[0]["low_price"], 9.8)
        self.assertEqual(rows[0]["close_price"], 10.8)
        self.assertEqual(rows[0]["volume"], 123456)
        self.assertEqual(rows[0]["turnover_rate"], 6.5)
        self.assertEqual(rows[0]["amplitude_pct"], 11.2)

    def test_daily_bar_rows_from_spot_allows_missing_ohlc_fields(self) -> None:
        rows = daily_bar_rows_from_spot(
            pd.DataFrame(
                [
                    {
                        "code": "000001",
                        "name": "PingAn",
                        "close": 12.3,
                        "change_pct": 1.1,
                        "turnover": 300000000,
                    }
                ]
            ),
            date(2026, 6, 8),
            "akshare",
        )

        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["open_price"])
        self.assertIsNone(rows[0]["high_price"])
        self.assertIsNone(rows[0]["low_price"])
        self.assertEqual(rows[0]["close_price"], 12.3)

    def test_merge_daily_bars_with_quotes_fills_missing_dates_only(self) -> None:
        daily = pd.DataFrame(
            [
                {"trade_date": "2026-05-29", "code": "002421", "close": 4.11},
            ]
        )
        quotes = pd.DataFrame(
            [
                {"trade_date": "2026-05-29", "code": "002421", "close": 4.12},
                {"trade_date": "2026-06-05", "code": "002421", "close": 5.21},
            ]
        )

        merged = merge_daily_bars_with_quotes(daily, quotes)

        self.assertEqual(len(merged), 2)
        self.assertEqual(float(merged[merged["trade_date"] == __import__("datetime").date(2026, 5, 29)]["close"].iloc[0]), 4.11)
        self.assertEqual(float(merged[merged["trade_date"] == __import__("datetime").date(2026, 6, 5)]["close"].iloc[0]), 5.21)

    def test_exchange_symbol_for_fallback_sources(self) -> None:
        self.assertEqual(to_exchange_symbol("600001"), "sh600001")
        self.assertEqual(to_exchange_symbol("688001"), "sh688001")
        self.assertEqual(to_exchange_symbol("000001"), "sz000001")
        self.assertEqual(to_exchange_symbol("300001"), "sz300001")

    def test_derive_limit_pool_rows_counts_consecutive_limit_ups(self) -> None:
        rows = derive_limit_pool_rows(
            pd.DataFrame(
                [
                    {"trade_date": "2026-05-26", "code": "600001", "change_pct": 9.95},
                    {"trade_date": "2026-05-27", "code": "600001", "change_pct": 10.0},
                    {"trade_date": "2026-05-28", "code": "600001", "change_pct": 9.9},
                    {"trade_date": "2026-05-28", "code": "000001", "change_pct": 4.0},
                ]
            ),
            __import__("datetime").date(2026, 5, 28),
        )

        self.assertEqual(
            rows,
            [
                {
                    "trade_date": __import__("datetime").date(2026, 5, 28),
                    "code": "600001",
                    "limit_up_days": 3,
                    "industry": None,
                    "reason": None,
                    "source": "akshare",
                }
            ],
        )

    def test_limit_pool_rows_keeps_industry_and_reason(self) -> None:
        rows = limit_pool_rows(
            pd.DataFrame(
                [
                    {
                        "代码": "600001",
                        "连板数": 2,
                        "所属行业": "机器人",
                        "涨停原因类别": "机器人+人工智能",
                    }
                ]
            ),
            __import__("datetime").date(2026, 5, 29),
        )

        self.assertEqual(rows[0]["industry"], "机器人")
        self.assertEqual(rows[0]["reason"], "机器人+人工智能")

    def test_limit_pool_rows_accepts_selected_reason_column(self) -> None:
        rows = limit_pool_rows(
            pd.DataFrame(
                [
                    {
                        "代码": "600001",
                        "连板数": 2,
                        "所属行业": "机器人",
                        "入选理由": "近期多次涨停",
                    }
                ]
            ),
            __import__("datetime").date(2026, 5, 29),
        )

        self.assertEqual(rows[0]["reason"], "近期多次涨停")


if __name__ == "__main__":
    unittest.main()
