from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.database import INDEX_COLUMNS, SCHEMA_SQL, UNIQUE_KEY_COLUMNS, MySQLConfig, candidate_to_row
from src.models import Candidate


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


if __name__ == "__main__":
    unittest.main()
