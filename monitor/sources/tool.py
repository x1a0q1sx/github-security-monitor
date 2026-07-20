"""Tool update monitoring with persistent last-seen state."""
from __future__ import annotations

from typing import Any, Dict, List, Sequence, Set, Tuple

from monitor.github_client import GitHubClient
from monitor.models import Record
from monitor.scoring import Scorer


def run_tool(
    client: GitHubClient,
    scorer: Scorer,
    tools: Sequence[Dict[str, str]],
    known_urls: Set[str],
    tool_state: Dict[str, Any],
    min_final: float = 5.0,
) -> Tuple[List[Record], Dict[str, Any], dict]:
    """Return (records, updated_tool_state, stats).

    Emits a record only when pushed_at or latest release tag changes.
    First observation initializes state without flooding (unless never seen in records).
    """
    state = tool_state if tool_state is not None else {"tools": {}}
    state.setdefault("tools", {})
    found: List[Record] = []
    stats = {"checked": 0, "updated": 0, "initialized": 0, "errors": 0}

    print(f"[TOOL] tracking {len(tools)} tools")
    for t in tools:
        owner, repo = t.get("owner"), t.get("repo")
        if not owner or not repo:
            continue
        key = f"{owner}/{repo}"
        print(f"  check: {key}")
        stats["checked"] += 1
        data = client.get_repo(owner, repo)
        if not data:
            stats["errors"] += 1
            continue

        pushed = data.get("pushed_at") or ""
        html = data.get("html_url") or f"https://github.com/{key}"
        release = client.get_latest_release(owner, repo) or {}
        tag = release.get("tag_name") or ""

        prev = state["tools"].get(key) or {}
        prev_pushed = prev.get("pushed_at") or ""
        prev_tag = prev.get("release_tag") or ""

        changed = False
        reason = []
        if not prev:
            # initialize
            state["tools"][key] = {
                "pushed_at": pushed,
                "release_tag": tag,
                "stars": data.get("stargazers_count") or 0,
            }
            stats["initialized"] += 1
            # only emit if not already in historical records
            if html not in known_urls:
                changed = True
                reason.append("first_seen")
        else:
            if pushed and pushed != prev_pushed:
                changed = True
                reason.append(f"pushed:{prev_pushed}->{pushed}")
            if tag and tag != prev_tag:
                changed = True
                reason.append(f"release:{prev_tag}->{tag}")
            state["tools"][key] = {
                "pushed_at": pushed or prev_pushed,
                "release_tag": tag or prev_tag,
                "stars": data.get("stargazers_count") or 0,
            }

        if not changed:
            continue

        rec = Record.from_github_repo(
            data,
            monitor_type="tool_update" if prev else "tool",
            monitor_keyword=",".join(reason) or "红队工具",
        )
        result = scorer.apply(rec, forced_relevance=7.0)
        if result.drop or rec.final_score < min_final:
            continue
        if rec.monitor_type == "tool" and rec.repo_url in known_urls:
            continue
        # tool_update is versioned by pushed_at/tag; always emit when changed
        if rec.monitor_type == "tool_update":
            rec.reasons = list(rec.reasons) + [f"release:{tag or '-'}"]
        found.append(rec)
        stats["updated"] += 1
        if rec.monitor_type == "tool":
            known_urls.add(rec.repo_url)

    stats["source"] = "tool"
    print(f"[TOOL] updated={stats['updated']} initialized={stats['initialized']}")
    return found, state, stats
