from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .analysis import build_recap
from .config import Settings, load_settings
from .market_data import AkshareMarketProvider, MarketDataError
from .email_notifier import EmailNotifier, NotifyError
from .report import render_markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and email A-share recap.")
    parser.add_argument("--config", default=None, help="Path to config.yaml.")
    parser.add_argument("--dry-run", action="store_true", help="Write report locally without sending.")
    parser.add_argument("--send", action="store_true", help="Send report by email.")
    parser.add_argument("--test-email", action="store_true", help="Send a test email without fetching data.")
    args = parser.parse_args(argv)

    settings = load_settings(args.config)
    if args.test_email:
        return send_test_email(settings.push_title_prefix)

    if not args.dry_run and not args.send:
        args.dry_run = True

    if should_skip_today(settings.raw):
        print("Today is not a configured trading day. Skipping recap.")
        return 0

    try:
        data = AkshareMarketProvider().fetch()
        recap = build_recap(data, settings.raw)
        title, markdown = render_markdown(recap, settings.push_title_prefix)
    except (MarketDataError, ValueError) as exc:
        if args.send:
            cached = load_recent_cached_report(settings)
            if cached:
                title, markdown = cached
                print(f"Live data fetch failed; sending cached report instead: {exc}", file=sys.stderr)
            else:
                print(f"Failed to build recap: {exc}", file=sys.stderr)
                return 2
        else:
            print(f"Failed to build recap: {exc}", file=sys.stderr)
            return 2

    if args.dry_run:
        output = settings.dry_run_output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8")
        print(f"Wrote dry-run report to {output}")

    if args.send:
        try:
            EmailNotifier.from_env().send(title=title, content=markdown)
        except NotifyError as exc:
            print(f"Failed to send email message: {exc}", file=sys.stderr)
            return 3
        print("Email message sent.")

    return 0


def load_recent_cached_report(settings: Settings) -> tuple[str, str] | None:
    path = settings.dry_run_output
    if not path.exists():
        return None

    max_age_hours = float(settings.raw.get("notify", {}).get("cache_max_age_hours", 18))
    modified_at = datetime.fromtimestamp(path.stat().st_mtime)
    age_hours = (datetime.now() - modified_at).total_seconds() / 3600
    if age_hours > max_age_hours:
        return None

    content = path.read_text(encoding="utf-8")
    title = title_from_markdown(content, settings.push_title_prefix)
    return title, content


def title_from_markdown(content: str, fallback: str) -> str:
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or fallback
    return fallback


def send_test_email(title_prefix: str) -> int:
    title = f"{title_prefix} email test"
    content = (
        "This is a test email from trade-msg.\n\n"
        "If you received it, SMTP notification is configured correctly.\n"
    )
    try:
        EmailNotifier.from_env().send(title=title, content=content)
    except NotifyError as exc:
        print(f"Failed to send test email: {exc}", file=sys.stderr)
        return 3
    print("Test email sent.")
    return 0


def should_skip_today(config: dict) -> bool:
    if not config.get("app", {}).get("skip_non_trading_day", True):
        return False
    timezone = config.get("app", {}).get("timezone", "Asia/Shanghai")
    try:
        today = datetime.now(ZoneInfo(timezone)).date()
    except ZoneInfoNotFoundError:
        today = datetime.now().date()
    return today.weekday() >= 5


if __name__ == "__main__":
    raise SystemExit(main())
