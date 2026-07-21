"""Relevance + quality scoring for security repo candidates."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from monitor.models import Record


def _parse_gh_time(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        # 2024-01-01T12:00:00Z
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        m = re.search(r"\d{4}-\d{2}-\d{2}", value)
        if not m:
            return None
        try:
            return datetime.fromisoformat(m.group(0)).replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _norm(text: str) -> str:
    # normalize separators so "amsi-bypass" matches keyword "amsi bypass"
    t = (text or "").lower()
    t = re.sub(r"[_\-/]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _contains(term: str, text: str, whole_word: bool = False) -> bool:
    term = term.lower()
    text = text.lower()
    if not term:
        return False
    if whole_word or re.fullmatch(r"[a-z0-9]{1,4}", term):
        # short tokens: require word boundary-ish
        pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
        return re.search(pattern, text) is not None
    return term in text


@dataclass
class KeywordEntry:
    keyword: str
    score: float
    tier: str  # S/A/B
    whole_word: bool = False


@dataclass
class ScoreResult:
    relevance: float
    quality: float
    final: float
    confidence: str
    matched: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    drop: bool = False
    drop_reason: str = ""


class Scorer:
    def __init__(
        self,
        keywords_cfg: Dict[str, Any],
        noise_cfg: Dict[str, Any],
        monitor_cfg: Dict[str, Any],
        black_users: Optional[Sequence[str]] = None,
    ):
        self.monitor = monitor_cfg or {}
        self.noise = noise_cfg or {}
        self.black_users = set(black_users or [])
        self.black_users.update(self.noise.get("extra_black_users") or [])
        self.black_repos = set(self.noise.get("extra_black_repos") or [])
        self.security_context = [s.lower() for s in (keywords_cfg.get("security_context") or [])]
        self.keywords: List[KeywordEntry] = []
        tiers = keywords_cfg.get("tiers") or {}
        for tier_name in ("S", "A", "B"):
            for item in tiers.get(tier_name) or []:
                kw = (item.get("keyword") or "").strip()
                if not kw:
                    continue
                self.keywords.append(
                    KeywordEntry(
                        keyword=kw,
                        score=float(item.get("score") or 0),
                        tier=tier_name,
                        whole_word=bool(item.get("whole_word")),
                    )
                )
        # longest first for nicer match display
        self.keywords.sort(key=lambda k: len(k.keyword), reverse=True)

        self.drop_name_res = [re.compile(p) for p in self.noise.get("drop_name_patterns") or []]
        self.drop_desc_res = [re.compile(p) for p in self.noise.get("drop_description_patterns") or []]
        self.penalty_terms = self.noise.get("penalty_terms") or []
        self.false_friends = self.noise.get("false_friend_patterns") or []

        self.min_final = float(self.monitor.get("min_final_score", 6.0))
        self.min_stars = int(self.monitor.get("min_stars", 0))
        self.allow_zero_precise = bool(self.monitor.get("allow_zero_stars_if_precise", True))
        self.skip_forks = bool(self.monitor.get("skip_forks", True))
        self.skip_no_info = bool(self.monitor.get("skip_no_info", True))

    def _text_blob(self, record: Record) -> str:
        topics = " ".join(record.topics or [])
        return " ".join(
            [
                record.repo_name or "",
                record.repo_description or "",
                topics,
                record.monitor_keyword or "",
            ]
        ).lower()

    def _hard_drop(self, record: Record, text: str) -> Optional[str]:
        author = (record.author or "").lower()
        if author and author in {b.lower() for b in self.black_users}:
            return "black_user"
        if record.repo_url in self.black_repos or record.repo_name in self.black_repos:
            return "black_repo"
        if self.skip_forks and record.is_fork:
            return "fork"
        name = record.repo_name or ""
        desc = record.repo_description or ""
        for rx in self.drop_name_res:
            if rx.search(name):
                return f"drop_name:{rx.pattern}"
        for rx in self.drop_desc_res:
            if rx.search(desc):
                return f"drop_desc:{rx.pattern}"
        if self.skip_no_info:
            if not (desc or "").strip() and (record.stars or 0) == 0 and not (record.language or "").strip():
                return "no_info"
        return None

    def _false_friend_blocked(self, token: str, text: str, name: str) -> bool:
        for rule in self.false_friends:
            if (rule.get("token") or "").lower() != token.lower():
                continue
            unless = [u.lower() for u in rule.get("unless_any") or []]
            if any(u in text for u in unless):
                return False
            for pat in rule.get("name_block_patterns") or []:
                if re.search(pat, name):
                    return True
            # default: blocked if no unless context
            return True
        return False

    def match_keywords(self, record: Record) -> Tuple[float, List[str], List[str], Dict[str, int]]:
        text = self._text_blob(record)
        name = _norm(record.repo_name)
        desc = _norm(record.repo_description)
        matched_detail: List[str] = []
        matched_kws: List[str] = []
        tier_hits = {"S": 0, "A": 0, "B": 0}
        relevance = 0.0
        seen = set()

        for entry in self.keywords:
            key = entry.keyword.lower()
            if key in seen:
                continue
            # prefer name hits
            in_name = _contains(entry.keyword, name, entry.whole_word or len(entry.keyword) <= 3)
            in_desc = _contains(entry.keyword, desc, entry.whole_word or len(entry.keyword) <= 3)
            in_all = in_name or in_desc or _contains(entry.keyword, text, entry.whole_word or len(entry.keyword) <= 3)
            if not in_all:
                continue

            if entry.tier == "A" and self._false_friend_blocked(entry.keyword, text, name):
                continue

            seen.add(key)
            weight = 1.0
            if in_name:
                weight = 1.25
            score = entry.score * weight
            relevance += score
            tier_hits[entry.tier] = tier_hits.get(entry.tier, 0) + 1
            matched_detail.append(f"{entry.keyword}({entry.score:.0f}{entry.tier})")
            matched_kws.append(entry.keyword)

        # context unlock bonus for tier-A without S
        has_context = any(ctx in text for ctx in self.security_context)
        if tier_hits.get("A", 0) and not tier_hits.get("S", 0):
            if has_context:
                relevance += 1.5
                matched_detail.append("security_context(+1.5)")
            else:
                # alone A terms are heavily discounted
                relevance *= 0.45
                matched_detail.append("A_without_context(x0.45)")

        # pure B-only is weak
        if tier_hits.get("B", 0) and not tier_hits.get("S", 0) and not tier_hits.get("A", 0):
            relevance *= 0.5
            matched_detail.append("B_only(x0.5)")

        return relevance, matched_detail, matched_kws, tier_hits

    def quality_score(self, record: Record) -> Tuple[float, List[str]]:
        reasons: List[str] = []
        q = 0.0
        stars = int(record.stars or 0)
        desc = (record.repo_description or "").strip()
        lang = (record.language or "").strip()
        topics = record.topics or []

        if stars >= 500:
            q += 4.5
            reasons.append("stars>=500")
        elif stars >= 100:
            q += 3.5
            reasons.append("stars>=100")
        elif stars >= 20:
            q += 2.5
            reasons.append("stars>=20")
        elif stars >= 5:
            q += 1.5
            reasons.append("stars>=5")
        elif stars >= 1:
            q += 0.7
            reasons.append("stars>=1")
        else:
            # mild penalty; precise hits can still pass with good relevance
            q -= 0.6
            reasons.append("stars=0")

        if desc:
            q += 1.2
            if len(desc) >= 40:
                q += 0.4
                reasons.append("desc_ok")
            else:
                reasons.append("desc_short")
        else:
            q -= 1.5
            reasons.append("empty_desc")

        if lang:
            q += 0.6
        else:
            q -= 0.4
            reasons.append("no_language")

        if topics:
            q += min(1.0, 0.25 * len(topics))
            sec_topics = {"security", "pentest", "malware", "redteam", "infosec", "exploit", "vulnerability"}
            if any(t.lower() in sec_topics for t in topics):
                q += 0.8
                reasons.append("security_topic")

        pushed = _parse_gh_time(record.pushed_at)
        if pushed:
            if pushed.tzinfo is None:
                pushed = pushed.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - pushed
            if age <= timedelta(days=7):
                q += 1.2
                reasons.append("pushed_7d")
            elif age <= timedelta(days=30):
                q += 0.6
                reasons.append("pushed_30d")
            elif age >= timedelta(days=365):
                q -= 0.8
                reasons.append("stale_1y")

        if record.is_fork:
            q -= 2.0
            reasons.append("fork")

        # penalties from noise terms
        blob = self._text_blob(record)
        for p in self.penalty_terms:
            term = (p.get("term") or "").lower()
            if term and term in blob:
                pen = float(p.get("penalty") or 0)
                q -= pen
                reasons.append(f"penalty:{term}")

        # clamp
        return max(-3.0, min(q, 10.0)), reasons

    def score(self, record: Record, forced_relevance: Optional[float] = None) -> ScoreResult:
        text = self._text_blob(record)
        drop = self._hard_drop(record, text)
        if drop:
            return ScoreResult(
                relevance=0,
                quality=0,
                final=0,
                confidence="noise",
                drop=True,
                drop_reason=drop,
                reasons=[f"drop:{drop}"],
            )

        if forced_relevance is not None:
            relevance = forced_relevance
            matched_detail = [record.monitor_keyword] if record.monitor_keyword else []
            matched_kws = []
            tier_hits = {"S": 1, "A": 0, "B": 0}
        else:
            relevance, matched_detail, matched_kws, tier_hits = self.match_keywords(record)

        # monitor-type base relevance boosts
        if record.monitor_type == "cve":
            if re.search(r"CVE-\d{4}-\d+", (record.repo_name or "") + " " + (record.repo_description or ""), re.I):
                relevance = max(relevance, 5.0)
                matched_detail.append("cve_id(+)")
            else:
                relevance = max(relevance, 2.0)
        elif record.monitor_type in ("user_repo", "tool", "tool_update"):
            relevance = max(relevance, 5.5)
            matched_detail.append(f"source:{record.monitor_type}")

        quality, q_reasons = self.quality_score(record)

        # normalize relevance roughly into 0-10
        rel_norm = max(0.0, min(relevance, 12.0))
        # B-balance weights
        final = rel_norm * 0.55 + quality * 0.45

        reasons = matched_detail + q_reasons

        # star gate
        stars = int(record.stars or 0)
        precise = tier_hits.get("S", 0) > 0 or record.monitor_type in ("tool", "tool_update", "user_repo", "cve")
        has_desc = bool((record.repo_description or "").strip())
        if stars < self.min_stars:
            final -= 2.0
            reasons.append("below_min_stars")
        # Zero-star soft penalty: skip for precise S hits that already have a description.
        if stars == 0 and record.monitor_type == "keyword":
            if self.allow_zero_precise and precise and has_desc:
                reasons.append("zero_star_precise_ok")
            else:
                final -= 1.5
                reasons.append("zero_star_soft_penalty")

        # confidence bands
        if final >= 8.0 and (precise or record.monitor_type in ("tool_update", "user_repo")):
            confidence = "high"
        elif final >= self.min_final:
            confidence = "medium"
        elif final >= self.min_final - 1.5:
            confidence = "low"
        else:
            confidence = "noise"

        # final gate for keepers decided by caller via min_final_score
        return ScoreResult(
            relevance=round(rel_norm, 2),
            quality=round(quality, 2),
            final=round(final, 2),
            confidence=confidence,
            matched=matched_detail,
            reasons=reasons,
            drop=False,
        )

    def apply(self, record: Record, forced_relevance: Optional[float] = None) -> ScoreResult:
        result = self.score(record, forced_relevance=forced_relevance)
        record.relevance_score = result.relevance
        record.quality_score = result.quality
        record.final_score = result.final
        record.confidence = result.confidence
        record.reasons = result.reasons[:24]
        if result.matched:
            record.matched_keywords = [
                m.split("(")[0] for m in result.matched if not m.startswith("security_") and not m.endswith(")")
            ] or record.matched_keywords
            # better: parse matched keyword list from detail
            kws = []
            for m in result.matched:
                if "(" in m and not m.startswith("A_") and not m.startswith("B_") and not m.startswith("security_"):
                    kws.append(m.split("(")[0])
            if kws:
                record.matched_keywords = kws
                record.monitor_keyword = ", ".join(result.matched[:8])
        if not record.tags:
            record.tags = self._tags(record)
        return result

    def _tags(self, record: Record) -> str:
        text = self._text_blob(record)
        tags: List[str] = []
        mapping = {
            "C2": ["c2", "beacon", "teamserver", "cobalt", "sliver", "mythic", "havoc"],
            "免杀": ["bypass", "evasion", "unhook", "amsi", "etw", "免杀"],
            "Shellcode": ["shellcode"],
            "POC": ["poc", "proof of concept"],
            "漏洞利用": ["exploit", "rce", "vulnerability"],
            "Webshell": ["webshell", "godzilla", "behinder"],
            "红队": ["redteam", "red team", "红队"],
            "CVE": ["cve-"],
        }
        for tag, kws in mapping.items():
            if any(k in text for k in kws):
                tags.append(tag)
        if record.monitor_type == "cve" and "CVE" not in tags:
            tags.insert(0, "CVE")
        if record.monitor_type in ("tool", "tool_update") and "工具" not in tags:
            tags.insert(0, "工具更新")
        if record.monitor_type == "user_repo":
            tags.insert(0, "大佬新作")
        return ",".join(tags[:6]) if tags else record.monitor_type
