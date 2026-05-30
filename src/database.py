from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import quote_plus

import pandas as pd

from .analysis import (
    normalize_stock_code,
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
            self._migrate_primary_keys_and_indexes(conn)

    def _migrate_primary_keys_and_indexes(self, conn: Any) -> None:
        for table, unique_columns in UNIQUE_KEY_COLUMNS.items():
            ensure_surrogate_primary_key(conn, self.config.database, table, unique_columns)
        for table, indexes in INDEX_COLUMNS.items():
            for index_name, columns in indexes.items():
                ensure_index(conn, self.config.database, table, index_name, columns, unique=False)
        for table, columns in UNIQUE_KEY_COLUMNS.items():
            ensure_index(conn, self.config.database, table, f"uk_{table}_business", columns, unique=True)

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
            self._upsert_many(conn, "quote_snapshots", quote_snapshot_rows(market_rows))
            self._upsert_many(conn, "hot_ranks", hot_rank_rows)
            self._upsert_many(conn, "limit_pool", limit_rows)
            self._upsert_many(conn, "index_quotes", index_rows)
            self._upsert_many(conn, "hot_topics", topic_rows)

        self.record_fetch_run(data.trade_date, "akshare", "success", None)

    def persist_stock_basic(self, df: pd.DataFrame, fallback_spot: pd.DataFrame | None = None) -> None:
        self.initialize()
        rows = stock_basic_rows(df)
        if not rows and fallback_spot is not None:
            rows = stock_basic_rows_from_spot(fallback_spot)
        with self.engine.begin() as conn:
            self._upsert_many(conn, "stock_basic", rows)

    def list_stock_codes(self, main_board_only: bool = True) -> list[str]:
        where = "WHERE is_main_board = 1" if main_board_only else ""
        df = self._read_df(f"SELECT code FROM stock_basic {where} ORDER BY code", {})
        if df.empty:
            return []
        return [str(item) for item in df["code"].dropna().tolist()]

    def latest_daily_bar_date(self, code: str) -> date | None:
        sqlalchemy = require_sqlalchemy()
        with self.engine.begin() as conn:
            value = conn.execute(
                sqlalchemy.text("SELECT MAX(trade_date) FROM daily_bars WHERE code = :code"),
                {"code": code},
            ).scalar_one_or_none()
        return value

    def persist_daily_bars(self, df: pd.DataFrame, code: str, source: str = "akshare") -> int:
        self.initialize()
        rows = daily_bar_rows(df, code, source)
        with self.engine.begin() as conn:
            self._upsert_many(conn, "daily_bars", rows)
        return len(rows)

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
        columns = [col for col in rows[0].keys() if col != "id"]
        update_columns = [col for col in columns if col not in UNIQUE_KEY_COLUMNS[table]]
        insert_cols = ", ".join(f"`{col}`" for col in columns)
        values_cols = ", ".join(f":{col}" for col in columns)
        update_clause = ", ".join(f"`{col}` = VALUES(`{col}`)" for col in update_columns)
        if not update_clause:
            update_clause = f"`{columns[0]}` = VALUES(`{columns[0]}`)"
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


def stock_basic_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    code_col = first_existing(df, ["code", "symbol", "\u4ee3\u7801", "\u80a1\u7968\u4ee3\u7801"])
    name_col = first_existing(df, ["name", "\u540d\u79f0", "\u80a1\u7968\u7b80\u79f0"])
    if not code_col or not name_col:
        return []
    rows: list[dict[str, Any]] = []
    for item in df.to_dict("records"):
        code = normalize_stock_code(item.get(code_col))
        if not code:
            continue
        name = str(item.get(name_col, ""))
        rows.append(stock_basic_row(code, name))
    return rows


def stock_basic_rows_from_spot(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    spot = normalize_spot(df)
    return [stock_basic_row(row.code, row.name) for row in spot.itertuples()]


def stock_basic_row(code: str, name: str) -> dict[str, Any]:
    market = infer_market(code)
    return {
        "code": code,
        "name": name,
        "market": market,
        "exchange": infer_exchange(code),
        "is_main_board": 1 if is_main_board_code(code) else 0,
        "is_st": 1 if "ST" in name.upper() else 0,
        "listing_status": "listed",
        "source": "akshare",
    }


def quote_snapshot_rows(market_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fetched_at = datetime.now()
    return [
        {
            "trade_date": row["trade_date"],
            "code": row["code"],
            "name": row["name"],
            "close_price": row["close_price"],
            "change_pct": row["change_pct"],
            "turnover": row["turnover"],
            "turnover_rate": row["turnover_rate"],
            "volume_ratio": row["volume_ratio"],
            "amplitude_pct": row["amplitude_pct"],
            "source": row["source"],
            "fetched_at": fetched_at,
        }
        for row in market_rows
    ]


def daily_bar_rows(df: pd.DataFrame, code: str, source: str) -> list[dict[str, Any]]:
    if df.empty:
        return []
    aliases = {
        "trade_date": ["\u65e5\u671f", "date", "trade_date"],
        "name": ["\u540d\u79f0", "name"],
        "open_price": ["\u5f00\u76d8", "open"],
        "high_price": ["\u6700\u9ad8", "high"],
        "low_price": ["\u6700\u4f4e", "low"],
        "close_price": ["\u6536\u76d8", "close"],
        "change_pct": ["\u6da8\u8dcc\u5e45", "change_pct"],
        "volume": ["\u6210\u4ea4\u91cf", "volume"],
        "turnover": ["\u6210\u4ea4\u989d", "amount", "turnover"],
        "amplitude_pct": ["\u632f\u5e45", "amplitude"],
        "turnover_rate": ["\u6362\u624b\u7387", "turnover_rate"],
    }
    columns = {target: first_existing(df, names) for target, names in aliases.items()}
    if not columns["trade_date"]:
        return []
    rows: list[dict[str, Any]] = []
    for item in df.to_dict("records"):
        parsed_date = pd.to_datetime(item.get(columns["trade_date"]), errors="coerce")
        if pd.isna(parsed_date):
            continue
        rows.append(
            {
                "trade_date": parsed_date.date(),
                "code": code,
                "name": str(item.get(columns["name"], "")) if columns["name"] else None,
                "open_price": optional_float(item.get(columns["open_price"])) if columns["open_price"] else None,
                "high_price": optional_float(item.get(columns["high_price"])) if columns["high_price"] else None,
                "low_price": optional_float(item.get(columns["low_price"])) if columns["low_price"] else None,
                "close_price": optional_float(item.get(columns["close_price"])) if columns["close_price"] else None,
                "change_pct": optional_float(item.get(columns["change_pct"])) if columns["change_pct"] else None,
                "volume": optional_float(item.get(columns["volume"])) if columns["volume"] else None,
                "turnover": optional_float(item.get(columns["turnover"])) if columns["turnover"] else None,
                "turnover_rate": optional_float(item.get(columns["turnover_rate"])) if columns["turnover_rate"] else None,
                "amplitude_pct": optional_float(item.get(columns["amplitude_pct"])) if columns["amplitude_pct"] else None,
                "source": source,
            }
        )
    return rows


def first_existing(df: pd.DataFrame, names: list[str]) -> str | None:
    return next((name for name in names if name in df.columns), None)


def optional_float(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def infer_exchange(code: str) -> str:
    if code.startswith(("600", "601", "603", "605", "688")):
        return "SH"
    if code.startswith(("000", "001", "002", "003", "300")):
        return "SZ"
    if code.startswith(("8", "4", "9")):
        return "BJ"
    return ""


def infer_market(code: str) -> str:
    if code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return "main_board"
    if code.startswith("300"):
        return "chinext"
    if code.startswith("688"):
        return "star"
    if code.startswith(("8", "4", "9")):
        return "beijing"
    return "unknown"


def is_main_board_code(code: str) -> bool:
    return code.startswith(("600", "601", "603", "605", "000", "001", "002", "003"))


def require_sqlalchemy():
    try:
        import sqlalchemy
    except ImportError as exc:
        raise DatabaseError("SQLAlchemy is not installed. Run `pip install -r requirements.txt`.") from exc
    return sqlalchemy


def has_column(conn: Any, database: str, table: str, column: str) -> bool:
    sqlalchemy = require_sqlalchemy()
    count = conn.execute(
        sqlalchemy.text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = :database AND table_name = :table AND column_name = :column"
        ),
        {"database": database, "table": table, "column": column},
    ).scalar_one()
    return int(count) > 0


def primary_key_columns(conn: Any, database: str, table: str) -> list[str]:
    sqlalchemy = require_sqlalchemy()
    rows = conn.execute(
        sqlalchemy.text(
            "SELECT column_name FROM information_schema.statistics "
            "WHERE table_schema = :database AND table_name = :table AND index_name = 'PRIMARY' "
            "ORDER BY seq_in_index"
        ),
        {"database": database, "table": table},
    ).all()
    return [row[0] for row in rows]


def has_index(conn: Any, database: str, table: str, index_name: str) -> bool:
    sqlalchemy = require_sqlalchemy()
    count = conn.execute(
        sqlalchemy.text(
            "SELECT COUNT(*) FROM information_schema.statistics "
            "WHERE table_schema = :database AND table_name = :table AND index_name = :index_name"
        ),
        {"database": database, "table": table, "index_name": index_name},
    ).scalar_one()
    return int(count) > 0


def ensure_surrogate_primary_key(conn: Any, database: str, table: str, business_columns: list[str]) -> None:
    sqlalchemy = require_sqlalchemy()
    pk_columns = primary_key_columns(conn, database, table)
    if pk_columns == ["id"]:
        ensure_index(conn, database, table, f"uk_{table}_business", business_columns, unique=True)
        return

    if pk_columns:
        conn.execute(sqlalchemy.text(f"ALTER TABLE `{table}` DROP PRIMARY KEY"))

    if has_column(conn, database, table, "id"):
        conn.execute(sqlalchemy.text(f"ALTER TABLE `{table}` MODIFY COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY"))
    else:
        conn.execute(sqlalchemy.text(f"ALTER TABLE `{table}` ADD COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST"))

    ensure_index(conn, database, table, f"uk_{table}_business", business_columns, unique=True)


def ensure_index(conn: Any, database: str, table: str, index_name: str, columns: list[str], unique: bool) -> None:
    if has_index(conn, database, table, index_name):
        return
    sqlalchemy = require_sqlalchemy()
    unique_sql = "UNIQUE " if unique else ""
    column_sql = ", ".join(f"`{column}`" for column in columns)
    conn.execute(sqlalchemy.text(f"CREATE {unique_sql}INDEX `{index_name}` ON `{table}` ({column_sql})"))


UNIQUE_KEY_COLUMNS = {
    "stock_basic": ["code"],
    "market_quotes": ["trade_date", "code", "source"],
    "quote_snapshots": ["trade_date", "code", "source", "fetched_at"],
    "daily_bars": ["trade_date", "code", "source"],
    "index_quotes": ["trade_date", "name", "source"],
    "limit_pool": ["trade_date", "code", "source"],
    "hot_ranks": ["trade_date", "code", "source"],
    "hot_topics": ["trade_date", "topic_type", "name", "source"],
    "recap_candidates": ["trade_date", "code"],
    "recap_reports": ["trade_date"],
}


INDEX_COLUMNS = {
    "fetch_runs": {
        "idx_fetch_runs_trade_source_status": ["trade_date", "source", "status"],
        "idx_fetch_runs_created_at": ["created_at"],
    },
    "stock_basic": {
        "idx_stock_basic_is_main_board": ["is_main_board"],
        "idx_stock_basic_is_st": ["is_st"],
        "idx_stock_basic_market": ["market"],
    },
    "market_quotes": {
        "idx_market_quotes_trade_date": ["trade_date"],
        "idx_market_quotes_code": ["code"],
        "idx_market_quotes_trade_turnover": ["trade_date", "turnover"],
        "idx_market_quotes_trade_change": ["trade_date", "change_pct"],
    },
    "quote_snapshots": {
        "idx_quote_snapshots_code_time": ["code", "fetched_at"],
        "idx_quote_snapshots_trade_time": ["trade_date", "fetched_at"],
    },
    "daily_bars": {
        "idx_daily_bars_code_trade": ["code", "trade_date"],
        "idx_daily_bars_trade_date": ["trade_date"],
        "idx_daily_bars_trade_turnover": ["trade_date", "turnover"],
    },
    "index_quotes": {
        "idx_index_quotes_trade_date": ["trade_date"],
    },
    "limit_pool": {
        "idx_limit_pool_trade_days": ["trade_date", "limit_up_days"],
        "idx_limit_pool_code_trade": ["code", "trade_date"],
    },
    "hot_ranks": {
        "idx_hot_ranks_trade_rank": ["trade_date", "hot_rank"],
        "idx_hot_ranks_code_trade": ["code", "trade_date"],
    },
    "hot_topics": {
        "idx_hot_topics_trade_type_change": ["trade_date", "topic_type", "change_pct"],
    },
    "recap_candidates": {
        "idx_recap_candidates_trade_score": ["trade_date", "score"],
        "idx_recap_candidates_code_trade": ["code", "trade_date"],
    },
    "recap_reports": {
        "idx_recap_reports_send_status": ["send_status"],
        "idx_recap_reports_generated_at": ["generated_at"],
    },
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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        KEY idx_fetch_runs_trade_source_status (trade_date, source, status),
        KEY idx_fetch_runs_created_at (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_basic (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        code VARCHAR(16) NOT NULL,
        name VARCHAR(64) NOT NULL,
        market VARCHAR(32) NULL,
        exchange VARCHAR(16) NULL,
        is_main_board TINYINT(1) NOT NULL DEFAULT 0,
        is_st TINYINT(1) NOT NULL DEFAULT 0,
        listing_status VARCHAR(32) NULL,
        source VARCHAR(32) NOT NULL DEFAULT 'akshare',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_stock_basic_business (code),
        KEY idx_stock_basic_is_main_board (is_main_board),
        KEY idx_stock_basic_is_st (is_st),
        KEY idx_stock_basic_market (market)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS market_quotes (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
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
        UNIQUE KEY uk_market_quotes_business (trade_date, code, source),
        KEY idx_market_quotes_trade_date (trade_date),
        KEY idx_market_quotes_code (code),
        KEY idx_market_quotes_trade_turnover (trade_date, turnover),
        KEY idx_market_quotes_trade_change (trade_date, change_pct)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS quote_snapshots (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
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
        fetched_at DATETIME NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_quote_snapshots_business (trade_date, code, source, fetched_at),
        KEY idx_quote_snapshots_code_time (code, fetched_at),
        KEY idx_quote_snapshots_trade_time (trade_date, fetched_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_bars (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        trade_date DATE NOT NULL,
        code VARCHAR(16) NOT NULL,
        name VARCHAR(64) NULL,
        open_price DOUBLE NULL,
        high_price DOUBLE NULL,
        low_price DOUBLE NULL,
        close_price DOUBLE NULL,
        change_pct DOUBLE NULL,
        volume DOUBLE NULL,
        turnover DOUBLE NULL,
        turnover_rate DOUBLE NULL,
        amplitude_pct DOUBLE NULL,
        source VARCHAR(32) NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_daily_bars_business (trade_date, code, source),
        KEY idx_daily_bars_code_trade (code, trade_date),
        KEY idx_daily_bars_trade_date (trade_date),
        KEY idx_daily_bars_trade_turnover (trade_date, turnover)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS index_quotes (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        trade_date DATE NOT NULL,
        name VARCHAR(64) NOT NULL,
        close_price DOUBLE NULL,
        change_pct DOUBLE NULL,
        source VARCHAR(32) NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_index_quotes_business (trade_date, name, source),
        KEY idx_index_quotes_trade_date (trade_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS limit_pool (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        trade_date DATE NOT NULL,
        code VARCHAR(16) NOT NULL,
        limit_up_days INT NULL,
        source VARCHAR(32) NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_limit_pool_business (trade_date, code, source),
        KEY idx_limit_pool_trade_days (trade_date, limit_up_days),
        KEY idx_limit_pool_code_trade (code, trade_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS hot_ranks (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        trade_date DATE NOT NULL,
        code VARCHAR(16) NOT NULL,
        hot_rank INT NULL,
        source VARCHAR(32) NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_hot_ranks_business (trade_date, code, source),
        KEY idx_hot_ranks_trade_rank (trade_date, hot_rank),
        KEY idx_hot_ranks_code_trade (code, trade_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS hot_topics (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        trade_date DATE NOT NULL,
        topic_type VARCHAR(16) NOT NULL,
        name VARCHAR(128) NOT NULL,
        change_pct DOUBLE NULL,
        turnover DOUBLE NULL,
        source VARCHAR(32) NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_hot_topics_business (trade_date, topic_type, name, source),
        KEY idx_hot_topics_trade_type_change (trade_date, topic_type, change_pct)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS recap_candidates (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
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
        UNIQUE KEY uk_recap_candidates_business (trade_date, code),
        KEY idx_recap_candidates_trade_score (trade_date, score),
        KEY idx_recap_candidates_code_trade (code, trade_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS recap_reports (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        trade_date DATE NOT NULL,
        title VARCHAR(255) NOT NULL,
        html_path VARCHAR(512) NOT NULL,
        text_path VARCHAR(512) NOT NULL,
        generated_at DATETIME NOT NULL,
        send_status VARCHAR(32) NOT NULL,
        sent_at DATETIME NULL,
        send_error TEXT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_recap_reports_business (trade_date),
        KEY idx_recap_reports_send_status (send_status),
        KEY idx_recap_reports_generated_at (generated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]
