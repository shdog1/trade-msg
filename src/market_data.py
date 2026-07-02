from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from time import sleep
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup

from .trading_calendar import resolve_trade_date_from_config


class MarketDataError(RuntimeError):
    pass


DAILY_BAR_ADJUST = "qfq"
TENCENT_SPOT_URL = "https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList"
TENCENT_SPOT_PAGE_SIZE = 200
SPOT_METRIC_COLUMNS = ("成交量", "换手率", "量比", "振幅", "总市值")
LIMIT_REASON_URL = "https://dabanke.com/gupiao-{code}.html"


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
    daily_bars: pd.DataFrame = field(default_factory=pd.DataFrame)


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
            spot = self._supplement_spot_metrics(spot, warnings)

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

    def fetch_daily_bars(self, code: str, start_date: date, end_date: date) -> tuple[pd.DataFrame, str]:
        with bypass_proxy_for_data():
            try:
                import akshare as ak
            except ImportError as exc:
                raise MarketDataError(
                    "AKShare is not installed. Run `pip install -r requirements.txt` first."
                ) from exc

            start = start_date.strftime("%Y%m%d")
            end = end_date.strftime("%Y%m%d")
            exchange_symbol = to_exchange_symbol(code)
            sources = [
                (
                    "eastmoney",
                    lambda: ak.stock_zh_a_hist(
                        symbol=code,
                        period="daily",
                        start_date=start,
                        end_date=end,
                        adjust=DAILY_BAR_ADJUST,
                    ),
                ),
                (
                    "tencent",
                    lambda: ak.stock_zh_a_hist_tx(
                        symbol=exchange_symbol,
                        start_date=start,
                        end_date=end,
                        adjust=DAILY_BAR_ADJUST,
                    ),
                ),
                (
                    "sina",
                    lambda: ak.stock_zh_a_daily(
                        symbol=exchange_symbol,
                        start_date=start,
                        end_date=end,
                        adjust=DAILY_BAR_ADJUST,
                    ),
                ),
            ]
            errors: list[str] = []
            for source_name, fetcher in sources:
                last_error: Exception | None = None
                for attempt in range(1, 3):
                    try:
                        df = fetcher()
                    except Exception as exc:  # noqa: BLE001
                        last_error = exc
                        if attempt < 2:
                            sleep(1.5)
                        continue
                    if isinstance(df, pd.DataFrame) and not df.empty:
                        return df, source_name
                    last_error = MarketDataError("returned no rows")
                    break
                errors.append(f"{source_name}: {last_error}")
                sleep(0.5)
            raise MarketDataError(f"Failed to fetch daily bars for {code}: {' | '.join(errors)}")

    def fetch_limit_pool(self, trade_date: date) -> pd.DataFrame:
        with bypass_proxy_for_data():
            try:
                import akshare as ak
            except ImportError as exc:
                raise MarketDataError(
                    "AKShare is not installed. Run `pip install -r requirements.txt` first."
                ) from exc

            func = getattr(ak, "stock_zt_pool_em", None)
            if func is None:
                raise MarketDataError("AKShare function `stock_zt_pool_em` is unavailable.")
            try:
                df = func(date=trade_date.strftime("%Y%m%d"))
            except Exception as exc:  # noqa: BLE001
                raise MarketDataError(f"Failed to fetch limit-up pool for {trade_date.isoformat()}: {exc}") from exc
            if not isinstance(df, pd.DataFrame) or df.empty:
                raise MarketDataError(f"Limit-up pool for {trade_date.isoformat()} returned no rows.")
            return df

    def fetch_limit_up_reasons(self, codes: list[str], as_of_date: date) -> list[dict[str, Any]]:
        with bypass_proxy_for_data():
            with ThreadPoolExecutor(max_workers=6) as executor:
                rows = list(executor.map(lambda code: self._fetch_limit_up_reason(code, as_of_date), codes))
        return [row for row in rows if row is not None]

    @staticmethod
    def _fetch_limit_up_reason(code: str, as_of_date: date) -> dict[str, Any] | None:
        normalized = str(code).strip()[-6:]
        try:
            response = requests.get(
                LIMIT_REASON_URL.format(code=normalized),
                timeout=20,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            response.raise_for_status()
            history = parse_limit_up_reason_history(response.text)
            match = next(
                (
                    item
                    for item in history
                    if item["event_date"] == as_of_date and item["event_type"] == "涨停"
                ),
                None,
            )
            return {
                "as_of_date": as_of_date,
                "code": normalized,
                "event_date": match["event_date"] if match else None,
                "event_type": match["event_type"] if match else None,
                "reason": match["reason"] if match else None,
                "source": "dabanke",
            }
        except (requests.RequestException, ValueError):
            return None

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

    def _supplement_spot_metrics(self, spot: pd.DataFrame, warnings: list[str]) -> pd.DataFrame:
        missing = [column for column in SPOT_METRIC_COLUMNS if metric_coverage(spot, column) < 0.9]
        if not missing:
            return spot
        try:
            metrics = self._fetch_tencent_spot_metrics()
        except MarketDataError as exc:
            warnings.append(f"Key spot metrics remain incomplete ({', '.join(missing)}): {exc}")
            return spot
        supplemented = merge_missing_spot_metrics(spot, metrics)
        remaining = [column for column in missing if metric_coverage(supplemented, column) < 0.9]
        if remaining:
            warnings.append(f"Key spot metrics remain incomplete after Tencent supplement: {', '.join(remaining)}.")
        else:
            warnings.append(f"Supplemented missing spot metrics from Tencent: {', '.join(missing)}.")
        return supplemented

    def _fetch_tencent_spot_metrics(self) -> pd.DataFrame:
        records: list[dict[str, Any]] = []
        offset = 0
        total: int | None = None
        while total is None or offset < total:
            params = {
                "_appver": "11.17.0",
                "board_code": "aStock",
                "sort_type": "price",
                "direct": "down",
                "offset": str(offset),
                "count": str(TENCENT_SPOT_PAGE_SIZE),
            }
            try:
                response = requests.get(TENCENT_SPOT_URL, params=params, timeout=20)
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") or {}
                page = data.get("rank_list") or []
                total = int(data.get("total") or 0)
            except (requests.RequestException, AttributeError, KeyError, ValueError, TypeError) as exc:
                raise MarketDataError(f"Failed to fetch Tencent spot metrics at offset {offset}: {exc}") from exc
            if not page:
                break
            records.extend(page)
            offset += len(page)
        if not records:
            raise MarketDataError("Tencent spot metrics returned no rows.")
        return normalize_tencent_spot_metrics(pd.DataFrame(records))

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


def to_exchange_symbol(code: str) -> str:
    normalized = str(code).strip()[-6:]
    if normalized.startswith(("600", "601", "603", "605", "688", "689")):
        return f"sh{normalized}"
    return f"sz{normalized}"


def metric_coverage(df: pd.DataFrame, column: str) -> float:
    if df.empty or column not in df.columns:
        return 0.0
    values = pd.to_numeric(df[column], errors="coerce")
    return float(values.gt(0).sum()) / len(df)


def normalize_tencent_spot_metrics(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "code" not in df.columns:
        return pd.DataFrame(columns=["代码", *SPOT_METRIC_COLUMNS])
    result = pd.DataFrame({"代码": df["code"].astype(str).str.extract(r"(\d{6})", expand=False)})
    mappings = {
        "成交量": ("volume", 100.0),
        "成交额": ("turnover", 10_000.0),
        "换手率": ("hsl", 1.0),
        "量比": ("lb", 1.0),
        "振幅": ("zf", 1.0),
        "总市值": ("zsz", 100_000_000.0),
    }
    for target, (source, multiplier) in mappings.items():
        if source in df.columns:
            result[target] = pd.to_numeric(df[source], errors="coerce") * multiplier
    return result.dropna(subset=["代码"]).drop_duplicates("代码", keep="last")


def merge_missing_spot_metrics(spot: pd.DataFrame, metrics: pd.DataFrame) -> pd.DataFrame:
    if spot.empty or metrics.empty:
        return spot
    result = spot.copy()
    code_column = next((column for column in ("代码", "股票代码", "symbol", "code") if column in result.columns), None)
    if code_column is None or "代码" not in metrics.columns:
        return result
    result["_metric_code"] = result[code_column].astype(str).str.extract(r"(\d{6})", expand=False)
    metric_lookup = metrics.set_index("代码")
    for column in metrics.columns:
        if column == "代码":
            continue
        supplement = result["_metric_code"].map(metric_lookup[column])
        if column not in result.columns:
            result[column] = supplement
            continue
        existing = pd.to_numeric(result[column], errors="coerce")
        result[column] = existing.where(existing.gt(0), supplement)
    return result.drop(columns=["_metric_code"])


def parse_limit_up_reason_history(page_html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(page_html, "html.parser")
    heading = soup.find(string=lambda value: bool(value and value.strip() == "涨停原因"))
    table = heading.find_next("table") if heading else None
    if table is None:
        return []
    rows: list[dict[str, Any]] = []
    for table_row in table.find_all("tr"):
        raw_cells = table_row.find_all(["th", "td"])
        cells = [cell.get_text(" ", strip=True) for cell in raw_cells]
        if len(cells) < 4 or cells[0] == "日期":
            continue
        parsed_date = pd.to_datetime(cells[0], errors="coerce")
        if pd.isna(parsed_date):
            continue
        reason_parts = [
            str(cell.get("title") or cell.get_text(" ", strip=True)).strip()
            for cell in raw_cells[3:]
        ]
        reason = " · ".join(part for part in reason_parts if part).strip()
        rows.append(
            {
                "event_date": parsed_date.date(),
                "event_type": cells[2].strip(),
                "reason": reason or None,
            }
        )
    return sorted(rows, key=lambda item: item["event_date"], reverse=True)
