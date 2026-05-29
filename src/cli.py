from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from .analysis import build_recap
from .config import Settings, load_settings
from .market_data import AkshareMarketProvider, MarketDataError
from .email_notifier import EmailNotifier, NotifyError
from .report import render_report


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

    try:
        data = AkshareMarketProvider().fetch(settings.raw)
        recap = build_recap(data, settings.raw)
        title, html, text = render_report(recap, settings.push_title_prefix)
        report_date = recap.market.trade_date.isoformat()
    except (MarketDataError, ValueError) as exc:
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

    if args.dry_run:
        output = write_report_files(settings, html, text, report_date)
        print(f"Wrote dry-run report to {output}")

    if args.send:
        try:
            EmailNotifier.from_env().send(title=title, text_content=text, html_content=html)
        except NotifyError as exc:
            print(f"Failed to send email message: {exc}", file=sys.stderr)
            return 3
        print("Email message sent.")

    return 0


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
