"""CVE repository discovery."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, List, Set, Tuple

from monitor.filters import filter_and_score, within_days
from monitor.github_client import GitHubClient
from monitor.models import Record
from monitor.scoring import Scorer


def run_cve(
    client: GitHubClient,
    scorer: Scorer,
    known_urls: Set[str],
    window_days: int = 2,
    min_final: float = 6.0,
    max_per_author: int = 3,
) -> Tuple[List[Record], dict]:
    year = datetime.now().year
    queries = [
        f"CVE-{year} in:name sort:updated",
        f"CVE-{year - 1} in:name pushed:>{datetime.now().year}-01-01",
    ]
    # Use search API properly
    candidates: List[Record] = []
    seen = set()
    print(f"[CVE] window={window_days}d year={year}")

    for q in (f"CVE-{year} in:name", f"CVE-{year} in:description"):
        print(f"  search: {q}")
        items = client.search_repos(q, sort="updated", per_page=50)
        for repo in items:
            url = repo.get("html_url") or ""
            if not url or url in seen:
                continue
            seen.add(url)
            name = repo.get("name") or ""
            full = repo.get("full_name") or name
            text = f"{full} {repo.get('description') or ''}"
            cves = re.findall(r"CVE-\d{4}-\d+", text, flags=re.I)
            if not cves:
                continue
            if not within_days(repo.get("pushed_at") or "", window_days):
                continue
            rec = Record.from_github_repo(repo, monitor_type="cve", monitor_keyword=cves[0].upper())
            rec.repo_name = full
            candidates.append(rec)

    kept, stats = filter_and_score(
        candidates,
        scorer,
        known_urls,
        min_final=min_final,
        max_per_author=max_per_author,
    )
    # CVE with a real ID is allowed even with thin description; only drop empty
    # zero-star shells that also lack any CVE id in name (already filtered above).
    final_kept = []
    for r in kept:
        desc = (r.repo_description or "").strip()
        has_cve = bool(re.search(r"CVE-\d{4}-\d+", f"{r.repo_name} {desc}", re.I))
        if r.stars == 0 and len(desc) < 8 and not has_cve:
            stats["low_score"] = stats.get("low_score", 0) + 1
            stats["kept"] = max(0, stats.get("kept", 1) - 1)
            continue
        final_kept.append(r)
    stats["source"] = "cve"
    print(f"[CVE] candidates={stats['candidates']} kept={len(final_kept)} dropped={stats['dropped']} low={stats['low_score']}")
    return final_kept, stats
