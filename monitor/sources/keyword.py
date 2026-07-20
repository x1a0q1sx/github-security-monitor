"""Layered keyword discovery with batched GitHub searches."""
from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

from monitor.filters import filter_and_score, within_days
from monitor.github_client import GitHubClient
from monitor.models import Record
from monitor.scoring import Scorer


def run_keyword(
    client: GitHubClient,
    scorer: Scorer,
    keywords_cfg: Dict[str, Any],
    known_urls: Set[str],
    window_days: int = 3,
    min_final: float = 6.0,
    max_per_author: int = 3,
    per_page: int = 30,
) -> Tuple[List[Record], dict]:
    search_cfg = keywords_cfg.get("search_queries") or {}
    queries: List[str] = []
    for tier in ("S", "A", "B"):
        for q in search_cfg.get(tier) or []:
            queries.append(q)

    # fallback if config empty: build from S keywords
    if not queries:
        s_terms = [i["keyword"] for i in (keywords_cfg.get("tiers") or {}).get("S") or []][:20]
        for i in range(0, len(s_terms), 4):
            chunk = s_terms[i : i + 4]
            queries.append(" OR ".join(f'"{t}"' if " " in t else t for t in chunk))

    candidates: List[Record] = []
    seen = set()
    print(f"[KEYWORD] queries={len(queries)} window={window_days}d")

    for q in queries:
        # bias to recently updated
        full_q = f"({q})"
        print(f"  search: {full_q[:100]}")
        items = client.search_repos(full_q, sort="updated", per_page=per_page)
        for repo in items:
            url = repo.get("html_url") or ""
            if not url or url in seen:
                continue
            seen.add(url)
            if not within_days(repo.get("pushed_at") or "", window_days):
                continue
            rec = Record.from_github_repo(repo, monitor_type="keyword")
            candidates.append(rec)

    kept, stats = filter_and_score(
        candidates,
        scorer,
        known_urls,
        min_final=min_final,
        max_per_author=max_per_author,
    )
    stats["source"] = "keyword"
    stats["queries"] = len(queries)
    print(
        f"[KEYWORD] candidates={stats['candidates']} kept={stats['kept']} "
        f"dup={stats['duplicates']} drop={stats['dropped']} low={stats['low_score']}"
    )
    return kept, stats
