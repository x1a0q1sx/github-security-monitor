"""通知去重 + 告警测试（阶段4 防回归）。"""
from unittest import mock

import pytest

import monitor.notify as notify
from monitor.notify import filter_new_notified, _fingerprint


@pytest.fixture()
def state_file(tmp_path, monkeypatch):
    p = tmp_path / "notification_state.json"
    monkeypatch.setattr(notify, "STATE_FILE", p)
    return p


def _item(url, mtype="keyword", day="2026-09-05", **kw):
    base = {
        "repo_name": url.rsplit("/", 1)[-1],
        "repo_url": url,
        "monitor_type": mtype,
        "discovered_at": f"{day} 10:00:00",
        "final_score": 8.0,
        "stars": 10,
        "confidence": "medium",
    }
    base.update(kw)
    return base


class TestFingerprint:
    def test_same_repo_same_day_same_fp(self):
        assert _fingerprint(_item("https://github.com/a/b")) == _fingerprint(_item("https://github.com/a/b"))

    def test_same_repo_diff_day_diff_fp(self):
        assert _fingerprint(_item("https://github.com/a/b", day="2026-09-05")) != _fingerprint(
            _item("https://github.com/a/b", day="2026-09-06")
        )


class TestFilterNewNotified:
    def test_first_pass_all_fresh(self, state_file):
        items = [_item(f"https://github.com/a/{i}") for i in range(3)]
        fresh = filter_new_notified(items)
        assert len(fresh) == 3
        assert state_file.exists()

    def test_second_pass_all_skipped(self, state_file):
        """关键防回归：Actions 重跑同窗口不重复通知"""
        items = [_item("https://github.com/a/b")]
        filter_new_notified(items)
        fresh = filter_new_notified(items)
        assert fresh == []

    def test_next_day_repo_notifies_again(self, state_file):
        """tool_update 类：同 repo 第二天有新动态应再通知"""
        filter_new_notified([_item("https://github.com/a/b", day="2026-09-05")])
        fresh = filter_new_notified([_item("https://github.com/a/b", day="2026-09-06")])
        assert len(fresh) == 1

    def test_mixed_pass(self, state_file):
        filter_new_notified([_item("https://github.com/a/old")])
        mixed = [_item("https://github.com/a/old"), _item("https://github.com/a/new")]
        fresh = filter_new_notified(mixed)
        assert [i["repo_name"] for i in fresh] == ["new"]


class TestNotifyNewRecordsDedup:
    def test_no_double_send(self, state_file, tmp_path):
        cfg = {"notify": {"dingding": {"enable": True, "webhook": "http://x", "secret": ""}}}
        items = [_item("https://github.com/a/b")]
        with mock.patch.object(notify, "send_dingding", return_value=True) as m:
            notify.notify_new_records(cfg, items, prefix="Daily")
            notify.notify_new_records(cfg, items, prefix="Daily")
        assert m.call_count == 1  # 第二次被去重


class TestAlerts:
    def test_alert_from_results_on_failure(self, state_file):
        cfg = {"notify": {"dingding": {"enable": True, "webhook": "http://x", "secret": ""}}}
        results = {"cve": {"kept": -1, "error": "boom"}}
        client = mock.Mock()
        client.metrics.return_value = {"circuit_open": 0, "rate_limited": 0, "budget_exhausted": 0}
        with mock.patch.object(notify, "send_dingding", return_value=True) as m:
            notify.alert_from_results(cfg, client, results)
        assert m.call_count == 1
        body = m.call_args[0][3]
        assert "cve" in body and "boom" in body

    def test_no_alert_when_healthy(self, state_file):
        cfg = {"notify": {"dingding": {"enable": True, "webhook": "http://x", "secret": ""}}}
        client = mock.Mock()
        client.metrics.return_value = {"circuit_open": 0, "rate_limited": 0, "budget_exhausted": 0}
        with mock.patch.object(notify, "send_dingding", return_value=True) as m:
            notify.alert_from_results(cfg, client, {"cve": {"kept": 3}})
        assert m.call_count == 0

    def test_alert_on_circuit_open(self, state_file):
        cfg = {"notify": {"dingding": {"enable": True, "webhook": "http://x", "secret": ""}}}
        client = mock.Mock()
        client.metrics.return_value = {"circuit_open": 4, "rate_limited": 0, "budget_exhausted": 0}
        with mock.patch.object(notify, "send_dingding", return_value=True) as m:
            notify.alert_from_results(cfg, client, {})
        assert m.call_count == 1
        assert "熔断" in m.call_args[0][3]

    def test_alerts_disable_switch(self, state_file):
        cfg = {"notify": {"alerts": {"enable": False}, "dingding": {"enable": True, "webhook": "http://x"}}}
        with mock.patch.object(notify, "send_dingding", return_value=True) as m:
            notify.notify_alert(cfg, "test subject", "detail")
        assert m.call_count == 0
