# trade-msg

A-share short-term market recap notifier. It builds a daily post-close review,
scores leading-stock watch candidates, and sends the report by email.

This tool is for research and review only. It is not investment advice.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set the SMTP fields. Use an app password or authorization code
instead of the normal login password.

Run a dry report without sending:

```powershell
python -m src.cli --dry-run
```

The dry run writes:

- `reports/latest.html`
- `reports/latest.txt`
- `reports/YYYY-MM-DD/recap.html`
- `reports/YYYY-MM-DD/recap.txt`

Send a real email message:

```powershell
python -m src.cli --send
```

Test email settings without fetching market data:

```powershell
python -m src.cli --test-email
```

Install a Windows scheduled task for 18:00 China time:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows_task.ps1
```

Run tests:

```powershell
python -m unittest discover -s tests -v
```

## What the report contains

- Market breadth and turnover overview.
- Indexes, breadth, limit-up/limit-down counts, turnover, and sentiment.
- Limit-up pool, board height, hot-rank leader context, industry heat, and
  concept heat when available.
- Main-board candidates only by default.
- Strategy tags for leading-stock rebound, low-suction pullback, and second-wave
  setups.
- A 0-100 watch score with explicit trigger and invalidation notes.

## Configuration

Most behavior lives in `config.yaml`:

- main-board code prefixes and exclusions
- score weights
- candidate count
- HTML email and archive output
- task time

Secrets live in `.env` and are not committed.
