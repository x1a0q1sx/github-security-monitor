"""Suggest new keywords (and demotions) from historical kept + noise records.

Lightweight, no ML: extract tokens/bigrams from repo names + descriptions,
compute keep vs noise document frequency, precision, and propose tier/score
changes into a human-reviewable JSON file. Never writes keywords.yaml
automatically.

Usage:
  python -m scripts.suggest_keywords --publish
  python -m scripts.suggest_keywords --min-keep 5 --top 30
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml

from monitor.config import ARCHIVE_DIR, DATA_DIR, DOCS_DATA_DIR, KEYWORDS_CONFIG

STOPWORDS = {
    "a", "an", "the", "and", "or", "for", "to", "of", "in", "on", "with", "by",
    "is", "are", "this", "that", "it", "as", "at", "be", "your", "you", "my",
    "we", "our", "their", "tool", "tools", "project", "repo", "repository",
    "code", "app", "application", "script", "scripts", "use", "using", "used",
    "simple", "easy", "fast", "new", "old", "one", "two", "3", "4", "5",
    "data", "file", "files", "build", "run", "run", "test", "tests", "demo",
    "example", "examples", "based", "based on", "https", "github", "com",
    "please", "note", "via", "from", "into", "not", "no", "yes", "ok",
    "which", "when", "where", "what", "how", "why", "all", "can", "will",
    "just", "like", "also", "more", "most", "very", "much", "than", "then",
    "open", "source", "opensource", "library", "package", "module", "plugin",
    "framework", "frameworks", "implementation", "implement", "support",
    "supports", "feature", "features", "version", "release", "update",
    "updated", "create", "created", "make", "made", "let", "allows", "allow",
}

# Tokens that, if alone, are clearly non-security generic noise
GENERIC_NOISE = {
    "personal", "readme", "homework", "assignment", "course", "tutorial",
    "notes", "portfolio", "resume", "blog", "website", "site", "landing",
    "page", "template", "starter", "boilerplate", "config", "setup",
}

MAX_NGRAM = 3
MIN_TOKEN_LEN = 2


def _split_name(name: str) -> List[str]:
    # OpenMalleableC2 -> open malleable c2 ; shellcode-loader -> shellcode loader
    s = re.sub(r"[^A-Za-z0-9]+", " ", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    return [t for t in s.lower().split() if t]


def _tokens_text(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9一-鿿]+", text.lower()) if len(t) >= MIN_TOKEN_LEN]


def _ngrams(tokens: List[str], max_n: int) -> List[str]:
    out: List[str] = []
    for n in range(1, max_n + 1):
        for i in range(len(tokens) - n + 1):
            gram = " ".join(tokens[i : i + n])
            if len(gram) < 3:
                continue
            out.append(gram)
    return out


def _is_stop_gram(gram: str) -> bool:
    parts = gram.split()
    if all(p in STOPWORDS for p in parts):
        return True
    if len(parts) == 1 and parts[0] in STOPWORDS:
        return True
    if len(parts) == 1 and parts[0] in GENERIC_NOISE:
        return True
    # ngram ending/starting with stopword noise
    if parts[0] in STOPWORDS or parts[-1] in STOPWORDS:
        return True
    return False


def _looks_proper(gram: str, name: str) -> bool:
    """Heuristic: tool/technique proper-noun shape (came from a repo name or CamelCase)."""
    parts = gram.split()
    # multi-word phrases with a security-ish token are good
    if len(parts) >= 2:
        return True
    tok = parts[0]
    if tok in GENERIC_NOISE or tok in STOPWORDS:
        return False
    # single token: prefer if it appeared in the repo name (proper noun vibe)
    name_tokens = set(_split_name(name))
    return tok in name_tokens and len(tok) >= 4


def _iter_record_texts(items: List[dict]) -> List[Tuple[str, str]]:
    """Return list of (name, normalized_text) per record."""
    out = []
    for it in items:
        name = it.get("repo_name") or ""
        desc = it.get("repo_description") or ""
        topics = " ".join(it.get("topics") or [])
        tags = it.get("tags") or ""
        text = f"{name} {desc} {topics} {tags}".lower()
        out.append((name, text))
    return out


def _existing_keywords(keywords_cfg: Dict) -> Set[str]:
    exist: Set[str] = set()
    for tier in ("S", "A", "B"):
        for it in (keywords_cfg.get("tiers") or {}).get(tier) or []:
            k = (it.get("keyword") or "").strip().lower()
            if k:
                exist.add(k)
    for t in (keywords_cfg.get("security_context") or []):
        exist.add(t.lower())
    return exist


def _is_covered_by_existing(gram: str, exist: Set[str]) -> bool:
    """Skip tokens/phrases already covered by existing keywords (substring or token)."""
    g = gram.lower().strip()
    if g in exist:
        return True
    g_parts = set(g.split())
    for ek in exist:
        ek_parts = set(ek.split())
        # gram tokens are subset of an existing keyword (or vice-versa for short grams)
        if g_parts and g_parts.issubset(ek_parts):
            return True
        if ek_parts and ek_parts.issubset(g_parts) and len(ek_parts) >= 2:
            return True
        if " " in ek and (g == ek or f" {g} " in f" {ek} " or ek.startswith(g + " ") or ek.endswith(" " + g)):
            return True
        if g in ek.split():
            return True
        if len(g) >= 4 and (g in ek or ek in g):
            return True
    return False


def _quality_ok(gram: str) -> bool:
    parts = gram.split()
    if all(p.isdigit() for p in parts):
        return False
    WEAK_SINGLE = {
        "loader", "bypass", "injection", "execution", "payload", "profile",
        "profiles", "server", "client", "framework", "analysis", "research",
        "strike", "cobalt", "memory", "process", "thread", "system", "direct",
        "indirect", "manual", "early", "sleep", "stack", "module", "api",
        "operators", "teamers", "operator", "team", "gate", "gates", "proof",
        "concept", "testing", "security", "authorized", "bounty", "bug",
        "mitre", "att", "ck", "malleable", "red", "post", "code", "remote",
        "windows", "linux", "python", "golang", "rust", "csharp",
    }
    KNOWN_FRAGMENTS = {
        "cobalt", "strike", "malleable", "shellcode", "syscall", "syscalls",
        "amsi", "etw", "edr", "rce", "cve", "c2", "beacon", "webshell",
        "red", "team", "redteam", "exploit", "poc", "payload", "loader",
    }
    if len(parts) == 1:
        p = parts[0]
        if re.fullmatch(r"[a-z0-9]+", p):
            if len(p) < 5 or p in WEAK_SINGLE or p in KNOWN_FRAGMENTS:
                return False
    if len(parts) >= 2:
        if any(p.isdigit() for p in parts):
            return False
        if all(p in WEAK_SINGLE or p in STOPWORDS or p in KNOWN_FRAGMENTS for p in parts):
            return False
        # reject if >= half are known fragments of existing S/A terms
        known_ratio = sum(1 for p in parts if p in KNOWN_FRAGMENTS) / len(parts)
        if known_ratio >= 0.67:
            return False
    return True


def _extract_from_record(it: dict, max_ngram: int) -> Set[str]:
    name = it.get("repo_name") or ""
    desc = it.get("repo_description") or ""
    topics = " ".join(it.get("topics") or [])
    tags = it.get("tags") or ""
    # include monitor_keyword plain names
    mk = it.get("monitor_keyword") or ""
    mk_clean = re.sub(r"\([^)]*\)", " ", mk)
    text = f"{name} {desc} {topics} {tags} {mk_clean}".lower()
    toks = _tokens_text(text)
    grams = set()
    for gram in _ngrams(toks, max_ngram):
        if not _is_stop_gram(gram):
            grams.add(gram)
    # also keep name-derived bigrams preferentially
    name_toks = _split_name(name.split("/")[-1] if "/" in name else name)
    for gram in _ngrams(name_toks, min(2, max_ngram)):
        if not _is_stop_gram(gram):
            grams.add(gram)
    return grams


def _load_noise_items() -> List[dict]:
    items: List[dict] = []
    for p in sorted(ARCHIVE_DIR.glob("noise-*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            items.extend(data.get("items") or [])
        except Exception:
            continue
    return items


def suggest_keywords(
    min_keep: int = 3,
    min_noise: int = 10,
    top: int = 40,
    max_ngram: int = MAX_NGRAM,
) -> Dict:
    keywords_cfg = yaml.safe_load(KEYWORDS_CONFIG.read_text(encoding="utf-8")) if KEYWORDS_CONFIG.exists() else {}
    exist = _existing_keywords(keywords_cfg)

    keep = json.loads((DATA_DIR / "records.json").read_text(encoding="utf-8")).get("items") or []
    noise = _load_noise_items()

    keep_df: Counter = Counter()
    noise_df: Counter = Counter()
    keep_samples: Dict[str, List[str]] = defaultdict(list)
    keep_stars: Dict[str, List[int]] = defaultdict(list)

    for it in keep:
        name = it.get("repo_name") or ""
        stars = int(it.get("stars") or 0)
        for gram in _extract_from_record(it, max_ngram):
            keep_df[gram] += 1
            keep_stars[gram].append(stars)
            if len(keep_samples[gram]) < 3:
                keep_samples[gram].append(name)

    for it in noise:
        for gram in _extract_from_record(it, max_ngram):
            noise_df[gram] += 1

    keep_total = len(keep) or 1
    noise_total = len(noise) or 1
    baseline = keep_total / (keep_total + noise_total + 1e-9)

    suggestions: List[Dict] = []
    for gram, kdf in keep_df.most_common():
        ndf = noise_df.get(gram, 0)
        total = kdf + ndf
        precision = kdf / total if total else 0
        lift = precision / baseline if baseline else 0
        is_proper = _looks_proper(gram, "")
        if _is_covered_by_existing(gram, exist):
            continue
        if not _quality_ok(gram):
            continue
        if kdf < min_keep:
            continue

        action = None
        tier = None
        score = None
        n_parts = len(gram.split())
        if precision >= 0.75 and kdf >= min_keep and n_parts >= 2:
            action = "add"
            tier = "S"
            score = 5 if precision >= 0.85 else 4
        elif precision >= 0.7 and kdf >= min_keep and n_parts == 1 and len(gram) >= 6:
            action = "add"
            tier = "A"
            score = 3
        elif precision >= 0.5 and kdf >= max(min_keep, 3) and n_parts >= 2:
            action = "add"
            tier = "A"
            score = 3
        elif precision >= 0.35 and kdf >= max(min_keep, 4) and n_parts >= 2:
            action = "add"
            tier = "B"
            score = 2
        elif precision < 0.2 and ndf >= min_noise and n_parts >= 2:
            action = "demote"
        if not action:
            continue

        avg_stars = sum(keep_stars.get(gram, [0])) / max(len(keep_stars.get(gram, [1])), 1)
        suggestions.append(
            {
                "keyword": gram,
                "action": action,
                "suggested_tier": tier,
                "suggested_score": score,
                "keep_df": kdf,
                "noise_df": ndf,
                "precision": round(precision, 3),
                "lift": round(lift, 2),
                "avg_stars_keep": round(avg_stars, 1),
                "is_proper": is_proper,
                "samples": keep_samples.get(gram, [])[:3],
            }
        )

    noise_only = []
    for gram, ndf in noise_df.most_common():
        if _is_covered_by_existing(gram, exist) or not _quality_ok(gram):
            continue
        kdf = keep_df.get(gram, 0)
        if kdf >= min_keep:
            continue
        if ndf < min_noise:
            continue
        if _is_stop_gram(gram):
            continue
        if len(gram.split()) < 2 and len(gram) < 6:
            continue
        noise_only.append({"keyword": gram, "noise_df": ndf, "keep_df": kdf})

    suggestions.sort(
        key=lambda x: (x["action"] != "add", -(x["keep_df"] * x["precision"]), -x["noise_df"])
    )
    suggestions = suggestions[:top]

    doc = {
        "version": 5,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "stats": {
            "keep_records": keep_total,
            "noise_records": noise_total,
            "existing_keywords": len(exist),
            "candidates_seen": len(keep_df),
            "suggestions": len(suggestions),
        },
        "suggestions": suggestions,
        "noise_terms": noise_only[:top],
    }
    return doc


def main():
    ap = argparse.ArgumentParser(description="Suggest keyword additions/demotions from history")
    ap.add_argument("--min-keep", type=int, default=5)
    ap.add_argument("--min-noise", type=int, default=10)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--publish", action="store_true", help="Copy output to docs/data/")
    args = ap.parse_args()

    doc = suggest_keywords(min_keep=args.min_keep, min_noise=args.min_noise, top=args.top)
    out = DATA_DIR / "keyword_suggestions.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.publish:
        DOCS_DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DOCS_DATA_DIR / "keyword_suggestions.json").write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"[suggest] wrote {out} — {len(doc['suggestions'])} suggestions, {len(doc['noise_terms'])} noise terms")
    print(json.dumps(doc["stats"], ensure_ascii=False, indent=2))
    print("\nTop suggestions:")
    for s in doc["suggestions"][:15]:
        print(
            f"  {s['action']:6} [{s.get('suggested_tier') or '-'}] "
            f"{s['keyword']:30} keep={s['keep_df']:3} noise={s['noise_df']:3} "
            f"prec={s['precision']:.2f} lift={s['lift']}"
        )


if __name__ == "__main__":
    main()
