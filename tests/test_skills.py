"""Skills 源测试 — 分类词边界/互斥/上限、security 分、freshness、同名合并。"""
from unittest import mock

import pytest

from monitor.models import SkillCard
from monitor.sources.skills import (
    _categorize,
    _display_key,
    _freshness_score,
    _match_term,
    _security_score,
    run_skills,
    score_skill,
)

CAT_KW = {
    "security": [security := "security", "pentest", "cve", "vulnerability"],
    "design": ["ui", "ux", "design", "css"],
    "testing": ["unit test", "e2e", "playwright", "pytest"],
    "git": ["git", "pull request", "commit"],
}


class TestMatchTerm:
    def test_word_boundary(self):
        assert _match_term("css", "this css file")
        assert not _match_term("css", "success backup")
        assert not _match_term("pr", "pattern lock")
        assert not _match_term("test", "pentest commands")

    def test_cjk_substring(self):
        assert _match_term("安全", "网络安全工具")
        assert not _match_term("渗透", "网络扫描")

    def test_multiword(self):
        assert _match_term("pull request", "auto pull request review")
        assert _match_term("unit test", "runs unit tests daily")


class TestSecurityScore:
    def test_word_boundary_thread_vs_threat(self):
        assert _security_score("thread pool", ["threat"]) == 0.0

    def test_saturation(self):
        one = _security_score("security tool", ["security"])
        two = _security_score("security pentest", ["security", "pentest"])
        six = _security_score(
            "security pentest cve vulnerability malware exploit",
            ["security", "pentest", "cve", "vulnerability", "malware", "exploit"],
        )
        assert one == pytest.approx(0.25)
        assert two == pytest.approx(0.40)  # 门槛语义保留
        assert six == 1.0


class TestCategorize:
    def test_security_mutex(self):
        """pentest 文本不再因 'testing' 字样进入 testing 分类"""
        cats = _categorize(
            "Pentest Commands essential penetration testing reference",
            CAT_KW,
        )
        assert cats == ["security"]

    def test_weak_cat_needs_two_hits_with_security(self):
        cats = _categorize("security review of the design", CAT_KW)
        # design 仅 1 命中 → 被 security 压制
        assert cats == ["security"]

    def test_weak_cat_two_hits_attached(self):
        cats = _categorize("security review of ui ux design system", CAT_KW)
        assert cats[0] == "security" and "design" in cats

    def test_no_security_sorted_by_hits(self):
        cats = _categorize("git commit and pull request helper", CAT_KW)
        assert cats[0] == "git"
        assert "security" not in cats

    def test_max_three(self):
        text = "security pentest cve vulnerability ui ux design css git commit pull request unit test e2e"
        cats = _categorize(text, CAT_KW, max_cats=3)
        assert len(cats) <= 3

    def test_general_fallback(self):
        assert _categorize("random cooking recipe", CAT_KW) == ["general"]


class TestFreshness:
    def test_decay(self):
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        fresh = _freshness_score((now - datetime.timedelta(days=7)).isoformat())
        mid = _freshness_score((now - datetime.timedelta(days=60)).isoformat())
        old = _freshness_score((now - datetime.timedelta(days=400)).isoformat())
        none = _freshness_score("")
        assert fresh == 6.0 and mid == 5.0 and old == 2.0 and none == 3.0


def _card(name="demo", source="skillhub", sec=0.0, slug=None, updated=None, **kw):
    return SkillCard(
        id=slug or f"slug-{name}",
        name=name,
        display_name=name,
        description="x",
        source=source,
        security_relevant=sec,
        updated_at=updated or "",
        **kw,
    )


class TestScoreSkill:
    def test_non_security_penalized(self):
        sec_card = score_skill(_card(sec=0.6))
        plain = score_skill(_card(sec=0.0))
        seed = score_skill(_card(sec=0.0, source="seed"))
        assert sec_card.scores["final"] > plain.scores["final"]
        # seed 豁免：同分卡片 seed 不低于非 seed
        assert seed.scores["final"] >= plain.scores["final"]

    def test_fresh_wins(self):
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        a = score_skill(_card(updated=(now - datetime.timedelta(days=5)).isoformat()))
        b = score_skill(_card(updated=(now - datetime.timedelta(days=400)).isoformat()))
        assert a.scores["freshness"] > b.scores["freshness"]


class TestDisplayMerge:
    def test_display_key_normalizes(self):
        a = _card(name="Code Review", slug="a/code-review")
        b = _card(name="code  review", slug="b/codereview")
        assert _display_key(a) == _display_key(b)
