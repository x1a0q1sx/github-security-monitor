#!/usr/bin/env python3
"""Compare single-keyword search vs batched OR search on GitHub.

This is the key reason V5 candidate volume collapses vs V4.

Usage:
  export GITHUB_TOKEN=ghp_xxx   # or GH_TOKEN
  export HTTPS_PROXY=socks5h://127.0.0.1:10808   # if needed
  python scripts/compare_search_modes.py
  python scripts/compare_search_modes.py --hours 72 --per-page 30
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple
from urllib.parse import quote

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

KEYWORDS = ROOT / "config" / "keywords.yaml"


def _token() -> str:
    return os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or ""


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "Accept": "application/vnd.github+json",
            "User-Agent": "github-security-monitor-search-compare",
        }
    )
    tok = _token()
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    return s


def search(sess: requests.Session, q: str, per_page: int = 30) -> Dict[str, Any]:
    url = "https://api.github.com/search/repositories"
    params = {"q": q, "sort": "updated", "order": "desc", "per_page": per_page}
    t0 = time.time()
    r = sess.get(url, params=params, timeout=30)
    elapsed = time.time() - t0
    if r.status_code != 200:
        return {
            "ok": False,
            "status": r.status_code,
            "error": r.text[:200],
            "elapsed": elapsed,
            "total_count": 0,
            "items": [],
            "incomplete": False,
        }
    data = r.json()
    return {
        "ok": True,
        "status": 200,
        "elapsed": elapsed,
        "total_count": int(data.get("total_count") or 0),
        "incomplete": bool(data.get("incomplete_results")),
        "items": data.get("items") or [],
        "error": "",
    }


def urls(items: List[Dict]) -> Set[str]:
    return {i.get("html_url") or "" for i in items if i.get("html_url")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-page", type=int, default=30)
    ap.add_argument("--sleep", type=float, default=0.8, help="sleep between API calls")
    ap.add_argument("--limit-single", type=int, default=20, help="max single-keyword probes (S tier first)")
    args = ap.parse_args()

    if not _token():
        print("WARNING: no GITHUB_TOKEN/GH_TOKEN — anonymous search limit is very low (10/min).")
    cfg = yaml.safe_load(KEYWORDS.read_text(encoding="utf-8"))
    tiers = cfg.get("tiers") or {}
    batches = cfg.get("search_queries") or {}

    sess = _session()
    # rate
    try:
        rl = sess.get("https://api.github.com/rate_limit", timeout=20).json()
        search_rl = (rl.get("resources") or {}).get("search") or {}
        print(f"search remaining={search_rl.get('remaining')}/{search_rl.get('limit')}")
    except Exception as e:
        print(f"rate_limit check failed: {e}")

    print("\n=== A) V5 batched queries (current production path) ===")
    batch_urls: Set[str] = set()
    batch_total = 0
    batch_fetched = 0
    for tier in ("S", "A", "B"):
        for q in batches.get(tier) or []:
            res = search(sess, q, per_page=args.per_page)
            time.sleep(args.sleep)
            n = len(res["items"])
            u = urls(res["items"])
            batch_urls |= u
            batch_total += res["total_count"]
            batch_fetched += n
            status = "OK" if res["ok"] else f"ERR{res['status']}"
            print(
                f"  [{tier}] total_count={res['total_count']:>6} fetched={n:>2} "
                f"incomplete={res['incomplete']} {status}  q={q[:80]}"
            )
            if not res["ok"]:
                print(f"       {res['error']}")
    print(f"BATCH unique urls (first page only): {len(batch_urls)}")
    print(f"BATCH sum(total_count) across queries (with overlap): {batch_total}")
    print(f"BATCH fetched items (raw, with overlap): {batch_fetched}")

    print("\n=== B) V4-style single keyword queries (sample of S/A) ===")
    singles: List[Tuple[str, str, int]] = []
    for tier in ("S", "A"):
        for it in tiers.get(tier) or []:
            kw = (it.get("keyword") or "").strip()
            if not kw:
                continue
            singles.append((tier, kw, int(it.get("score") or 0)))
    # prioritize higher score
    singles.sort(key=lambda x: (-x[2], x[0], x[1]))
    singles = singles[: args.limit_single]

    single_urls: Set[str] = set()
    single_total = 0
    single_fetched = 0
    for tier, kw, score in singles:
        # phrase if multi-word
        q = f'"{kw}"' if " " in kw else kw
        res = search(sess, q, per_page=args.per_page)
        time.sleep(args.sleep)
        n = len(res["items"])
        u = urls(res["items"])
        single_urls |= u
        single_total += res["total_count"]
        single_fetched += n
        only_here = len(u - batch_urls)
        status = "OK" if res["ok"] else f"ERR{res['status']}"
        print(
            f"  [{tier}/{score}] total={res['total_count']:>6} fetched={n:>2} "
            f"only_vs_batch={only_here:>2} {status}  kw={kw}"
        )

    print("\n=== Comparison ===")
    print(f"single-sample unique urls: {len(single_urls)}  (from {len(singles)} keywords)")
    print(f"batch unique urls:         {len(batch_urls)}  (from all V5 search_queries)")
    print(f"overlap:                   {len(single_urls & batch_urls)}")
    print(f"ONLY in single-sample:      {len(single_urls - batch_urls)}")
    print(f"ONLY in batch:             {len(batch_urls - single_urls)}")
    print(
        "\nNOTE: GitHub Search returns at most `per_page` items PER QUERY string.\n"
        "A batch like (A OR B OR C OR D) still returns only 30 hits total,\n"
        "not 30 per term. V4 issued one query per keyword, so coverage was much higher."
    )

    # Theoretical coverage of keywords in batch strings
    qtext = " ".join(q for qs in batches.values() for q in qs).lower()
    missing = []
    for tier in ("S", "A", "B"):
        for it in tiers.get(tier) or []:
            kw = (it.get("keyword") or "").lower()
            if kw and kw not in qtext:
                missing.append((tier, it.get("score"), it.get("keyword")))
    print(f"\nKeywords not present in any batch query string: {len(missing)}")
    for tier, score, kw in missing[:40]:
        print(f"  missing [{tier}/{score}] {kw}")
    if len(missing) > 40:
        print(f"  ... +{len(missing)-40} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
