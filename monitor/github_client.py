"""Unified GitHub API client with basic rate limiting and retries."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import requests


class GitHubClient:
    def __init__(
        self,
        token: str = "",
        timeout: int = 20,
        min_interval_ms: int = 350,
        max_retries: int = 3,
    ):
        self.token = token or ""
        self.timeout = timeout
        self.min_interval = max(min_interval_ms, 0) / 1000.0
        self.max_retries = max_retries
        self._last_request = 0.0
        self.session = requests.Session()
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "github-security-monitor-v5",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self.session.headers.update(headers)

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        delta = time.time() - self._last_request
        if delta < self.min_interval:
            time.sleep(self.min_interval - delta)

    def request(self, method: str, url: str, **kwargs) -> Optional[requests.Response]:
        kwargs.setdefault("timeout", self.timeout)
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                resp = self.session.request(method, url, **kwargs)
                self._last_request = time.time()
            except requests.RequestException as e:
                print(f"  [github] network error: {e}")
                time.sleep(1.5 * (attempt + 1))
                continue

            if resp.status_code == 200:
                return resp

            if resp.status_code in (403, 429):
                # rate limited
                reset = resp.headers.get("X-RateLimit-Reset")
                retry_after = resp.headers.get("Retry-After")
                wait = 5 * (attempt + 1)
                if retry_after:
                    try:
                        wait = max(wait, int(retry_after))
                    except ValueError:
                        pass
                elif reset:
                    try:
                        wait = max(wait, min(int(reset) - int(time.time()), 60))
                    except ValueError:
                        pass
                print(f"  [github] rate limited ({resp.status_code}), sleep {wait}s")
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
    ) -> List[Dict[str, Any]]:
        q = query
        url = "https://api.github.com/search/repositories"
        params = {
            "q": q,
            "sort": sort,
            "order": order,
            "per_page": per_page,
            "page": page,
        }
        data = self.get_json(url, params=params)
        if not data or not isinstance(data, dict):
            return []
        return list(data.get("items") or [])

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
