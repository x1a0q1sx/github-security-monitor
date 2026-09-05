"""GitHubClient 测试 — mock 网络层，验证重试/退避/节流分离，不发真实请求。"""
from unittest import mock

import pytest

from monitor.github_client import GitHubClient


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
def fast_client():
    """零间隔客户端，测试不真等节流"""
    return GitHubClient(token="t", min_interval_ms=0, search_min_interval_ms=0, max_retries=3)


class TestRequest:
    def test_200_returns(self, fast_client):
        with mock.patch.object(fast_client.session, "request", return_value=_resp(200, {"ok": 1})):
            resp = fast_client.get_json("https://api.github.com/x")
        assert resp == {"ok": 1}

    def test_404_returns_none_without_retry(self, fast_client):
        with mock.patch.object(fast_client.session, "request", return_value=_resp(404)) as m:
            out = fast_client.get_json("https://api.github.com/x")
        assert out is None
        assert m.call_count == 1

    def test_5xx_retries_then_none(self, fast_client):
        with mock.patch.object(
            fast_client.session, "request",
            side_effect=[_resp(500), _resp(502), _resp(500)],
        ) as m, mock.patch("monitor.github_client.time.sleep"):
            out = fast_client.get_json("https://api.github.com/x")
        assert out is None
        assert m.call_count == 3

    def test_5xx_recovers_midway(self, fast_client):
        with mock.patch.object(
            fast_client.session, "request",
            side_effect=[_resp(500), _resp(200, {"ok": 1})],
        ), mock.patch("monitor.github_client.time.sleep"):
            out = fast_client.get_json("https://api.github.com/x")
        assert out == {"ok": 1}

    def test_429_waits_and_retries(self, fast_client):
        """429 必须按 Retry-After 等待后重试"""
        with mock.patch.object(
            fast_client.session, "request",
            side_effect=[
                _resp(429, headers={"Retry-After": "0"}),
                _resp(200, {"ok": 1}),
            ],
        ) as m, mock.patch("monitor.github_client.time.sleep") as ms:
            out = fast_client.get_json("https://api.github.com/x")
        assert out == {"ok": 1}
        assert m.call_count == 2
        assert ms.called  # 确实等待过

    def test_403_rate_limit_reads_reset(self, fast_client):
        reset = int(__import__("time").time()) + 1
        with mock.patch.object(
            fast_client.session, "request",
            side_effect=[
                _resp(403, headers={"X-RateLimit-Reset": str(reset)}),
                _resp(200, {"ok": 1}),
            ],
        ) as m, mock.patch("monitor.github_client.time.sleep"):
            out = fast_client.get_json("https://api.github.com/x")
        assert out == {"ok": 1}
        assert m.call_count == 2

    def test_network_error_retries(self, fast_client):
        import requests
        with mock.patch.object(
            fast_client.session, "request",
            side_effect=[requests.ConnectionError("boom"), _resp(200, {"ok": 1})],
        ), mock.patch("monitor.github_client.time.sleep"):
            out = fast_client.get_json("https://api.github.com/x")
        assert out == {"ok": 1}


class TestSearchPacing:
    def test_search_flag_passed(self, fast_client):
        """search_repos 应走 search=True 节流通道"""
        with mock.patch.object(
            fast_client, "request", return_value=_resp(200, {"items": [], "total_count": 0})
        ) as m:
            fast_client.search_repos("cobaltstrike", per_page=10)
        args, kwargs = m.call_args
        assert kwargs.get("search") is True

    def test_token_header(self):
        c = GitHubClient(token="abc", min_interval_ms=0, search_min_interval_ms=0)
        assert c.session.headers["Authorization"] == "Bearer abc"
        c2 = GitHubClient(token="", min_interval_ms=0, search_min_interval_ms=0)
        assert "Authorization" not in c2.session.headers
