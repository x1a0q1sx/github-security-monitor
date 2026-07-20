"""Candidate filtering helpers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Set, Tuple
import re

from monitor.models import Record
from monitor.scoring import Scorer, ScoreResult


def parse_date(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        m = re.search(r"\d{4}-\d{2}-\d{2}", value)
        if not m:
            return None
        try:
            return datetime.fromisoformat(m.group(0)).replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def within_days(ts: str, days: int, field_label: str = "pushed") -> bool:
    dt = parse_date(ts)
    if not dt:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - dt <= timedelta(days=days)


def filter_and_score(
    candidates: Iterable[Record],
    scorer: Scorer,
    known_urls: Set[str],
    min_final: float,
    max_per_author: int = 3,
) -> Tuple[List[Record], dict]:
    kept: List[Record] = []
    stats = {
        "candidates": 0,
        "duplicates": 0,
        "dropped": 0,
        "low_score": 0,
        "kept": 0,
        "drop_reasons": {},
    }
    author_count: dict = {}

    for rec in candidates:
        stats["candidates"] += 1
        if not rec.repo_url:
            stats["dropped"] += 1
            continue
        # tool_update intentionally reuses repo_url across versions
        if rec.monitor_type != "tool_update" and rec.repo_url in known_urls:
            stats["duplicates"] += 1
            continue

        result: ScoreResult = scorer.apply(rec)
        if result.drop:
            stats["dropped"] += 1
            stats["drop_reasons"][result.drop_reason] = stats["drop_reasons"].get(result.drop_reason, 0) + 1
            continue

        author = rec.author or "unknown"
        author_count[author] = author_count.get(author, 0) + 1
        if author_count[author] > max_per_author:
            stats["dropped"] += 1
            stats["drop_reasons"]["author_cap"] = stats["drop_reasons"].get("author_cap", 0) + 1
            continue

        if rec.final_score < min_final:
            stats["low_score"] += 1
            continue

        kept.append(rec)
        if rec.monitor_type != "tool_update":
            known_urls.add(rec.repo_url)
        stats["kept"] += 1

    # sort by score desc
    kept.sort(key=lambda r: r.final_score, reverse=True)
    return kept, stats
