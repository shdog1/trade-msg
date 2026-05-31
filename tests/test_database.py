from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import pandas as pd

from src.database import (
    INDEX_COLUMNS,
    SCHEMA_SQL,
    UNIQUE_KEY_COLUMNS,
    MySQLConfig,
    candidate_to_row,
    daily_bar_rows,
    derive_limit_pool_rows,
    limit_pool_rows,
    stock_basic_rows,
)
from src.models import Candidate
from src.market_data import to_exchange_symbol


class DatabaseTest(unittest.TestCase):
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
            strategy_tags=["龙头反弹"],
            score=75,
            score_parts={"strategy_fit": 20},
            trigger="突破观察",
            invalidation="跌破失效",
            reasons=["成交额较高"],
        )

        row = candidate_to_row(__import__("datetime").date(2026, 5, 29), candidate)

        self.assertEqual(row["code"], "600001")
        self.assertIn("龙头反弹", row["strategy_tags"])
        self.assertIn("成交额较高", row["reasons"])

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


if __name__ == "__main__":
    unittest.main()
