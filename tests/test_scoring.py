"""Scorer V5 测试 — tier 命中、上下文折扣、false-friend、噪声、hard drop。"""
import pytest

from monitor.models import Record
from monitor.scoring import Scorer
from monitor.config import load_keywords_config, load_noise_config


KEYWORDS_CFG = {
    "security_context": ["security", "redteam", "exploit", "malware", "c2", "bypass"],
    "tiers": {
        "S": [
            {"keyword": "cobaltstrike", "score": 8},
            {"keyword": "shadowpad", "score": 8},
        ],
        "A": [
            {"keyword": "c2", "score": 5},
            {"keyword": "rce", "score": 5},
        ],
        "B": [
            {"keyword": "scanner", "score": 2},
        ],
    },
}

NOISE_CFG = {
    "drop_name_patterns": ["(?i)^test[-_]?repo"],
    "drop_description_patterns": ["(?i)my personal readme"],
    "penalty_terms": [
        {"term": "tutorial", "penalty": 1.5},
        {"term": "homework", "penalty": 3.0},
    ],
    "false_friend_patterns": [
        {
            "token": "c2",
            "unless_any": ["command and control", "cobalt", "beacon", "redteam", "c2 framework"],
            "name_block_patterns": ["(?i)c2[0-9a-f]{4,}"],
        }
    ],
    "extra_black_users": ["spammer007"],
}


@pytest.fixture()
def scorer():
    return Scorer(
        keywords_cfg=KEYWORDS_CFG,
        noise_cfg=NOISE_CFG,
        monitor_cfg={"min_final_score": 6.0},
        black_users=["knownbad"],
    )


def _rec(**kw) -> Record:
    base = dict(
        repo_name="tool",
        repo_url="https://github.com/a/tool",
        repo_description="security tool",
    )
    base.update(kw)
    return Record(**base)


class TestMatchKeywords:
    def test_s_tier_hit(self, scorer):
        rel, detail, kws, tiers = scorer.match_keywords(
            _rec(repo_name="cobaltstrike-beacon", repo_description="c2 framework")
        )
        assert tiers["S"] >= 1
        assert "cobaltstrike" in kws

    def test_short_token_word_boundary(self, scorer):
        """c2 不应命中 bc20/hash 类子串"""
        rel, detail, kws, tiers = scorer.match_keywords(
            _rec(repo_name="bc20-encoder", repo_description="image processing")
        )
        assert "c2" not in kws
        assert "rce" not in [k for k in kws]

    def test_rce_not_in_source(self, scorer):
        rel, detail, kws, _ = scorer.match_keywords(
            _rec(repo_name="parser", repo_description="the source code of a config")
        )
        assert "rce" not in kws

    def test_false_friend_blocks_c2_without_context(self, scorer):
        """c2 出现但没有安全上下文 → false-friend 拦截"""
        rel, detail, kws, _ = scorer.match_keywords(
            _rec(repo_name="c2-notes", repo_description="chapter 2 summary of a textbook")
        )
        assert "c2" not in kws

    def test_c2_allowed_with_context(self, scorer):
        rel, detail, kws, _ = scorer.match_keywords(
            _rec(repo_name="c2-server", repo_description="command and control framework for redteam")
        )
        assert "c2" in kws

    def test_a_without_context_discount(self, scorer):
        """仅 A 命中且无 security_context → relevance ×0.45"""
        rel_no_ctx, _, kws, _ = scorer.match_keywords(
            _rec(repo_name="rce-lab", repo_description="rce playground notes")
        )
        # 同仓库带上下文
        rel_ctx, _, _, _ = scorer.match_keywords(
            _rec(repo_name="rce-lab", repo_description="rce exploit for security research")
        )
        assert rel_ctx > rel_no_ctx

    def test_name_hit_weighted_higher(self, scorer):
        """name 命中 ×1.25 > desc 命中"""
        rel_name, _, _, _ = scorer.match_keywords(
            _rec(repo_name="my-scanner", repo_description="network helper")
        )
        rel_desc, _, _, _ = scorer.match_keywords(
            _rec(repo_name="helper", repo_description="a simple scanner")
        )
        assert rel_name > rel_desc


class TestHardDrop:
    def test_black_user(self, scorer):
        r = scorer.score(_rec(author="knownbad"))
        assert r.drop and r.drop_reason == "black_user"

    def test_extra_black_user(self, scorer):
        r = scorer.score(_rec(author="spammer007"))
        assert r.drop and r.drop_reason == "black_user"

    def test_drop_name_pattern(self, scorer):
        r = scorer.score(_rec(repo_name="test_repo_01"))
        assert r.drop and r.drop_reason.startswith("drop_name")

    def test_drop_desc_pattern(self, scorer):
        r = scorer.score(_rec(repo_description="my personal readme"))
        assert r.drop and r.drop_reason.startswith("drop_desc")

    def test_fork_drop(self, scorer):
        r = scorer.score(_rec(is_fork=True))
        assert r.drop and r.drop_reason == "fork"

    def test_no_info_drop(self, scorer):
        r = scorer.score(_rec(repo_description="", stars=0, language=""))
        assert r.drop and r.drop_reason == "no_info"


class TestQualityAndConfidence:
    def test_stars_ladder(self, scorer):
        """stars 越多 quality 越高（非简单 >100 +1）"""
        q0, _ = scorer.quality_score(_rec(stars=0, repo_description="ok tool"))
        q100, _ = scorer.quality_score(_rec(stars=150, repo_description="ok tool"))
        q900, _ = scorer.quality_score(_rec(stars=900, repo_description="ok tool"))
        assert q900 > q100 > q0

    def test_penalty_terms_reduce_quality(self, scorer):
        q_clean, _ = scorer.quality_score(_rec(repo_description="cobaltstrike c2"))
        q_noise, reasons = scorer.quality_score(_rec(repo_description="cobaltstrike tutorial homework"))
        assert q_noise < q_clean

    def test_confidence_bands(self, scorer):
        r = scorer.apply(_rec(repo_name="cobaltstrike-beacon", repo_description="c2 framework", stars=800))
        assert r.confidence in ("high", "medium")

    def test_apply_writes_back(self, scorer):
        rec = _rec(repo_name="cobaltstrike", repo_description="c2 framework", stars=50)
        scorer.apply(rec)
        assert rec.final_score > 0
        assert rec.tags  # 自动打标


class TestCveBoost:
    def test_cve_id_boost(self, scorer):
        rec = _rec(monitor_type="cve", repo_name="CVE-2026-1234", repo_description="poc")
        r = scorer.apply(rec)
        assert any("cve_id(+)" in x or "source:" in x for x in rec.reasons)
