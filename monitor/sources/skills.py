"""Skill discovery from SkillHub + GitHub + seed catalog."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import requests

from monitor.github_client import GitHubClient
from monitor.models import SkillCard, now_str


def _norm_id(*parts: str) -> str:
    raw = "/".join(p.strip() for p in parts if p and str(p).strip())
    return re.sub(r"\s+", "-", raw.lower())


def _match_term(term: str, text: str) -> bool:
    """Word-boundary matching for ASCII terms; substring for CJK terms.

    Prevents pollution like css∈"success", pr∈"pattern", test∈"pentest",
    threat∈"thread" — the main cause of bogus skill categories.
    """
    term = (term or "").strip().lower()
    text = (text or "").lower()
    if not term:
        return False
    if any("\u4e00" <= ch <= "\u9fff" for ch in term):
        return term in text
    # tolerate English plural ("unit test" matches "unit tests") but nothing else:
    # "test" still refuses "testing" because the s/es suffix can't cover "in"
    pattern = re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?:es|s)?(?![a-z0-9])")
    return pattern.search(text) is not None


def _security_score(text: str, boost_terms: Sequence[str]) -> float:
    """0..1 security relevance.

    Saturating curve: 1 hit=0.25, 2=0.40 (>=0.4 counts as security-relevant,
    same threshold semantics as before), 3=0.55 ... 6+=1.0.
    """
    t = (text or "").lower()
    hits = sum(1 for term in boost_terms if _match_term(term, t))
    if hits <= 0:
        return 0.0
    return min(1.0, 0.25 + 0.15 * (hits - 1))


def _categorize(
    text: str,
    category_keywords: Dict[str, List[str]],
    max_cats: int = 3,
    primary: str = "security",
) -> List[str]:
    """Category assignment by word-boundary keyword hits.

    - sorted by hit count desc, capped at max_cats
    - primary category (security) suppresses weak categories: when security
      hits, another category needs >=2 distinct keyword hits to be attached
      (so "penetration testing" no longer drags skills into `testing`)
    """
    t = (text or "").lower()
    hits: Dict[str, int] = {}
    for cat, kws in (category_keywords or {}).items():
        n = sum(1 for k in kws if _match_term(k, t))
        if n:
            hits[cat] = n

    cats: List[str] = []
    if primary in hits:
        cats.append(primary)
        for cat, n in sorted(hits.items(), key=lambda x: (-x[1], x[0])):
            if cat == primary:
                continue
            if n >= 2:
                cats.append(cat)
    else:
        for cat, n in sorted(hits.items(), key=lambda x: (-x[1], x[0])):
            cats.append(cat)
    return cats[:max_cats] or ["general"]


def _freshness_score(updated_at: str) -> float:
    """Time-decay freshness: recently updated skills rank higher.

    <=30d: 6.0, <=90d: 5.0, <=365d: 4.0, older: 2.0, unknown: 3.0.
    """
    if not updated_at or not str(updated_at).strip():
        return 3.0
    s = str(updated_at).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return 4.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - dt).days
    if days <= 30:
        return 6.0
    if days <= 90:
        return 5.0
    if days <= 365:
        return 4.0
    return 2.0


def score_skill(
    card: SkillCard,
    prefer_security_weight: float = 1.35,
    installed: Optional[Set[str]] = None,
) -> SkillCard:
    stats = card.stats or {}
    installs = float(stats.get("installs") or 0)
    downloads = float(stats.get("downloads") or 0)
    stars = float(stats.get("stars") or 0)

    pop = 0.0
    # log-ish manual scale
    volume = installs + downloads * 0.3 + stars * 5
    if volume >= 100000:
        pop = 9.5
    elif volume >= 10000:
        pop = 8.0
    elif volume >= 1000:
        pop = 6.5
    elif volume >= 100:
        pop = 5.0
    elif volume >= 10:
        pop = 3.5
    elif volume > 0:
        pop = 2.0
    else:
        pop = 1.0 if card.source == "seed" else 0.5

    freshness = _freshness_score(card.updated_at)

    relevance = 3.0 + card.security_relevant * 5.0
    if "security" in (card.category or []):
        relevance += 1.0
    # curated seeds are intentionally high-signal
    if card.source == "seed":
        pop = max(pop, 6.0)
        relevance = max(relevance, 5.0)

    final = pop * 0.4 + freshness * 0.15 + relevance * 0.45
    if card.security_relevant >= 0.4:
        final *= prefer_security_weight
    else:
        # non-security skills must clear a higher bar in a security-focused
        # catalog; curated seeds are exempt (find-skills etc. are intentional)
        if card.source != "seed":
            final *= 0.85

    reasons = []
    if installs:
        reasons.append(f"installs:{int(installs)}")
    if stars:
        reasons.append(f"stars:{int(stars)}")
    if card.security_relevant >= 0.4:
        reasons.append(f"security:{card.security_relevant:.2f}")
    if card.source:
        reasons.append(f"source:{card.source}")

    installed = installed or set()
    if card.id in installed or card.name in installed:
        final *= 0.2
        reasons.append("already_installed")

    card.scores = {
        "popularity": round(pop, 2),
        "freshness": round(freshness, 2),
        "relevance": round(relevance, 2),
        "final": round(min(final, 10.0), 2),
    }
    card.reasons = reasons
    return card


def _display_key(card: SkillCard) -> str:
    """Normalized display name for same-name merging across sources."""
    name = (card.display_name or card.name or "").strip().lower()
    return re.sub(r"\s+", "", name)


def fetch_skillhub(
    base_url: str,
    queries: Sequence[str],
    limit_per_query: int,
    boost_terms: Sequence[str],
    category_keywords: Dict[str, List[str]],
) -> List[SkillCard]:
    cards: List[SkillCard] = []
    seen: Set[str] = set()
    session = requests.Session()
    session.headers["User-Agent"] = "github-security-monitor-v5"

    for q in queries:
        url = f"{base_url.rstrip('/')}/api/v1/search"
        try:
            resp = session.get(url, params={"q": q, "limit": limit_per_query}, timeout=20)
            if resp.status_code != 200:
                print(f"  [skillhub] {q}: HTTP {resp.status_code}")
                continue
            data = resp.json()
        except Exception as e:
            print(f"  [skillhub] {q}: {e}")
            continue

        for item in data.get("results") or []:
            slug = item.get("slug") or item.get("name") or ""
            if not slug or slug in seen:
                continue
            seen.add(slug)
            desc = item.get("description") or item.get("summary") or ""
            # SkillHub often ships a Chinese blurb separately
            desc_cn = (
                item.get("description_zh")
                or item.get("summary_zh")
                or item.get("description_cn")
                or ""
            )
            text = f"{item.get('displayName') or item.get('name') or ''} {desc} {desc_cn} {' '.join(item.get('tags') or [])}"
            sec = _security_score(text, boost_terms)
            cats = _categorize(text, category_keywords)
            if sec >= 0.4 and "security" not in cats:
                cats.insert(0, "security")
            install = f"npx skills add {slug} -g -y"
            homepage = item.get("homepage") or f"{base_url}/skills/{slug}"
            cards.append(
                SkillCard(
                    id=slug,
                    name=item.get("name") or slug,
                    display_name=item.get("displayName") or item.get("name") or slug,
                    description=desc,
                    description_cn=desc_cn,
                    source="skillhub",
                    repo_url="",
                    homepage=homepage,
                    install=install,
                    category=cats,
                    tags=list(item.get("tags") or []),
                    stats={
                        "stars": item.get("stars") or 0,
                        "installs": item.get("installs") or 0,
                        "downloads": item.get("downloads") or 0,
                    },
                    security_relevant=sec,
                    updated_at=str(item.get("updated_at") or item.get("updatedAt") or ""),
                )
            )
        print(f"  [skillhub] q={q!r} +{len(data.get('results') or [])}")
    return cards


def fetch_github_skills(
    client: GitHubClient,
    queries: Sequence[str],
    per_page: int,
    boost_terms: Sequence[str],
    category_keywords: Dict[str, List[str]],
) -> List[SkillCard]:
    cards: List[SkillCard] = []
    seen: Set[str] = set()
    for q in queries:
        print(f"  [github-skills] {q}")
        repos = client.search_repos(q, sort="updated", per_page=per_page)
        for repo in repos:
            full = repo.get("full_name") or ""
            if not full or full in seen:
                continue
            if repo.get("fork"):
                continue
            seen.add(full)
            desc = repo.get("description") or ""
            topics = repo.get("topics") or []
            text = f"{full} {desc} {' '.join(topics)}"
            # heuristic: skill-like repos
            if not any(_match_term(k, text) for k in ("skill", "claude", "agent", "cursor", "codex")):
                # still keep if strong security tool skill naming
                if "skill" not in (repo.get("name") or "").lower():
                    continue
            sec = _security_score(text, boost_terms)
            cats = _categorize(text, category_keywords)
            name = (repo.get("name") or full.split("/")[-1]).replace("-skill", "")
            install = f"npx skills add {full} -g -y"
            cards.append(
                SkillCard(
                    id=full,
                    name=name,
                    display_name=name,
                    description=desc,
                    source="github",
                    repo_url=repo.get("html_url") or "",
                    homepage=repo.get("html_url") or "",
                    install=install,
                    category=cats,
                    tags=list(topics),
                    stats={
                        "stars": repo.get("stargazers_count") or 0,
                        "installs": 0,
                        "downloads": 0,
                    },
                    security_relevant=sec,
                    updated_at=repo.get("updated_at") or "",
                )
            )
    return cards


def load_seed_skills(seed_items: Sequence[Dict[str, Any]], boost_terms: Sequence[str]) -> List[SkillCard]:
    cards = []
    for item in seed_items or []:
        desc = item.get("description") or ""
        text = f"{item.get('display_name') or item.get('name')} {desc} {' '.join(item.get('tags') or [])}"
        sec = item.get("security_relevant")
        if sec is None:
            sec = _security_score(text, boost_terms)
        card = SkillCard(
            id=item.get("id") or item.get("name"),
            name=item.get("name") or "",
            display_name=item.get("display_name") or item.get("name") or "",
            description=desc,
            description_cn=item.get("description_cn") or item.get("description_zh") or "",
            source=item.get("source") or "seed",
            repo_url=item.get("repo_url") or "",
            homepage=item.get("homepage") or "",
            install=item.get("install") or "",
            category=list(item.get("category") or []),
            tags=list(item.get("tags") or []),
            stats=dict(item.get("stats") or {}),
            security_relevant=float(sec or 0),
        )
        cards.append(card)
    return cards


def run_skills(
    client: GitHubClient,
    main_skills_cfg: Dict[str, Any],
    skills_file_cfg: Dict[str, Any],
) -> Tuple[List[SkillCard], dict]:
    cfg = main_skills_cfg or {}
    sources = cfg.get("sources") or {}
    boost = skills_file_cfg.get("security_boost_terms") or []
    cat_kw = skills_file_cfg.get("category_keywords") or {}
    installed = set(cfg.get("installed_slugs") or [])
    prefer = float(cfg.get("prefer_security_weight") or 1.35)
    min_final = float(cfg.get("min_final_score") or 5.0)
    max_items = int(cfg.get("max_items") or 80)

    all_cards: List[SkillCard] = []
    print("[SKILLS] discovery start")

    if sources.get("seed", True):
        all_cards.extend(load_seed_skills(skills_file_cfg.get("seed_skills") or [], boost))
        print(f"  seed: {len(skills_file_cfg.get('seed_skills') or [])}")

    if sources.get("skillhub", True):
        sh = cfg.get("skillhub") or {}
        all_cards.extend(
            fetch_skillhub(
                base_url=sh.get("base_url") or "https://lightmake.site",
                queries=sh.get("queries") or ["security"],
                limit_per_query=int(sh.get("limit_per_query") or 15),
                boost_terms=boost,
                category_keywords=cat_kw,
            )
        )

    if sources.get("github", True):
        gh = cfg.get("github") or {}
        all_cards.extend(
            fetch_github_skills(
                client,
                queries=gh.get("queries") or ['"claude skill" security'],
                per_page=int(gh.get("per_page") or 20),
                boost_terms=boost,
                category_keywords=cat_kw,
            )
        )

    # dedupe by id/name, then merge same display_name across sources/slugs
    best: Dict[str, SkillCard] = {}
    for c in all_cards:
        c = score_skill(c, prefer_security_weight=prefer, installed=installed)
        key = (c.id or c.name).lower()
        prev = best.get(key)
        if not prev or (c.scores or {}).get("final", 0) > (prev.scores or {}).get("final", 0):
            best[key] = c

    merged_names = 0
    by_display: Dict[str, SkillCard] = {}
    for c in best.values():
        dk = _display_key(c)
        prev = by_display.get(dk)
        if prev is None:
            by_display[dk] = c
        else:
            merged_names += 1
            if (c.scores or {}).get("final", 0) > (prev.scores or {}).get("final", 0):
                by_display[dk] = c
    unique_cards = list(by_display.values())

    ranked = sorted(unique_cards, key=lambda x: (x.scores or {}).get("final", 0), reverse=True)
    ranked = [c for c in ranked if (c.scores or {}).get("final", 0) >= min_final][:max_items]

    # Fill description_cn when missing (reuse free multi-provider translator)
    translated = 0
    try:
        from monitor.translate import is_mostly_chinese, translate_en_to_zh

        for c in ranked:
            desc = (c.description or "").strip()
            cn = (c.description_cn or "").strip()
            if not desc:
                continue
            if cn and (is_mostly_chinese(cn) or any("一" <= ch <= "鿿" for ch in cn)):
                continue
            if is_mostly_chinese(desc):
                c.description_cn = desc
                continue
            out = translate_en_to_zh(desc)
            if out and out != desc and any("一" <= ch <= "鿿" for ch in out):
                c.description_cn = out
                translated += 1
        if translated:
            print(f"  [skills] translated description_cn for {translated} skills")
    except Exception as e:
        print(f"  [skills] translate skipped: {e}")

    non_security = sum(
        1 for c in ranked
        if c.security_relevant < 0.4 and "security" not in (c.category or [])
    )
    stats = {
        "source": "skills",
        "collected": len(all_cards),
        "unique": len(unique_cards),
        "merged_names": merged_names,
        "kept": len(ranked),
        "security": sum(1 for c in ranked if c.security_relevant >= 0.4 or "security" in (c.category or [])),
        "non_security": non_security,
        "translated": translated,
    }
    print(
        f"[SKILLS] collected={stats['collected']} unique={stats['unique']} "
        f"merged={merged_names} kept={stats['kept']} non_security={non_security}"
    )
    return ranked, stats
