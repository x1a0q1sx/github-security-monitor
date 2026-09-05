"""filters + run.py 状态判定测试。"""
from datetime import datetime, timedelta, timezone

import pytest

from monitor.filters import parse_date, within_days
from monitor.run import _record_status


class TestFilters:
    def test_parse_z_suffix(self):
        dt = parse_date("2026-09-01T12:00:00Z")
        assert dt is not None and dt.year == 2026

    def test_within_days_true(self):
        ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        assert within_days(ts, 3)

    def test_within_days_false(self):
        ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        assert not within_days(ts, 3)

    def test_invalid(self):
        assert not within_days("garbage", 3)
        assert not within_days("", 3)


class TestRecordStatus:
    """阶段1 防回归：status 不再恒为 success"""

    def test_success(self):
        assert _record_status({"cve": {"kept": 3}}) == "success"

    def test_zero_kept_is_success(self):
        assert _record_status({"cve": {"kept": 0}}) == "success"

    def test_partial(self):
        assert _record_status({"cve": {"kept": 3}, "keyword": {"kept": -1}}) == "partial"

    def test_failed(self):
        assert _record_status({"cve": {"kept": -1}, "keyword": {"kept": -1}}) == "failed"

    def test_error_key_only_counts_when_kept_negative(self):
        assert _record_status({"cve": {"kept": 1, "error": "x"}}) == "success"
