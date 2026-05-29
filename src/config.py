from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    raw: dict[str, Any]
    root: Path = ROOT

    @property
    def max_candidates(self) -> int:
        return int(self.raw["market"].get("max_candidates", 8))

    @property
    def dry_run_output(self) -> Path:
        configured = self.raw["app"].get("dry_run_output", "reports/latest.md")
        return self.root / configured

    @property
    def push_title_prefix(self) -> str:
        notify = self.raw.get("notify", {})
        legacy_push = self.raw.get("push", {})
        return str(
            notify.get("title_prefix")
            or legacy_push.get("title_prefix")
            or "A-share short-term recap"
        )


def load_settings(path: str | Path | None = None) -> Settings:
    load_dotenv(ROOT / ".env")
    config_path = Path(path) if path else ROOT / "config.yaml"
    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return Settings(raw=raw)

