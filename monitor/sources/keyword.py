"""Keyword discovery: S-tier single search + A/B contextual batches.

GitHub Search returns at most `per_page` hits *per query string*.
A batch like (A OR B OR C OR D) still yields only 30 items total — not 30
per term — so high-volume terms starve long-tail S keywords. V5 therefore:

- S (precise): one Search API call per keyword (optionally tiny 2-packs for
  ultra-rare terms), with dedicated search rate pacing.
- A/B: keep contextual batched queries (ambiguity needs qualifiers).
"""
from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

from monitor.filters import filter_and_score, within_days
from monitor.github_client import GitHubClient
from monitor.models import Record
from monitor.scoring import Scorer


def _quote_kw(kw: str) -> str:
    kw = (kw or "").strip()
    if not kw:
        return ""
    if " " in kw or any(c in kw for c in ":+-"):
        return f'"{kw}"'
    return kw


def build_search_plan(keywords_cfg: Dict[str, Any]) -> List[Dict[str, str]]:
    """Return ordered list of {tier, label, query} search jobs."""
    plan: List[Dict[str, str]] = []
    tiers = keywords_cfg.get("tiers") or {}
    search_cfg = keywords_cfg.get("search_queries") or {}

    # --- S: one query per keyword (sorted by score desc) ---
    s_items = list(tiers.get("S") or [])
    s_items.sort(key=lambda x: (-float(x.get("score") or 0), x.get("keyword") or ""))
    for it in s_items:
        kw = (it.get("keyword") or "").strip()
        if not kw:
            continue
        plan.append(
            {
                "tier": "S",
                "label": kw,
                "query": _quote_kw(kw),
            }
        )

    # --- A/B: contextual batches from config (preferred) ---
    for tier in ("A", "B"):
        for q in search_cfg.get(tier) or []:
            plan.append({"tier": tier, "label": q[:60], "query": q})

    # Fallback if no A/B batches configured: light per-keyword for top A only
    if not any(p["tier"] == "A" for p in plan):
        for it in (tiers.get("A") or [])[:12]:
            kw = (it.get("keyword") or "").strip()
            if not kw:
                continue
            # force security context for ambiguous A terms
            plan.append(
                {
                    "tier": "A",
                    "label": kw,
                    "query": f"{_quote_kw(kw)} (security OR exploit OR redteam OR malware OR c2 OR bypass)",
                }
            )

    return plan


def run_keyword(
    client: GitHubClient,
    scorer: Scorer,
    keywords_cfg: Dict[str, Any],
    known_urls: Set[str],
    window_days: int = 3,
    min_final: float = 5.5,
    max_per_author: int = 3,
    per_page: int = 30,
) -> Tuple[List[Record], dict]:
    plan = build_search_plan(keywords_cfg)
    if not plan:
        print("[KEYWORD] no search plan (empty keywords config)")
        return [], {"source": "keyword", "queries": 0, "candidates": 0, "kept": 0}

    candidates: List[Record] = []
    seen: Set[str] = set()
    query_stats: List[Dict[str, Any]] = []
    sum_total_count = 0
    fetched_raw = 0
    errors = 0

    print(
        f"[KEYWORD] plan={len(plan)} queries "
        f"(S-single={sum(1 for p in plan if p['tier']=='S')}, "
        f"A/B-batch={sum(1 for p in plan if p['tier']!='S')}) "
        f"window={window_days}d per_page={per_page} token={'yes' if client.has_token else 'NO'}"
    )

    for job in plan:
        tier, label, q = job["tier"], job["label"], job["query"]
        meta = client.search_repos(q, sort="updated", per_page=per_page, return_meta=True)
        if not meta.get("ok"):
            errors += 1
            print(f"  [{tier}] FAIL q={label!r}")
            query_stats.append(
                {
                    "tier": tier,
                    "label": label,
                    "total_count": 0,
                    "fetched": 0,
                    "windowed": 0,
                    "ok": False,
                }
            )
            continue

        total = int(meta.get("total_count") or 0)
        items = meta.get("items") or []

        # S-tier single-keyword queries can exceed one page (per_page hits cap);
        # fetch page 2 once when the first page is full and total_count is larger.
        if tier == "S" and len(items) == per_page and total > per_page:
            meta2 = client.search_repos(q, sort="updated", per_page=per_page, page=2, return_meta=True)
            if meta2.get("ok"):
                items.extend(meta2.get("items") or [])

        sum_total_count += total
        fetched_raw += len(items)
        windowed = 0
        for repo in items:
            url = repo.get("html_url") or ""
            if not url or url in seen:
                continue
            if not within_days(repo.get("pushed_at") or "", window_days):
                continue
            seen.add(url)
            windowed += 1
            rec = Record.from_github_repo(repo, monitor_type="keyword")
            # seed monitor_keyword with the search label for traceability
            if tier == "S":
                rec.monitor_keyword = label
            candidates.append(rec)

        incomplete = meta.get("incomplete")
        print(
            f"  [{tier}] total={total:>5} fetched={len(items):>2} "
            f"windowed_new={windowed:>2} incomplete={incomplete} q={label!r}"
        )
        query_stats.append(
            {
                "tier": tier,
                "label": label,
                "total_count": total,
                "fetched": len(items),
                "windowed": windowed,
                "incomplete": incomplete,
                "ok": True,
            }
        )

    kept, stats = filter_and_score(
        candidates,
        scorer,
        known_urls,
        min_final=min_final,
        max_per_author=max_per_author,
    )
    stats["source"] = "keyword"
    stats["queries"] = len(plan)
    stats["queries_ok"] = sum(1 for q in query_stats if q.get("ok"))
    stats["queries_err"] = errors
    stats["sum_total_count"] = sum_total_count
    stats["fetched_raw"] = fetched_raw
    stats["unique_windowed"] = len(candidates)
    stats["query_stats"] = query_stats
    print(
        f"[KEYWORD] unique_windowed={len(candidates)} kept={stats['kept']} "
        f"dup={stats['duplicates']} drop={stats['dropped']} low={stats['low_score']} "
        f"err={errors}"
    )
    return kept, stats
