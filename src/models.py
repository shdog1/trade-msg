from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class IndexSnapshot:
    name: str
    close: float | None
    change_pct: float | None


@dataclass(frozen=True)
class HotTopic:
    name: str
    change_pct: float | None
    turnover: float | None = None


@dataclass(frozen=True)
class MarketSnapshot:
    trade_date: date
    total_count: int
    up_count: int
    down_count: int
    limit_up_count: int
    limit_down_count: int
    total_turnover: float
    average_change_pct: float
    sentiment: str
    indexes: list[IndexSnapshot] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Candidate:
    code: str
    name: str
    close: float
    change_pct: float
    turnover: float
    volume_ratio: float | None
    amplitude_pct: float | None
    hot_rank: int | None
    limit_up_days: int | None
    strategy_tags: list[str]
    score: int
    score_parts: dict[str, int]
    trigger: str
    invalidation: str
    reasons: list[str]
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Recap:
    market: MarketSnapshot
    candidates: list[Candidate]
    limit_platform_candidates: list[Candidate]
    industries: list[HotTopic]
    concepts: list[HotTopic]
    limit_leaders: list[Candidate]
    warnings: list[str]
