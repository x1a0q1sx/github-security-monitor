"""Rescore historical records, archive noise, write V5 records.json."""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from monitor.config import load_keywords_config, load_main_config, load_noise_config
from monitor.models import Record, empty_records_doc
from monitor.scoring import Scorer
from monitor.storage import ARCHIVE_DIR, Storage


def migrate(storage: Storage | None = None, cfg: Dict[str, Any] | None = None) -> Dict[str, Any]:
    cfg = cfg or load_main_config()
    storage = storage or Storage()
    mon = cfg.get("monitor") or {}
    min_final = float(mon.get("min_final_score") or 6.0)

    scorer = Scorer(
        keywords_cfg=load_keywords_config(),
        noise_cfg=load_noise_config(),
        monitor_cfg=mon,
        black_users=cfg.get("black_users") or [],
    )

    src = storage.records_file
    if not src.exists():
        print("[migrate] no records.json")
        return {"kept": 0, "noise": 0, "total": 0}

    # archive original
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_path = storage.archive_file(src, f"pre-v5-{ts}")
    print(f"[migrate] archived original -> {archive_path}")

    old = storage.load_records()
    items = old.get("items") or []
    print(f"[migrate] scoring {len(items)} historical items (min_final={min_final})")

    kept = []
    noise = []
    conf_counter = Counter()
    type_counter = Counter()

    for raw in items:
        rec = Record.from_dict(raw)
        # historical items may lack is_fork/topics
        result = scorer.apply(rec)
        type_counter[rec.monitor_type] += 1
        conf_counter[rec.confidence] += 1
        payload = rec.to_dict()
        if result.drop or rec.final_score < min_final or rec.confidence == "noise":
            payload["archived"] = True
            noise.append(payload)
        else:
            payload["archived"] = False
            kept.append(payload)

    kept.sort(key=lambda x: float(x.get("final_score") or 0), reverse=True)
    noise.sort(key=lambda x: float(x.get("final_score") or 0), reverse=True)

    # backfill Chinese descriptions for kept items when enabled
    mon = cfg.get("monitor") or {}
    translated = 0
    if mon.get("translate", True):
        from monitor.translate import enrich_dict_items_cn

        sleep_ms = float(mon.get("translate_sleep_ms") or 50)
        print(f"[migrate] translating missing description_cn (sleep={sleep_ms}ms)...")
        translated = enrich_dict_items_cn(
            kept,
            enabled=True,
            sleep_s=max(sleep_ms, 0) / 1000.0,
            only_missing=True,
        )
        print(f"[migrate] translated {translated} descriptions")

    # write clean records
    doc = empty_records_doc()
    doc["items"] = kept
    doc["meta"] = {
        "min_final_score": min_final,
        "scored": True,
        "migrated_at": datetime.now().isoformat(timespec="seconds"),
        "source_total": len(items),
        "noise_total": len(noise),
        "translated": translated,
    }
    storage.save_records(doc)

    # write noise archive json
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    noise_path = ARCHIVE_DIR / f"noise-{ts}.json"
    from monitor.config import write_json

    write_json(
        noise_path,
        {
            "version": 5,
            "items": noise,
            "total": len(noise),
            "migrated_at": datetime.now().isoformat(timespec="seconds"),
            "by_confidence": dict(conf_counter),
        },
    )

    storage.publish_to_docs()

    stats = {
        "total": len(items),
        "kept": len(kept),
        "noise": len(noise),
        "keep_rate": round(len(kept) / len(items), 4) if items else 0,
        "by_confidence": dict(conf_counter),
        "by_type_source": dict(type_counter),
        "archive": str(archive_path),
        "noise_file": str(noise_path),
    }
    print("[migrate] done:", stats)
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    stats = migrate()
    if args.publish:
        Storage().publish_to_docs()
    print(stats)


if __name__ == "__main__":
    main()
