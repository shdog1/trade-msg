from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import quote_plus

import pandas as pd

from .analysis import (
    normalize_hot_rank,
    normalize_indexes,
    normalize_limit_pool,
    normalize_spot,
    normalize_topics,
)
from .market_data import MarketData
from .models import Candidate, Recap


class DatabaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class MySQLConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    charset: str = "utf8mb4"
    auto_create_database: bool = True

    @classmethod
    def from_env(cls, config: dict[str, Any] | None = None) -> "MySQLConfig":
        db_config = (config or {}).get("database", {})
        password = os.getenv("MYSQL_PASSWORD", "")
        if not password:
            raise DatabaseError("MYSQL_PASSWORD is not set in .env or environment.")
        return cls(
            host=os.getenv("MYSQL_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=int(os.getenv("MYSQL_PORT", "3306").strip() or "3306"),
            user=os.getenv("MYSQL_USER", "root").strip() or "root",
            password=password,
            database=os.getenv("MYSQL_DATABASE", "trade_msg").strip() or "trade_msg",
            charset=os.getenv("MYSQL_CHARSET", "utf8mb4").strip() or "utf8mb4",
            auto_create_database=bool(db_config.get("auto_create_database", True)),
        )

    def url(self, include_database: bool = True) -> str:
        auth = f"{quote_plus(self.user)}:{quote_plus(self.password)}"
        database = f"/{self.database}" if include_database else ""
        return f"mysql+pymysql://{auth}@{self.host}:{self.port}{database}?charset={self.charset}"


class MySQLStore:
    def __init__(self, config: MySQLConfig):
        self.config = config
        self._engine = None

    @classmethod
    def from_env(cls, config: dict[str, Any] | None = None) -> "MySQLStore":
        return cls(MySQLConfig.from_env(config))

    @property
    def engine(self):
        if self._engine is None:
            sqlalchemy = require_sqlalchemy()
            self._engine = sqlalchemy.create_engine(self.config.url(), future=True, pool_pre_ping=True)
        return self._engine

    def initialize(self) -> None:
        sqlalchemy = require_sqlalchemy()
        if self.config.auto_create_database:
            admin_engine = sqlalchemy.create_engine(self.config.url(include_database=False), future=True)
            with admin_engine.begin() as conn:
                conn.execute(
                    sqlalchemy.text(
                        f"CREATE DATABASE IF NOT EXISTS `{self.config.database}` "
                        f"CHARACTER SET {self.config.charset} COLLATE {self.config.charset}_unicode_ci"
                    )
                )
            admin_engine.dispose()

        with self.engine.begin() as conn:
            for statement in SCHEMA_SQL:
                conn.execute(sqlalchemy.text(statement))

    def has_market_data(self, trade_date: date) -> bool:
        sqlalchemy = require_sqlalchemy()
        with self.engine.begin() as conn:
            count = conn.execute(
                sqlalchemy.text("SELECT COUNT(*) FROM market_quotes WHERE trade_date = :trade_date"),
                {"trade_date": trade_date},
            ).scalar_one()
        return int(count) > 0

    def persist_market_data(self, data: MarketData) -> None:
        self.initialize()
        spot = normalize_spot(data.spot)
        hot_ranks = normalize_hot_rank(data.hot_rank)
        limit_pool = normalize_limit_pool(data.limit_pool)
        indexes = normalize_indexes(data.indexes)
        industries = normalize_topics(data.industries, top_n=200)
        concepts = normalize_topics(data.concepts, top_n=200)

        with self.engine.begin() as conn:
            market_rows = [
                {
                    "trade_date": data.trade_date,
                    "code": row.code,
                    "name": row.name,
                    "close_price": row.close,
                    "change_pct": row.change_pct,
                    "turnover": row.turnover,
                    "turnover_rate": getattr(row, "turnover_rate", None),
                    "volume_ratio": getattr(row, "volume_ratio", None),
                    "amplitude_pct": getattr(row, "amplitude_pct", None),
                    "source": "akshare",
                }
                for row in spot.itertuples()
            ]
            hot_rank_rows = [
                {"trade_date": data.trade_date, "code": code, "hot_rank": rank, "source": "akshare"}
                for code, rank in hot_ranks.items()
            ]
            limit_rows = [
                {"trade_date": data.trade_date, "code": code, "limit_up_days": days, "source": "akshare"}
                for code, days in limit_pool.items()
            ]
            index_rows = [
                {
                    "trade_date": data.trade_date,
                    "name": item.name,
                    "close_price": item.close,
                    "change_pct": item.change_pct,
                    "source": "akshare",
                }
                for item in indexes
            ]
            topic_rows = [
                {
                    "trade_date": data.trade_date,
                    "topic_type": "industry",
                    "name": item.name,
                    "change_pct": item.change_pct,
                    "turnover": item.turnover,
                    "source": "akshare",
                }
                for item in industries
            ] + [
                {
                    "trade_date": data.trade_date,
                    "topic_type": "concept",
                    "name": item.name,
                    "change_pct": item.change_pct,
                    "turnover": item.turnover,
                    "source": "akshare",
                }
                for item in concepts
            ]

            self._upsert_many(conn, "market_quotes", market_rows)
            self._upsert_many(conn, "hot_ranks", hot_rank_rows)
            self._upsert_many(conn, "limit_pool", limit_rows)
            self._upsert_many(conn, "index_quotes", index_rows)
            self._upsert_many(conn, "hot_topics", topic_rows)

        self.record_fetch_run(data.trade_date, "akshare", "success", None)

    def load_market_data(self, trade_date: date) -> MarketData:
        self.initialize()
        spot = self._read_df(
            "SELECT code, name, close_price AS close, change_pct, turnover, "
            "turnover_rate, volume_ratio, amplitude_pct FROM market_quotes WHERE trade_date = :trade_date",
            {"trade_date": trade_date},
        )
        hot_rank = self._read_df(
            "SELECT code, hot_rank FROM hot_ranks WHERE trade_date = :trade_date",
            {"trade_date": trade_date},
        )
        limit_pool = self._read_df(
            "SELECT code, limit_up_days FROM limit_pool WHERE trade_date = :trade_date",
            {"trade_date": trade_date},
        )
        indexes = self._read_df(
            "SELECT name, close_price AS close, change_pct FROM index_quotes WHERE trade_date = :trade_date",
            {"trade_date": trade_date},
        )
        industries = self._read_df(
            "SELECT name, change_pct, turnover FROM hot_topics WHERE trade_date = :trade_date AND topic_type = 'industry'",
            {"trade_date": trade_date},
        )
        concepts = self._read_df(
            "SELECT name, change_pct, turnover FROM hot_topics WHERE trade_date = :trade_date AND topic_type = 'concept'",
            {"trade_date": trade_date},
        )
        return MarketData(
            spot=spot,
            hot_rank=hot_rank,
            limit_pool=limit_pool,
            indexes=indexes,
            industries=industries,
            concepts=concepts,
            trade_date=trade_date,
            warnings=[],
        )

    def persist_recap(self, recap: Recap) -> None:
        self.initialize()
        with self.engine.begin() as conn:
            self._upsert_many(
                conn,
                "recap_candidates",
                [candidate_to_row(recap.market.trade_date, item) for item in recap.candidates],
            )

    def record_report(self, trade_date: date, title: str, html_path: str, text_path: str, status: str) -> None:
        self.initialize()
        with self.engine.begin() as conn:
            self._upsert_many(
                conn,
                "recap_reports",
                [
                    {
                        "trade_date": trade_date,
                        "title": title,
                        "html_path": html_path,
                        "text_path": text_path,
                        "send_status": status,
                        "generated_at": datetime.now(),
                    }
                ],
            )

    def mark_report_sent(self, trade_date: date, status: str, error: str | None = None) -> None:
        sqlalchemy = require_sqlalchemy()
        with self.engine.begin() as conn:
            conn.execute(
                sqlalchemy.text(
                    "UPDATE recap_reports SET send_status = :status, sent_at = :sent_at, "
                    "send_error = :error WHERE trade_date = :trade_date"
                ),
                {
                    "trade_date": trade_date,
                    "status": status,
                    "sent_at": datetime.now() if status == "sent" else None,
                    "error": error,
                },
            )

    def record_fetch_run(self, trade_date: date, source: str, status: str, error: str | None) -> None:
        sqlalchemy = require_sqlalchemy()
        with self.engine.begin() as conn:
            conn.execute(
                sqlalchemy.text(
                    "INSERT INTO fetch_runs (trade_date, source, status, error, started_at, finished_at) "
                    "VALUES (:trade_date, :source, :status, :error, :started_at, :finished_at)"
                ),
                {
                    "trade_date": trade_date,
                    "source": source,
                    "status": status,
                    "error": error,
                    "started_at": datetime.now(),
                    "finished_at": datetime.now(),
                },
            )

    def _read_df(self, sql: str, params: dict[str, Any]) -> pd.DataFrame:
        sqlalchemy = require_sqlalchemy()
        with self.engine.begin() as conn:
            result = conn.execute(sqlalchemy.text(sql), params)
            rows = result.mappings().all()
        return pd.DataFrame([dict(row) for row in rows])

    def _upsert_many(self, conn: Any, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        sqlalchemy = require_sqlalchemy()
        columns = list(rows[0].keys())
        update_columns = [col for col in columns if col not in UNIQUE_KEY_COLUMNS[table]]
        insert_cols = ", ".join(f"`{col}`" for col in columns)
        values_cols = ", ".join(f":{col}" for col in columns)
        update_clause = ", ".join(f"`{col}` = VALUES(`{col}`)" for col in update_columns)
        sql = (
            f"INSERT INTO `{table}` ({insert_cols}) VALUES ({values_cols}) "
            f"ON DUPLICATE KEY UPDATE {update_clause}"
        )
        conn.execute(sqlalchemy.text(sql), rows)


def candidate_to_row(trade_date: date, candidate: Candidate) -> dict[str, Any]:
    return {
        "trade_date": trade_date,
        "code": candidate.code,
        "name": candidate.name,
        "strategy_tags": json.dumps(candidate.strategy_tags, ensure_ascii=False),
        "score": candidate.score,
        "trigger_text": candidate.trigger,
        "invalidation_text": candidate.invalidation,
        "reasons": json.dumps(candidate.reasons, ensure_ascii=False),
        "score_parts": json.dumps(candidate.score_parts, ensure_ascii=False),
    }


def require_sqlalchemy():
    try:
        import sqlalchemy
    except ImportError as exc:
        raise DatabaseError("SQLAlchemy is not installed. Run `pip install -r requirements.txt`.") from exc
    return sqlalchemy


UNIQUE_KEY_COLUMNS = {
    "market_quotes": {"trade_date", "code", "source"},
    "index_quotes": {"trade_date", "name", "source"},
    "limit_pool": {"trade_date", "code", "source"},
    "hot_ranks": {"trade_date", "code", "source"},
    "hot_topics": {"trade_date", "topic_type", "name", "source"},
    "recap_candidates": {"trade_date", "code"},
    "recap_reports": {"trade_date"},
}


SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS fetch_runs (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        trade_date DATE NOT NULL,
        source VARCHAR(64) NOT NULL,
        status VARCHAR(32) NOT NULL,
        error TEXT NULL,
        started_at DATETIME NOT NULL,
        finished_at DATETIME NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS market_quotes (
        trade_date DATE NOT NULL,
        code VARCHAR(16) NOT NULL,
        name VARCHAR(64) NOT NULL,
        close_price DOUBLE NULL,
        change_pct DOUBLE NULL,
        turnover DOUBLE NULL,
        turnover_rate DOUBLE NULL,
        volume_ratio DOUBLE NULL,
        amplitude_pct DOUBLE NULL,
        source VARCHAR(32) NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (trade_date, code, source)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS index_quotes (
        trade_date DATE NOT NULL,
        name VARCHAR(64) NOT NULL,
        close_price DOUBLE NULL,
        change_pct DOUBLE NULL,
        source VARCHAR(32) NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (trade_date, name, source)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS limit_pool (
        trade_date DATE NOT NULL,
        code VARCHAR(16) NOT NULL,
        limit_up_days INT NULL,
        source VARCHAR(32) NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (trade_date, code, source)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS hot_ranks (
        trade_date DATE NOT NULL,
        code VARCHAR(16) NOT NULL,
        hot_rank INT NULL,
        source VARCHAR(32) NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (trade_date, code, source)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS hot_topics (
        trade_date DATE NOT NULL,
        topic_type VARCHAR(16) NOT NULL,
        name VARCHAR(128) NOT NULL,
        change_pct DOUBLE NULL,
        turnover DOUBLE NULL,
        source VARCHAR(32) NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (trade_date, topic_type, name, source)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS recap_candidates (
        trade_date DATE NOT NULL,
        code VARCHAR(16) NOT NULL,
        name VARCHAR(64) NOT NULL,
        strategy_tags JSON NOT NULL,
        score INT NOT NULL,
        trigger_text TEXT NOT NULL,
        invalidation_text TEXT NOT NULL,
        reasons JSON NOT NULL,
        score_parts JSON NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (trade_date, code)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS recap_reports (
        trade_date DATE PRIMARY KEY,
        title VARCHAR(255) NOT NULL,
        html_path VARCHAR(512) NOT NULL,
        text_path VARCHAR(512) NOT NULL,
        generated_at DATETIME NOT NULL,
        send_status VARCHAR(32) NOT NULL,
        sent_at DATETIME NULL,
        send_error TEXT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]
