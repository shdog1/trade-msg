from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from time import sleep

from .analysis import build_recap
from .config import Settings, load_settings
from .database import DatabaseError, MySQLStore
from .market_data import AkshareMarketProvider, MarketDataError
from .email_notifier import EmailNotifier, NotifyError
from .report import render_report
from .trading_calendar import load_trade_dates_from_akshare, now_in_timezone, resolve_trade_date_from_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and email A-share recap.")
    parser.add_argument("--config", default=None, help="Path to config.yaml.")
    parser.add_argument("--dry-run", action="store_true", help="Write report locally without sending.")
    parser.add_argument("--send", action="store_true", help="Send report by email.")
    parser.add_argument("--fetch-only", action="store_true", help="Fetch market data into MySQL without rendering.")
    parser.add_argument("--backfill-days", type=int, default=None, help="Backfill daily bars for recent N days.")
    parser.add_argument(
        "--backfill-stock",
        action="append",
        default=None,
        help="Backfill one or more stock codes. Can be repeated or comma-separated.",
    )
    parser.add_argument("--backfill-all", action="store_true", help="Backfill all A-share stocks instead of main board only.")
    parser.add_argument(
        "--backfill-sleep",
        type=float,
        default=1.0,
        help="Seconds to wait between daily bar requests during backfill.",
    )
    parser.add_argument("--date", default=None, help="Trade date to recap, e.g. 2026-05-29.")
    parser.add_argument("--scheduled", action="store_true", help="Skip automatically when today is not a trading day.")
    parser.add_argument("--refresh-calendar", action="store_true", help="Fetch and store the A-share trading calendar.")
    parser.add_argument("--web", action="store_true", help="Run local web console.")
    parser.add_argument("--test-email", action="store_true", help="Send a test email without fetching data.")
    args = parser.parse_args(argv)

    settings = load_settings(args.config)
    if args.web:
        from .web import run_server

        run_server()
        return 0

    if args.test_email:
        return send_test_email(settings.push_title_prefix)

    if not args.dry_run and not args.send and not args.fetch_only and args.backfill_days is None:
        args.dry_run = True

    trade_date = parse_trade_date(args.date) if args.date else resolve_trade_date_from_config(settings.raw)
    try:
        store = MySQLStore.from_env(settings.raw)
        store.initialize()
    except DatabaseError as exc:
        print(f"Failed to initialize MySQL: {exc}", file=sys.stderr)
        return 4

    if args.refresh_calendar:
        return refresh_trade_calendar(store, trade_date)

    if args.scheduled and not args.date and should_skip_scheduled_run(store, settings):
        print("Scheduled run skipped: today is not an A-share trading day.")
        return 0

    if args.fetch_only:
        return fetch_into_database(store, settings, trade_date)

    if args.backfill_days is not None:
        return backfill_daily_bars(
            store,
            settings,
            trade_date,
            args.backfill_days,
            parse_stock_codes(args.backfill_stock),
            max(args.backfill_sleep, 0),
            args.backfill_all,
        )

    try:
        ensure_market_data(store, settings, trade_date)
        data = store.load_market_data(trade_date)
        recap = build_recap(data, settings.raw)
        store.persist_recap(recap)
        title, html, text = render_report(recap, settings.push_title_prefix)
        report_date = recap.market.trade_date.isoformat()
    except (DatabaseError, MarketDataError, ValueError) as exc:
        if args.send:
            cached = load_recent_cached_report(settings)
            if cached:
                title, html, text = cached
                report_date = date_from_title(title)
                print(f"Live data fetch failed; sending cached report instead: {exc}", file=sys.stderr)
            else:
                print(f"Failed to build recap: {exc}", file=sys.stderr)
                return 2
        else:
            print(f"Failed to build recap: {exc}", file=sys.stderr)
            return 2

    if args.dry_run or args.send:
        output = write_report_files(settings, html, text, report_date)
        store.record_report(trade_date, title, str(output), str(settings.dated_text_output(report_date)), "generated")

    if args.dry_run:
        print(f"Wrote dry-run report to {output}")

    if args.send:
        try:
            EmailNotifier.from_env().send(title=title, text_content=text, html_content=html)
        except NotifyError as exc:
            store.mark_report_sent(trade_date, "failed", str(exc))
            print(f"Failed to send email message: {exc}", file=sys.stderr)
            return 3
        store.mark_report_sent(trade_date, "sent", None)
        print("Email message sent.")

    return 0


def parse_trade_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_stock_codes(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    codes: list[str] = []
    for value in values:
        for part in value.replace("，", ",").replace(" ", ",").split(","):
            code = part.strip()
            if code:
                codes.append(code)
    return codes or None


def refresh_trade_calendar(store: MySQLStore, trade_date: date) -> int:
    try:
        trade_dates = load_trade_dates_from_akshare()
        if not trade_dates:
            raise MarketDataError("Trading calendar returned no rows.")
        count = store.persist_trade_calendar(trade_dates)
        store.record_fetch_run(trade_date, "trade_calendar", "success", None)
    except (DatabaseError, MarketDataError, Exception) as exc:  # noqa: BLE001
        try:
            store.record_fetch_run(trade_date, "trade_calendar", "failed", str(exc))
        except DatabaseError:
            pass
        print(f"Failed to refresh trading calendar: {exc}", file=sys.stderr)
        return 2
    print(f"Refreshed trading calendar, rows={count}.")
    return 0


def should_skip_scheduled_run(store: MySQLStore, settings: Settings) -> bool:
    app = settings.raw.get("app", {})
    if not bool(app.get("skip_non_trading_day", True)):
        return False

    timezone = str(app.get("timezone", "Asia/Shanghai"))
    today = now_in_timezone(timezone).date()
    trade_dates = store.list_trade_dates()
    if not trade_dates:
        try:
            trade_dates = load_trade_dates_from_akshare()
            if trade_dates:
                store.persist_trade_calendar(trade_dates)
        except Exception:  # noqa: BLE001
            return True
    return today not in trade_dates


def fetch_into_database(store: MySQLStore, settings: Settings, trade_date: date) -> int:
    try:
        provider = AkshareMarketProvider()
        data = provider.fetch(settings.raw, trade_date=trade_date)
        stock_basic = provider.fetch_stock_basic()
        store.persist_market_data(data)
        store.persist_stock_basic(stock_basic, fallback_spot=data.spot)
    except (DatabaseError, MarketDataError) as exc:
        try:
            store.record_fetch_run(trade_date, "akshare", "failed", str(exc))
        except DatabaseError:
            pass
        print(f"Failed to fetch market data into MySQL: {exc}", file=sys.stderr)
        return 2
    print(f"Fetched market data into MySQL for {trade_date.isoformat()}.")
    return 0


def backfill_daily_bars(
    store: MySQLStore,
    settings: Settings,
    trade_date: date,
    days: int,
    stock_codes: list[str] | None,
    request_sleep: float = 1.0,
    include_all: bool = False,
) -> int:
    provider = AkshareMarketProvider()
    try:
        codes = stock_codes if stock_codes else store.list_stock_codes(main_board_only=not include_all)
        if not codes:
            basic = provider.fetch_stock_basic()
            store.persist_stock_basic(basic)
            codes = stock_codes if stock_codes else store.list_stock_codes(main_board_only=not include_all)
        start_date = trade_date - timedelta(days=max(days * 2, days + 30))
        total_rows = 0
        failures: list[tuple[str, str]] = []
        for index, code in enumerate(codes, start=1):
            latest = store.latest_daily_bar_date(code)
            code_start = latest + timedelta(days=1) if latest else start_date
            if code_start > trade_date:
                continue
            try:
                df, source = provider.fetch_daily_bars(code, code_start, trade_date)
                total_rows += store.persist_daily_bars(df, code, source="akshare")
                store.record_fetch_run(trade_date, f"daily_bars:{code}:{source}", "success", None)
            except (DatabaseError, MarketDataError) as exc:
                message = str(exc)
                failures.append((code, message))
                try:
                    store.record_fetch_run(trade_date, f"daily_bars:{code}", "failed", message)
                except DatabaseError:
                    pass
                if stock_codes:
                    raise
                print(f"Skip {code}: {message}", file=sys.stderr)
                sleep(max(request_sleep, 2.0))
                continue
            sleep(request_sleep)
            if index % 100 == 0:
                print(f"Backfilled {index}/{len(codes)} stocks, rows={total_rows}, failures={len(failures)}.")
    except (DatabaseError, MarketDataError) as exc:
        print(f"Failed to backfill daily bars: {exc}", file=sys.stderr)
        return 2
    print(f"Backfilled daily bars for {len(codes)} stocks, rows={total_rows}, failures={len(failures)}.")
    if failures:
        print("Failed stocks: " + ", ".join(code for code, _ in failures[:30]), file=sys.stderr)
        if len(failures) > 30:
            print(f"... and {len(failures) - 30} more failures.", file=sys.stderr)
    return 0


def ensure_market_data(store: MySQLStore, settings: Settings, trade_date: date) -> None:
    if store.has_market_data(trade_date):
        return
    provider = AkshareMarketProvider()
    data = provider.fetch(settings.raw, trade_date=trade_date)
    store.persist_market_data(data)
    try:
        store.persist_stock_basic(provider.fetch_stock_basic(), fallback_spot=data.spot)
    except (DatabaseError, MarketDataError):
        store.persist_stock_basic(data.spot, fallback_spot=data.spot)


def load_recent_cached_report(settings: Settings) -> tuple[str, str, str] | None:
    path = settings.dry_run_output
    if not path.exists():
        return None

    max_age_hours = float(settings.raw.get("notify", {}).get("cache_max_age_hours", 18))
    modified_at = datetime.fromtimestamp(path.stat().st_mtime)
    age_hours = (datetime.now() - modified_at).total_seconds() / 3600
    if age_hours > max_age_hours:
        return None

    html = path.read_text(encoding="utf-8")
    text_path = path.with_suffix(".txt")
    text = text_path.read_text(encoding="utf-8") if text_path.exists() else html_to_text_fallback(html)
    title = title_from_html(html, settings.push_title_prefix)
    return title, html, text


def write_report_files(settings: Settings, html: str, text: str, date_text: str) -> Path:
    dated_html = settings.dated_report_output(date_text)
    dated_html.parent.mkdir(parents=True, exist_ok=True)
    dated_html.write_text(html, encoding="utf-8")
    settings.dated_text_output(date_text).write_text(text, encoding="utf-8")

    latest_html = settings.dry_run_output
    latest_html.parent.mkdir(parents=True, exist_ok=True)
    latest_html.write_text(html, encoding="utf-8")
    latest_html.with_suffix(".txt").write_text(text, encoding="utf-8")
    return dated_html


def title_from_html(content: str, fallback: str) -> str:
    start = content.find("<h1>")
    end = content.find("</h1>")
    if start >= 0 and end > start:
        return content[start + 4 : end].strip() or fallback
    return fallback


def html_to_text_fallback(content: str) -> str:
    return content.replace("<br>", "\n").replace("</p>", "\n").replace("</tr>", "\n")


def date_from_title(title: str) -> str:
    for part in title.split():
        if len(part) == 10 and part[4] == "-" and part[7] == "-":
            return part
    return datetime.now().date().isoformat()


def send_test_email(title_prefix: str) -> int:
    title = f"{title_prefix} email test"
    content = (
        "This is a test email from trade-msg.\n\n"
        "If you received it, SMTP notification is configured correctly.\n"
    )
    try:
        EmailNotifier.from_env().send(title=title, text_content=content)
    except NotifyError as exc:
        print(f"Failed to send test email: {exc}", file=sys.stderr)
        return 3
    print("Test email sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
