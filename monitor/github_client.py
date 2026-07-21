"""Unified GitHub API client with basic rate limiting and retries."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import requests


class GitHubClient:
    """Thin GitHub API wrapper with basic pacing.

    GitHub Search has a separate, much lower rate budget than core API
    (authenticated: ~30 req/min; anonymous: ~10 req/min). We therefore use a
    dedicated, slower pacing for search endpoints than for core GETs.
    """

    def __init__(
        self,
        token: str = "",
        timeout: int = 20,
        min_interval_ms: int = 350,
        search_min_interval_ms: int = 2200,
        max_retries: int = 3,
    ):
        self.token = token or ""
        self.timeout = timeout
        self.min_interval = max(min_interval_ms, 0) / 1000.0
        self.search_min_interval = max(search_min_interval_ms, 0) / 1000.0
        self.max_retries = max_retries
        self._last_request = 0.0
        self._last_search = 0.0
        self.session = requests.Session()
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "github-security-monitor-v5",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self.session.headers.update(headers)

    @property
    def has_token(self) -> bool:
        return bool(self.token)

    def _throttle(self, search: bool = False) -> None:
        interval = self.search_min_interval if search else self.min_interval
        if interval <= 0:
            return
        last = self._last_search if search else self._last_request
        delta = time.time() - last
        if delta < interval:
            time.sleep(interval - delta)

    def _rate_wait_seconds(self, resp: requests.Response, attempt: int) -> int:
        wait = 5 * (attempt + 1)
        retry_after = resp.headers.get("Retry-After")
        reset = resp.headers.get("X-RateLimit-Reset")
        if retry_after:
            try:
                wait = max(wait, int(retry_after))
            except ValueError:
                pass
        elif reset:
            try:
                wait = max(wait, min(int(reset) - int(time.time()), 65))
            except ValueError:
                pass
        return wait

    def request(
        self,
        method: str,
        url: str,
        search: bool = False,
        **kwargs,
    ) -> Optional[requests.Response]:
        kwargs.setdefault("timeout", self.timeout)
        for attempt in range(self.max_retries):
            self._throttle(search=search)
            try:
                resp = self.session.request(method, url, **kwargs)
                now = time.time()
                if search:
                    self._last_search = now
                else:
                    self._last_request = now
            except requests.RequestException as e:
                print(f"  [github] network error: {e}")
                time.sleep(1.5 * (attempt + 1))
                continue

            if resp.status_code == 200:
                return resp

            if resp.status_code in (403, 429):
                wait = self._rate_wait_seconds(resp, attempt)
                kind = "search" if search else "core"
                print(f"  [github] rate limited {kind} ({resp.status_code}), sleep {wait}s")
                time.sleep(wait)
                continue

            if resp.status_code >= 500:
                time.sleep(1.5 * (attempt + 1))
                continue

            print(f"  [github] HTTP {resp.status_code}: {resp.text[:200]}")
            return None
        return None

    def get_json(self, url: str, params: Optional[Dict] = None) -> Optional[Any]:
        resp = self.request("GET", url, params=params)
        if not resp:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    def search_repos(
        self,
        query: str,
        sort: str = "updated",
        order: str = "desc",
        per_page: int = 30,
        page: int = 1,
        return_meta: bool = False,
    ):
        url = "https://api.github.com/search/repositories"
        params = {
            "q": query,
            "sort": sort,
            "order": order,
            "per_page": per_page,
            "page": page,
        }
        resp = self.request("GET", url, params=params, search=True)
        if not resp:
            if return_meta:
                return {"items": [], "total_count": 0, "incomplete": False, "ok": False}
            return []
        try:
            data = resp.json()
        except ValueError:
            data = {}
        items = list(data.get("items") or []) if isinstance(data, dict) else []
        if return_meta:
            return {
                "items": items,
                "total_count": int(data.get("total_count") or 0),
                "incomplete": bool(data.get("incomplete_results")),
                "ok": resp.status_code == 200,
                "status": resp.status_code,
            }
        return items

    def get_repo(self, owner: str, repo: str) -> Optional[Dict[str, Any]]:
        url = f"https://api.github.com/repos/{owner}/{repo}"
        data = self.get_json(url)
        return data if isinstance(data, dict) else None

    def get_user_repos(self, user: str, per_page: int = 10, sort: str = "created") -> List[Dict[str, Any]]:
        url = f"https://api.github.com/users/{user}/repos"
        data = self.get_json(url, params={"sort": sort, "per_page": per_page, "type": "owner"})
        if not data or not isinstance(data, list):
            return []
        return data

    def get_latest_release(self, owner: str, repo: str) -> Optional[Dict[str, Any]]:
        url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        resp = self.request("GET", url)
        if not resp:
            return None
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError:
                return None
        return None
