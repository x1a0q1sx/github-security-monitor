"""Watchlisted user repository monitoring."""
from __future__ import annotations

from typing import List, Sequence, Set, Tuple

from monitor.filters import filter_and_score, within_days
from monitor.github_client import GitHubClient
from monitor.models import Record
from monitor.scoring import Scorer


def run_user(
    client: GitHubClient,
    scorer: Scorer,
    users: Sequence[str],
    known_urls: Set[str],
    window_days: int = 7,
    min_final: float = 5.0,
    max_per_author: int = 5,
) -> Tuple[List[Record], dict]:
    candidates: List[Record] = []
    print(f"[USER] users={len(users)} window={window_days}d")

    for user in users:
        print(f"  user: {user}")
        repos = client.get_user_repos(user, per_page=15, sort="created")
        for repo in repos:
            if repo.get("fork"):
                continue
            # new repos in window OR recently pushed non-fork with description
            created_ok = within_days(repo.get("created_at") or "", window_days)
            pushed_ok = within_days(repo.get("pushed_at") or "", 2)
            if not (created_ok or pushed_ok):
                continue
            # for old repos with only recent push, require they are relatively new (<90d) to avoid noise
            if not created_ok and pushed_ok:
                if not within_days(repo.get("created_at") or "", 90):
                    continue
            rec = Record.from_github_repo(repo, monitor_type="user_repo", monitor_keyword=f"用户:{user}")
            candidates.append(rec)

    kept, stats = filter_and_score(
        candidates,
        scorer,
        known_urls,
        min_final=min_final,
        max_per_author=max_per_author,
    )
    stats["source"] = "user"
    print(f"[USER] candidates={stats['candidates']} kept={stats['kept']}")
    return kept, stats
