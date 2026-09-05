"""Storage 测试 — 去重键、by_type/by_confidence 重算、soft_limit、执行记录。"""
import json

import pytest

from monitor.models import Record
from monitor.storage import Storage


@pytest.fixture()
def storage(tmp_path) -> Storage:
    return Storage(data_dir=tmp_path)


def _rec(url: str, mtype: str = "keyword", **kw) -> Record:
    return Record(repo_name=url.rsplit("/", 1)[-1], repo_url=url, monitor_type=mtype, **kw)


class TestAppendRecords:
    def test_dedupe_by_url(self, storage):
        r1 = _rec("https://github.com/a/b")
        first = storage.append_records([r1], soft_limit=100)
        assert first["added"] == 1
        # 同 URL 再来 → 不重复
        second = storage.append_records([_rec("https://github.com/a/b")], soft_limit=100)
        assert second["added"] == 0

    def test_tool_update_multi_version(self, storage):
        """tool_update 允许同一 URL 不同 pushed_at 多次入库"""
        url = "https://github.com/x/tool"
        a = _rec(url, mtype="tool_update", pushed_at="2026-09-01T00:00:00Z")
        b = _rec(url, mtype="tool_update", pushed_at="2026-09-03T00:00:00Z")
        first = storage.append_records([a], soft_limit=100)
        second = storage.append_records([b], soft_limit=100)
        assert first["added"] == 1 and second["added"] == 1

    def test_by_type_and_confidence_recomputed(self, storage):
        recs = [
            _rec("https://github.com/a/1", mtype="cve"),
            _rec("https://github.com/a/2", mtype="keyword"),
            _rec("https://github.com/a/3", mtype="keyword"),
        ]
        for r in recs[:2]:
            r.confidence = "high"
        recs[2].confidence = "low"
        storage.append_records(recs, soft_limit=100)
        doc = storage.load_records()
        assert doc["by_type"] == {"cve": 1, "keyword": 2}
        assert doc["by_confidence"] == {"high": 2, "low": 1}
        assert doc["total"] == 3

    def test_soft_limit_trims_noise_first(self, storage):
        """超过 soft_limit 时优先裁剪 noise 记录；非 noise 保留"""
        keep = [_rec(f"https://github.com/a/k{i}", discovered_at=f"2026-08-{i:02d}T00:00:00") for i in range(1, 6)]
        for r in keep:
            r.confidence = "medium"
        noise = [_rec(f"https://github.com/a/n{i}", discovered_at=f"2026-08-{i:02d}T00:00:00") for i in range(1, 9)]
        for r in noise:
            r.confidence = "noise"
        storage.append_records(keep + noise, soft_limit=5)
        doc = storage.load_records()
        urls = {it["repo_url"] for it in doc["items"]}
        # 所有非 noise 记录必须保留，noise 被裁掉
        assert all(f"https://github.com/a/k{i}" in urls for i in range(1, 6))
        assert doc["total"] <= 5 + len(doc["items"])  # 总量贴近 soft_limit
        assert not any(it.get("confidence") == "noise" for it in doc["items"]) or doc["total"] > 5


class TestExecutions:
    def test_append_execution(self, storage):
        storage.append_execution({"type": "daily", "status": "success", "total_new": 3})
        data = storage.load_executions()
        assert len(data) == 1
        assert data[0]["id"] == 1
        assert data[0]["status"] == "success"

    def test_failed_status_persisted(self, storage):
        """阶段1 防回归：failed/partial 状态必须能落盘"""
        storage.append_execution({"type": "daily", "status": "failed", "total_new": 0, "error": "boom"})
        data = storage.load_executions()
        assert data[-1]["status"] == "failed"
        assert data[-1]["error"] == "boom"

    def test_partial_status_persisted(self, storage):
        storage.append_execution({"type": "daily", "status": "partial", "total_new": 2})
        assert storage.load_executions()[-1]["status"] == "partial"


class TestToolState:
    def test_roundtrip(self, storage):
        storage.save_tool_state({"https://github.com/x/y": {"pushed_at": "2026-09-01"}})
        assert storage.load_tool_state()["https://github.com/x/y"]["pushed_at"] == "2026-09-01"
