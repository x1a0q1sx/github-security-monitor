"""Data models for monitor records and skills."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Record:
    repo_name: str
    repo_url: str
    repo_description: str = ""
    monitor_type: str = "keyword"
    monitor_keyword: str = ""
    tags: str = ""
    author: str = ""
    stars: int = 0
    language: str = ""
    created_at: str = ""
    pushed_at: str = ""
    description_cn: str = ""
    discovered_at: str = field(default_factory=now_str)
    # V5 fields
    final_score: float = 0.0
    relevance_score: float = 0.0
    quality_score: float = 0.0
    confidence: str = "low"  # high | medium | low | noise
    reasons: List[str] = field(default_factory=list)
    matched_keywords: List[str] = field(default_factory=list)
    is_fork: bool = False
    topics: List[str] = field(default_factory=list)
    archived: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Record":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore
        payload = {k: v for k, v in data.items() if k in known}
        payload.setdefault("reasons", [])
        payload.setdefault("matched_keywords", [])
        payload.setdefault("topics", [])
        return cls(**payload)

    @classmethod
    def from_github_repo(
        cls,
        repo: Dict[str, Any],
        monitor_type: str,
        monitor_keyword: str = "",
    ) -> "Record":
        owner = ""
        if isinstance(repo.get("owner"), dict):
            owner = repo["owner"].get("login", "")
        html = repo.get("html_url") or ""
        if not owner and html:
            parts = html.rstrip("/").split("/")
            if len(parts) >= 2:
                owner = parts[-2]
        full_name = repo.get("full_name") or repo.get("name") or ""
        return cls(
            repo_name=full_name,
            repo_url=html,
            repo_description=repo.get("description") or "",
            monitor_type=monitor_type,
            monitor_keyword=monitor_keyword,
            author=owner,
            stars=int(repo.get("stargazers_count") or 0),
            language=repo.get("language") or "",
            created_at=repo.get("created_at") or "",
            pushed_at=repo.get("pushed_at") or repo.get("updated_at") or "",
            is_fork=bool(repo.get("fork")),
            topics=list(repo.get("topics") or []),
        )


@dataclass
class SkillCard:
    id: str
    name: str
    display_name: str = ""
    description: str = ""
    source: str = "unknown"
    repo_url: str = ""
    homepage: str = ""
    install: str = ""
    category: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    scores: Dict[str, float] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    security_relevant: float = 0.0
    discovered_at: str = field(default_factory=now_str)
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillCard":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore
        payload = {k: v for k, v in data.items() if k in known}
        payload.setdefault("category", [])
        payload.setdefault("tags", [])
        payload.setdefault("stats", {})
        payload.setdefault("scores", {})
        payload.setdefault("reasons", [])
        return cls(**payload)


def empty_records_doc() -> Dict[str, Any]:
    return {
        "version": 5,
        "items": [],
        "total": 0,
        "last_updated": "",
        "by_type": {},
        "by_confidence": {},
        "meta": {"min_final_score": 6.0, "scored": True},
    }


def empty_skills_doc() -> Dict[str, Any]:
    return {
        "version": 5,
        "items": [],
        "total": 0,
        "last_updated": "",
        "by_source": {},
        "by_category": {},
    }


def empty_trending_doc() -> Dict[str, Any]:
    return {"items": [], "updated_at": "", "total": 0}
