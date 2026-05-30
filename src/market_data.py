from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from time import sleep
from typing import Any

import pandas as pd

from .trading_calendar import resolve_trade_date_from_config


class MarketDataError(RuntimeError):
    pass


@dataclass
class MarketData:
    spot: pd.DataFrame
    hot_rank: pd.DataFrame
    limit_pool: pd.DataFrame
    indexes: pd.DataFrame
    industries: pd.DataFrame
    concepts: pd.DataFrame
    trade_date: date
    warnings: list[str]


class AkshareMarketProvider:
    """Fetches public A-share market data through AKShare."""

    def fetch(self, config: dict[str, Any] | None = None, trade_date: date | None = None) -> MarketData:
        with bypass_proxy_for_data():
            try:
                import akshare as ak
            except ImportError as exc:
                raise MarketDataError(
                    "AKShare is not installed. Run `pip install -r requirements.txt` first."
                ) from exc

            warnings: list[str] = []
            trade_date = trade_date or resolve_trade_date_from_config(config or {})
            spot = self._fetch_spot(ak, warnings)

            hot_rank = self._optional_call(ak, "stock_hot_rank_em", warnings)
            limit_pool = self._fetch_limit_pool(ak, warnings, trade_date)
            indexes = self._optional_call(ak, "stock_zh_index_spot_em", warnings)
            industries = self._optional_call(ak, "stock_board_industry_name_em", warnings)
            concepts = self._optional_call(ak, "stock_board_concept_name_em", warnings)

            return MarketData(
                spot=spot,
                hot_rank=hot_rank,
                limit_pool=limit_pool,
                indexes=indexes,
                industries=industries,
                concepts=concepts,
                trade_date=trade_date,
                warnings=warnings,
            )

    def fetch_stock_basic(self) -> pd.DataFrame:
        with bypass_proxy_for_data():
            try:
                import akshare as ak
            except ImportError as exc:
                raise MarketDataError(
                    "AKShare is not installed. Run `pip install -r requirements.txt` first."
                ) from exc
            return self._call(ak.stock_info_a_code_name, "A-share stock basic")

    def fetch_daily_bars(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        with bypass_proxy_for_data():
            try:
                import akshare as ak
            except ImportError as exc:
                raise MarketDataError(
                    "AKShare is not installed. Run `pip install -r requirements.txt` first."
                ) from exc
            last_error: Exception | None = None
            for attempt in range(1, 4):
                try:
                    return ak.stock_zh_a_hist(
                        symbol=code,
                        period="daily",
                        start_date=start_date.strftime("%Y%m%d"),
                        end_date=end_date.strftime("%Y%m%d"),
                        adjust="",
                    )
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    if attempt < 3:
                        sleep(2)
            raise MarketDataError(f"Failed to fetch daily bars for {code}: {last_error}")

    def _fetch_spot(self, ak: Any, warnings: list[str]) -> pd.DataFrame:
        sources = [
            ("eastmoney", "stock_zh_a_spot_em"),
            ("sina", "stock_zh_a_spot"),
        ]
        errors: list[str] = []
        for source_name, func_name in sources:
            func = getattr(ak, func_name, None)
            if func is None:
                errors.append(f"{source_name}: AKShare function `{func_name}` is unavailable")
                continue
            last_error: MarketDataError | None = None
            for attempt in range(1, 4):
                try:
                    df = self._call(func, f"{source_name} A-share spot quote")
                except MarketDataError as exc:
                    last_error = exc
                    if attempt < 3:
                        sleep(2)
                    continue
                break
            else:
                if last_error:
                    errors.append(str(last_error))
                continue
            if source_name != "eastmoney":
                warnings.append(f"Primary Eastmoney spot source failed; used {source_name} fallback.")
            return df

        detail = " | ".join(errors)
        raise MarketDataError(
            "All A-share spot quote sources failed. "
            "Try switching DATA_BYPASS_PROXY in .env between true and false. "
            f"Details: {detail}"
        )

    @staticmethod
    def _call(func: Any, label: str) -> pd.DataFrame:
        try:
            df = func()
        except Exception as exc:  # noqa: BLE001 - surface provider failures clearly.
            raise MarketDataError(f"Failed to fetch {label}: {exc}") from exc
        if not isinstance(df, pd.DataFrame) or df.empty:
            raise MarketDataError(f"{label} returned no rows.")
        return df

    @staticmethod
    def _optional_call(ak: Any, name: str, warnings: list[str]) -> pd.DataFrame:
        func = getattr(ak, name, None)
        if func is None:
            warnings.append(f"AKShare function `{name}` is unavailable.")
            return pd.DataFrame()
        try:
            return func()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{name} fetch failed: {exc}")
            return pd.DataFrame()

    @staticmethod
    def _fetch_limit_pool(ak: Any, warnings: list[str], trade_date: date) -> pd.DataFrame:
        func = getattr(ak, "stock_zt_pool_em", None)
        if func is None:
            warnings.append("AKShare function `stock_zt_pool_em` is unavailable.")
            return pd.DataFrame()
        try:
            return func(date=trade_date.strftime("%Y%m%d"))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"stock_zt_pool_em fetch failed: {exc}")
            return pd.DataFrame()


@contextmanager
def bypass_proxy_for_data() -> Iterator[None]:
    """AKShare/Eastmoney endpoints often fail through local proxy software."""
    if os.getenv("DATA_BYPASS_PROXY", "true").strip().lower() in {"0", "false", "no"}:
        yield
        return

    proxy_keys = [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    ]
    previous = {key: os.environ.get(key) for key in proxy_keys}
    try:
        for key in proxy_keys:
            os.environ.pop(key, None)
        os.environ["NO_PROXY"] = "*"
        os.environ["no_proxy"] = "*"
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
