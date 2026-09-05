"""GitHubClient 预算/熔断/指标 测试（阶段3 防回归）。"""
from unittest import mock

import pytest

from monitor.github_client import CircuitOpenError, GitHubClient


def _resp(status=200, json_data=None, headers=None):
    resp = mock.Mock()
    resp.status_code = status
    resp.headers = headers or {}
    resp.text = "{}"
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError()
    return resp


@pytest.fixture()
def client():
    return GitHubClient(token="t", min_interval_ms=0, search_min_interval_ms=0, max_retries=3)


class TestBudget:
    def test_search_budget_blocks_after_limit(self):
        c = GitHubClient(min_interval_ms=0, search_min_interval_ms=0, search_budget=2)
        with mock.patch.object(c.session, "request", return_value=_resp(200, {"items": []})):
            assert c.search_repos("q1", return_meta=True)["ok"]
            assert c.search_repos("q2", return_meta=True)["ok"]
            with pytest.raises(CircuitOpenError):
                c.search_repos("q3", return_meta=True)
        m = c.metrics()
        assert m["requests"] == 2 and m["budget_exhausted"] == 1

    def test_zero_budget_unlimited(self, client):
        with mock.patch.object(client.session, "request", return_value=_resp(200, {"items": []})):
            for _ in range(5):
                client.search_repos("q", return_meta=True)
        assert client.metrics()["requests"] == 5

    def test_core_budget_independent(self):
        c = GitHubClient(min_interval_ms=0, search_min_interval_ms=0, core_budget=1)
        with mock.patch.object(c.session, "request", return_value=_resp(200, {"items": []})):
            c.search_repos("a")  # search 不受 core 预算限制
            c.get_repo("o", "r")  # 第 1 次 core
            with pytest.raises(CircuitOpenError):
                c.get_repo("o", "r2")  # 第 2 次超 core 预算


class TestCircuitBreaker:
    def test_opens_after_consecutive_rate_limits(self):
        c = GitHubClient(min_interval_ms=0, search_min_interval_ms=0, max_retries=2)
        with mock.patch.object(c.session, "request", return_value=_resp(429, headers={"Retry-After": "0"})), \
             mock.patch("monitor.github_client.time.sleep"):
            # 每次 get_json 内部消耗 2 个重试 → consec=4 ≥ 3，第二次调用内已熔断
            c.get_json("https://api.github.com/x")   # 返回 None，consec=2
            c.get_json("https://api.github.com/x")   # 内部置 _open=True
            # 熔断后直接拒绝，不再发请求
            with pytest.raises(CircuitOpenError):
                c.get_json("https://api.github.com/x")
        assert c.metrics()["circuit_open"] >= 1

    def test_opens_after_network_errors(self):
        import requests as rq
        c = GitHubClient(min_interval_ms=0, search_min_interval_ms=0, max_retries=5)
        with mock.patch.object(c.session, "request", side_effect=rq.ConnectionError("x")), \
             mock.patch("monitor.github_client.time.sleep"):
            # 一次 get_json 内部 5 个重试全部网络错误 → consec=5 → _open=True
            c.get_json("https://api.github.com/x")
            with pytest.raises(CircuitOpenError):
                c.get_json("https://api.github.com/x")
        assert c._open is True

    def test_success_resets_rate_counter(self):
        c = GitHubClient(min_interval_ms=0, search_min_interval_ms=0, max_retries=2)
        with mock.patch.object(
            c.session, "request",
            side_effect=[_resp(429, headers={"Retry-After": "0"}), _resp(200, {"items": []})],
        ), mock.patch("monitor.github_client.time.sleep"):
            c.get_json("https://api.github.com/x")
        assert c._consec_rate_limited == 0

    def test_reset_run_state(self):
        c = GitHubClient(min_interval_ms=0, search_min_interval_ms=0, search_budget=1)
        with mock.patch.object(c.session, "request", return_value=_resp(200, {"items": []})):
            c.search_repos("a")
        c.reset_run_state()
        assert c._open is False
        assert c.metrics()["requests"] == 0
        with mock.patch.object(c.session, "request", return_value=_resp(200, {"items": []})):
            c.search_repos("b")  # 预算恢复可用
