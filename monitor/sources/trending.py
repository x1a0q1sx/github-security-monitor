"""Security trending / high-star catalog (weekly)."""
from __future__ import annotations

from typing import Any, Dict, List, Sequence

from monitor.github_client import GitHubClient
from monitor.models import now_iso


DEFAULT_QUERIES = [
    "topic:security stars:>50",
    "redteam stars:>50",
    "penetration-testing stars:>50",
    "malware-analysis stars:>30",
    "reverse-engineering stars:>30",
]


def run_trending(
    client: GitHubClient,
    queries: Sequence[str] | None = None,
    min_stars: int = 50,
    top_n: int = 30,
) -> Dict[str, Any]:
    queries = list(queries or DEFAULT_QUERIES)
    all_items: List[Dict[str, Any]] = []
    seen = set()
    print(f"[TRENDING] queries={len(queries)}")

    for query in queries:
        print(f"  query: {query}")
        repos = client.search_repos(query, sort="stars", order="desc", per_page=10)
        for repo in repos:
            url = repo.get("html_url") or ""
            if not url or url in seen:
                continue
            stars = int(repo.get("stargazers_count") or 0)
            if stars < min_stars:
                continue
            seen.add(url)
            all_items.append(
                {
                    "repo_name": repo.get("full_name") or repo.get("name"),
                    "repo_url": url,
                    "repo_description": repo.get("description") or "",
                    "stars": stars,
                    "forks": repo.get("forks_count") or 0,
                    "language": repo.get("language") or "",
                    "topics": repo.get("topics") or [],
                    "created_at": repo.get("created_at") or "",
                    "updated_at": repo.get("updated_at") or "",
                    "query": query,
                }
            )

    all_items.sort(key=lambda x: x["stars"], reverse=True)
    top = all_items[:top_n]
    doc = {"items": top, "updated_at": now_iso(), "total": len(top)}
    print(f"[TRENDING] collected={len(all_items)} top={len(top)}")
    return doc
