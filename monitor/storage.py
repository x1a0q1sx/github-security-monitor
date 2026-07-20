"""JSON storage for records, executions, trending, skills, tool state."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from monitor.config import ARCHIVE_DIR, DATA_DIR, DOCS_DATA_DIR, read_json, write_json
from monitor.models import (
    Record,
    empty_records_doc,
    empty_skills_doc,
    empty_trending_doc,
    now_iso,
)

RECORDS_FILE = DATA_DIR / "records.json"
EXECUTIONS_FILE = DATA_DIR / "executions.json"
TRENDING_FILE = DATA_DIR / "trending.json"
SKILLS_FILE = DATA_DIR / "skills.json"
TOOL_STATE_FILE = DATA_DIR / "tool_state.json"


class Storage:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.data_dir = data_dir
        self.records_file = data_dir / "records.json"
        self.executions_file = data_dir / "executions.json"
        self.trending_file = data_dir / "trending.json"
        self.skills_file = data_dir / "skills.json"
        self.tool_state_file = data_dir / "tool_state.json"

    # ---- records ----
    def load_records(self) -> Dict[str, Any]:
        data = read_json(self.records_file, empty_records_doc())
        if "items" not in data:
            data = empty_records_doc()
        data.setdefault("by_type", {})
        data.setdefault("by_confidence", {})
        data.setdefault("version", 5)
        return data

    def save_records(self, data: Dict[str, Any]) -> None:
        items = data.get("items", [])
        data["total"] = len(items)
        data["last_updated"] = now_iso()
        by_type: Dict[str, int] = {}
        by_conf: Dict[str, int] = {}
        for it in items:
            t = it.get("monitor_type") or "unknown"
            by_type[t] = by_type.get(t, 0) + 1
            c = it.get("confidence") or "unset"
            by_conf[c] = by_conf.get(c, 0) + 1
        data["by_type"] = by_type
        data["by_confidence"] = by_conf
        write_json(self.records_file, data)

    @staticmethod
    def record_key(item: Dict[str, Any] | Record) -> str:
        """Dedupe key. tool_update may appear multiple times across versions."""
        if isinstance(item, Record):
            url = item.repo_url or ""
            mtype = item.monitor_type or ""
            pushed = item.pushed_at or ""
            mk = item.monitor_keyword or ""
        else:
            url = item.get("repo_url") or ""
            mtype = item.get("monitor_type") or ""
            pushed = item.get("pushed_at") or ""
            mk = item.get("monitor_keyword") or ""
        if mtype == "tool_update":
            return f"{mtype}|{url}|{pushed}|{mk}"
        return url

    def known_urls(self, records: Optional[Dict[str, Any]] = None) -> Set[str]:
        rec = records if records is not None else self.load_records()
        return {it.get("repo_url", "") for it in rec.get("items", []) if it.get("repo_url")}

    def known_keys(self, records: Optional[Dict[str, Any]] = None) -> Set[str]:
        rec = records if records is not None else self.load_records()
        return {self.record_key(it) for it in rec.get("items", []) if it.get("repo_url")}

    def append_records(self, new_items: List[Record], soft_limit: int = 8000) -> Dict[str, Any]:
        data = self.load_records()
        existing = self.known_keys(data)
        # also expose bare urls for sources that only care about first-seen repos
        existing_urls = self.known_urls(data)
        added = []
        for r in new_items:
            if not r.repo_url:
                continue
            key = self.record_key(r)
            if key in existing:
                continue
            # non-update types: still unique by url
            if r.monitor_type != "tool_update" and r.repo_url in existing_urls:
                continue
            data["items"].append(r.to_dict())
            existing.add(key)
            existing_urls.add(r.repo_url)
            added.append(r)
        # soft trim oldest low-confidence first if over limit
        if soft_limit and len(data["items"]) > soft_limit:
            items = data["items"]
            noise = [i for i in items if i.get("confidence") == "noise"]
            keep = [i for i in items if i.get("confidence") != "noise"]
            overflow = len(items) - soft_limit
            if overflow > 0 and noise:
                drop = noise[:overflow]
                drop_set = {id(x) for x in drop}
                data["items"] = [i for i in items if id(i) not in drop_set]
        self.save_records(data)
        return {"added": len(added), "items": added, "doc": data}

    # ---- executions ----
    def load_executions(self) -> List[Dict[str, Any]]:
        return read_json(self.executions_file, [])

    def save_executions(self, data: List[Dict[str, Any]]) -> None:
        # keep last 200
        write_json(self.executions_file, data[-200:])

    def append_execution(self, execution: Dict[str, Any]) -> None:
        data = self.load_executions()
        execution.setdefault("id", len(data) + 1)
        data.append(execution)
        self.save_executions(data)

    # ---- trending ----
    def load_trending(self) -> Dict[str, Any]:
        return read_json(self.trending_file, empty_trending_doc())

    def save_trending(self, data: Dict[str, Any]) -> None:
        write_json(self.trending_file, data)

    # ---- skills ----
    def load_skills(self) -> Dict[str, Any]:
        data = read_json(self.skills_file, empty_skills_doc())
        data.setdefault("items", [])
        return data

    def save_skills(self, data: Dict[str, Any]) -> None:
        items = data.get("items", [])
        data["total"] = len(items)
        data["last_updated"] = now_iso()
        by_source: Dict[str, int] = {}
        by_cat: Dict[str, int] = {}
        for it in items:
            s = it.get("source") or "unknown"
            by_source[s] = by_source.get(s, 0) + 1
            for c in it.get("category") or ["uncategorized"]:
                by_cat[c] = by_cat.get(c, 0) + 1
        data["by_source"] = by_source
        data["by_category"] = by_cat
        write_json(self.skills_file, data)

    # ---- tool state ----
    def load_tool_state(self) -> Dict[str, Any]:
        return read_json(self.tool_state_file, {"tools": {}})

    def save_tool_state(self, data: Dict[str, Any]) -> None:
        write_json(self.tool_state_file, data)

    # ---- publish to docs ----
    def publish_to_docs(self) -> None:
        DOCS_DATA_DIR.mkdir(parents=True, exist_ok=True)
        for name in ("records.json", "executions.json", "trending.json", "skills.json"):
            src = self.data_dir / name
            if src.exists():
                dest = DOCS_DATA_DIR / name
                dest.write_bytes(src.read_bytes())

    # ---- archive ----
    def archive_file(self, src: Path, label: str) -> Path:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        dest = ARCHIVE_DIR / f"{label}-{src.name}"
        if src.exists():
            dest.write_bytes(src.read_bytes())
        return dest
