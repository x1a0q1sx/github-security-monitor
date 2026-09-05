"""GitHub Security Monitor V5 — CLI entrypoint.

Usage:
  python -m monitor.run --daily
  python -m monitor.run --trending
  python -m monitor.run --skills
  python -m monitor.run --all
  python -m monitor.run --migrate-only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime
from typing import Any, Dict, List

# project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitor.config import (
    load_keywords_config,
    load_main_config,
    load_noise_config,
    load_skills_config,
)
from monitor.github_client import CircuitOpenError, GitHubClient
from monitor.models import Record
from monitor.notify import alert_from_results, notify_new_records, notify_skills, notify_summary
from monitor.scoring import Scorer
from monitor.storage import Storage


def build_client(cfg: Dict[str, Any]) -> GitHubClient:
    gh = cfg.get("github") or {}
    return GitHubClient(
        token=gh.get("token") or "",
        timeout=int(gh.get("request_timeout") or 20),
        min_interval_ms=int(gh.get("min_request_interval_ms") or 350),
        # Search API ~30/min authenticated → default ~2.2s between search calls
        search_min_interval_ms=int(gh.get("search_min_interval_ms") or 2200),
        max_retries=int(gh.get("max_retries") or 3),
        search_budget=int(gh.get("search_budget") or 0),
        core_budget=int(gh.get("core_budget") or 0),
    )


def build_scorer(cfg: Dict[str, Any]) -> Scorer:
    return Scorer(
        keywords_cfg=load_keywords_config(),
        noise_cfg=load_noise_config(),
        monitor_cfg=cfg.get("monitor") or {},
        black_users=cfg.get("black_users") or [],
    )


def _enrich_cn(cfg: Dict[str, Any], records: List[Record]) -> None:
    """Translate English descriptions when monitor.translate is enabled."""
    mon = cfg.get("monitor") or {}
    if not mon.get("translate", True):
        return
    from monitor.translate import enrich_records_cn

    sleep_ms = float(mon.get("translate_sleep_ms") or 50)
    n = enrich_records_cn(records, enabled=True, sleep_s=max(sleep_ms, 0) / 1000.0)
    if n:
        print(f"  [translate] filled description_cn for {n} records")


def _save_new(
    storage: Storage,
    records: List[Record],
    soft_limit: int,
    cfg: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    if not records:
        return []
    if cfg is not None:
        _enrich_cn(cfg, records)
    result = storage.append_records(records, soft_limit=soft_limit)
    return [r.to_dict() for r in result["items"]]


def run_daily(cfg: Dict[str, Any], storage: Storage, hours: int | None = None) -> Dict[str, Any]:
    from monitor.sources.cve import run_cve
    from monitor.sources.keyword import run_keyword
    from monitor.sources.tool import run_tool
    from monitor.sources.user import run_user

    client = build_client(cfg)
    scorer = build_scorer(cfg)
    mon = cfg.get("monitor") or {}
    min_final = float(mon.get("min_final_score") or 6.0)
    max_author = int(mon.get("max_per_author_per_run") or 3)
    soft_limit = int(mon.get("records_soft_limit") or 8000)
    # hours arg maps roughly to window days
    kw_days = int(mon.get("keyword_window_days") or 3)
    cve_days = int(mon.get("cve_window_days") or 2)
    user_days = int(mon.get("user_window_days") or 7)
    if hours:
        kw_days = max(1, int(round(hours / 24)) or 1)
        cve_days = kw_days

    known = storage.known_urls()
    results: Dict[str, Any] = {}
    all_new: List[Dict[str, Any]] = []

    # CVE
    try:
        kept, stats = run_cve(client, scorer, known, window_days=cve_days, min_final=min_final, max_per_author=max_author)
        added = _save_new(storage, kept, soft_limit, cfg)
        all_new.extend(added)
        results["cve"] = {**stats, "kept": len(added)}
    except Exception as e:
        print(f"[ERROR] CVE: {e}")
        traceback.print_exc()
        results["cve"] = {"kept": -1, "error": str(e)}

    # Keyword
    try:
        known = storage.known_urls()
        kept, stats = run_keyword(
            client,
            scorer,
            load_keywords_config(),
            known,
            window_days=kw_days,
            min_final=min_final,
            max_per_author=max_author,
            per_page=int((cfg.get("github") or {}).get("search_page_size") or 30),
        )
        added = _save_new(storage, kept, soft_limit, cfg)
        all_new.extend(added)
        results["keyword"] = {**stats, "kept": len(added)}
    except Exception as e:
        print(f"[ERROR] Keyword: {e}")
        traceback.print_exc()
        results["keyword"] = {"kept": -1, "error": str(e)}

    # User
    try:
        known = storage.known_urls()
        kept, stats = run_user(
            client,
            scorer,
            cfg.get("users") or [],
            known,
            window_days=user_days,
            min_final=max(5.0, min_final - 1),
            max_per_author=5,
        )
        added = _save_new(storage, kept, soft_limit, cfg)
        all_new.extend(added)
        results["user"] = {**stats, "kept": len(added)}
    except Exception as e:
        print(f"[ERROR] User: {e}")
        traceback.print_exc()
        results["user"] = {"kept": -1, "error": str(e)}

    # Tool
    try:
        known = storage.known_urls()
        state = storage.load_tool_state()
        kept, new_state, stats = run_tool(
            client,
            scorer,
            cfg.get("tools") or [],
            known,
            state,
            min_final=max(5.0, min_final - 1),
        )
        storage.save_tool_state(new_state)
        added = _save_new(storage, kept, soft_limit, cfg)
        all_new.extend(added)
        results["tool"] = {**stats, "kept": len(added)}
    except Exception as e:
        print(f"[ERROR] Tool: {e}")
        traceback.print_exc()
        results["tool"] = {"kept": -1, "error": str(e)}

    notify_new_records(cfg, all_new, prefix="Daily")
    # expose API usage metrics so executions.json shows WHY a source found nothing
    results["api"] = client.metrics()
    alert_from_results(cfg, client, results)
    return results


def run_trending(cfg: Dict[str, Any], storage: Storage) -> Dict[str, Any]:
    from monitor.sources.trending import run_trending as _run

    client = build_client(cfg)
    tcfg = cfg.get("trending") or {}
    doc = _run(
        client,
        queries=tcfg.get("queries") or None,
        min_stars=int(tcfg.get("min_stars") or 50),
        top_n=int(tcfg.get("top_n") or 30),
    )
    storage.save_trending(doc)
    return {"trending": {"kept": doc.get("total", 0)}}


def run_skills(cfg: Dict[str, Any], storage: Storage) -> Dict[str, Any]:
    from monitor.sources.skills import run_skills as _run

    if not (cfg.get("skills") or {}).get("enable", True):
        print("[SKILLS] disabled in config")
        return {"skills": {"kept": 0}}

    client = build_client(cfg)
    cards, stats = _run(client, cfg.get("skills") or {}, load_skills_config())
    doc = {
        "version": 5,
        "items": [c.to_dict() for c in cards],
        "total": len(cards),
        "last_updated": datetime.now().isoformat(timespec="seconds"),
    }
    storage.save_skills(doc)
    notify_skills(cfg, doc["items"])
    return {"skills": stats}


def run_migrate(storage: Storage, cfg: Dict[str, Any]) -> Dict[str, Any]:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "migrate_v5.py"
    spec = importlib.util.spec_from_file_location("migrate_v5", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod.migrate(storage, cfg)


def run_suggest_keywords(storage: Storage) -> Dict[str, Any]:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "suggest_keywords.py"
    spec = importlib.util.spec_from_file_location("suggest_keywords", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    doc = mod.suggest_keywords()
    out = storage.data_dir / "keyword_suggestions.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[suggest] {len(doc.get('suggestions') or [])} suggestions, "
        f"{len(doc.get('noise_terms') or [])} noise terms"
    )
    return {
        "kept": len(doc.get("suggestions") or []),
        "noise_terms": len(doc.get("noise_terms") or []),
        "stats": doc.get("stats") or {},
    }


def _record_status(results: Dict[str, Any]) -> str:
    """Derive execution status from per-source results.

    success: no source failed; partial: some sources failed but others produced
    records; failed: every selected source failed (or nothing succeeded).
    """
    failed = [k for k, v in results.items()
              if isinstance(v, dict) and int(v.get("kept") or 0) < 0]
    if not failed:
        return "success"
    produced = [k for k, v in results.items()
                if isinstance(v, dict) and int(v.get("kept") or 0) > 0]
    return "partial" if produced else "failed"


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GitHub Security Monitor V5")
    parser.add_argument("--daily", action="store_true", help="CVE + keyword + user + tool")
    parser.add_argument("--trending", action="store_true", help="Weekly high-star security catalog")
    parser.add_argument("--skills", action="store_true", help="Discover & rank agent skills")
    parser.add_argument("--all", action="store_true", help="daily + trending + skills")
    parser.add_argument("--cve", action="store_true")
    parser.add_argument("--keyword", action="store_true")
    parser.add_argument("--user", action="store_true")
    parser.add_argument("--tool", action="store_true")
    parser.add_argument("--migrate-only", action="store_true", help="Rescore/archive historical records")
    parser.add_argument(
        "--suggest-keywords",
        action="store_true",
        help="Suggest keyword additions/demotions from keep+noise history",
    )
    parser.add_argument("--hours", type=int, default=0, help="Optional time window hint (hours)")
    parser.add_argument("--publish", action="store_true", help="Copy data/*.json to docs/data/")
    args = parser.parse_args(argv)

    cfg = load_main_config()
    storage = Storage()
    start = datetime.now()
    print("=" * 50)
    print("GitHub Security Monitor V5")
    print(f"Started: {start.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    results: Dict[str, Any] = {}

    selected = any(
        [
            args.daily,
            args.trending,
            args.skills,
            args.all,
            args.cve,
            args.keyword,
            args.user,
            args.tool,
            args.migrate_only,
            args.suggest_keywords,
        ]
    )
    if not selected:
        parser.print_help()
        return 2

    fatal_error = ""
    try:
        if args.migrate_only:
            results["migrate"] = run_migrate(storage, cfg)

        if args.all or args.daily:
            results.update(run_daily(cfg, storage, hours=args.hours or None))
        if args.all or args.trending:
            results.update(run_trending(cfg, storage))
        if args.all or args.skills:
            results.update(run_skills(cfg, storage))
        if args.suggest_keywords or args.all:
            results["suggest_keywords"] = run_suggest_keywords(storage)

        # individual sources
        if args.cve or args.keyword or args.user or args.tool:
            # reuse daily pieces
            mon = cfg.get("monitor") or {}
            client = build_client(cfg)
            scorer = build_scorer(cfg)
            known = storage.known_urls()
            min_final = float(mon.get("min_final_score") or 6.0)
            soft_limit = int(mon.get("records_soft_limit") or 8000)
            if args.cve:
                from monitor.sources.cve import run_cve

                kept, stats = run_cve(client, scorer, known, window_days=int(mon.get("cve_window_days") or 2), min_final=min_final)
                added = _save_new(storage, kept, soft_limit, cfg)
                results["cve"] = {**stats, "kept": len(added)}
            if args.keyword:
                from monitor.sources.keyword import run_keyword

                known = storage.known_urls()
                kept, stats = run_keyword(
                    client, scorer, load_keywords_config(), known,
                    window_days=int(mon.get("keyword_window_days") or 3), min_final=min_final,
                )
                added = _save_new(storage, kept, soft_limit, cfg)
                results["keyword"] = {**stats, "kept": len(added)}
            if args.user:
                from monitor.sources.user import run_user

                known = storage.known_urls()
                kept, stats = run_user(client, scorer, cfg.get("users") or [], known, window_days=int(mon.get("user_window_days") or 7))
                added = _save_new(storage, kept, soft_limit, cfg)
                results["user"] = {**stats, "kept": len(added)}
            if args.tool:
                from monitor.sources.tool import run_tool

                known = storage.known_urls()
                state = storage.load_tool_state()
                kept, new_state, stats = run_tool(client, scorer, cfg.get("tools") or [], known, state)
                storage.save_tool_state(new_state)
                added = _save_new(storage, kept, soft_limit, cfg)
                results["tool"] = {**stats, "kept": len(added)}

        notify_summary(cfg, {k: (v.get("kept") if isinstance(v, dict) else v) for k, v in results.items()})
    except KeyboardInterrupt:
        fatal_error = "interrupted by user"
        print(f"[FATAL] {fatal_error}")
    except Exception as e:
        # Uncaught error must still land in executions.json, not vanish silently
        fatal_error = f"{type(e).__name__}: {e}"
        print(f"[FATAL] {fatal_error}")
        traceback.print_exc()
    finally:
        if args.publish or args.daily or args.trending or args.skills or args.all or args.migrate_only:
            try:
                storage.publish_to_docs()
            except Exception as e:
                print(f"[ERROR] publish_to_docs: {e}")

        end = datetime.now()
        total_new = 0
        for v in results.values():
            if isinstance(v, dict):
                try:
                    total_new += max(int(v.get("kept") or 0), 0)
                except Exception:
                    pass

        status = _record_status(results)
        if fatal_error and not results:
            status = "failed"

        storage.append_execution(
            {
                "type": "all" if args.all else "daily" if args.daily else "custom",
                "status": status,
                "started_at": start.isoformat(timespec="seconds"),
                "finished_at": end.isoformat(timespec="seconds"),
                "duration_seconds": (end - start).total_seconds(),
                "results": results,
                "total_new": total_new,
                "version": 5,
                **({"error": fatal_error} if fatal_error else {}),
            }
        )

        print("=" * 50)
        print(f"COMPLETED in {(end - start).total_seconds():.1f}s")
        print(f"Status: {status}")
        print(f"Results: {results}")
        print(f"Total new/kept: {total_new}")
        print("=" * 50)

    return 1 if fatal_error and not results else 0


if __name__ == "__main__":
    raise SystemExit(main())
