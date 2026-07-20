"""Shared paths and YAML/JSON helpers for V5."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
ARCHIVE_DIR = DATA_DIR / "archive"
DOCS_DATA_DIR = ROOT / "docs" / "data"

# Prefer V5 config layout; fall back to legacy root config.yaml
MAIN_CONFIG = CONFIG_DIR / "config.yaml"
LEGACY_CONFIG = ROOT / "config.yaml"
KEYWORDS_CONFIG = CONFIG_DIR / "keywords.yaml"
NOISE_CONFIG = CONFIG_DIR / "noise_rules.yaml"
SKILLS_CONFIG = CONFIG_DIR / "skills.yaml"


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def load_main_config() -> Dict[str, Any]:
    if MAIN_CONFIG.exists():
        cfg = _read_yaml(MAIN_CONFIG)
    elif LEGACY_CONFIG.exists():
        # best-effort legacy adapter
        raw = _read_yaml(LEGACY_CONFIG)
        old = raw.get("all_config", raw)
        cfg = {
            "version": 4,
            "github": {"token": old.get("github_token", "")},
            "monitor": {
                "min_final_score": float(old.get("score_threshold", 5)),
                "min_stars": 0,
                "allow_zero_stars_if_precise": True,
                "skip_forks": True,
                "skip_no_info": True,
                "max_per_author_per_run": old.get("monitor", {}).get("max_push_per_user", 3),
                "keyword_window_days": 3,
                "cve_window_days": 2,
                "user_window_days": 2,
                "translate": bool(old.get("translate", {}).get("enable", 0)),
            },
            "notify": {
                "dingding": {
                    "enable": bool(old.get("dingding", {}).get("enable", 0)),
                    "webhook": old.get("dingding", {}).get("webhook", ""),
                    "secret": old.get("dingding", {}).get("secretKey", ""),
                },
                "feishu": {
                    "enable": bool(old.get("feishu", {}).get("enable", 0)),
                    "webhook": old.get("feishu", {}).get("webhook", ""),
                },
                "min_notify_score": 7.0,
            },
            "black_users": old.get("black_user", []),
            "users": old.get("user_list", []),
            "tools": [],
            "trending": {"min_stars": 30, "top_n": 30, "queries": []},
            "skills": {"enable": False},
        }
        for url in old.get("tools_list", []):
            # https://api.github.com/repos/owner/repo
            parts = url.rstrip("/").split("/")
            if len(parts) >= 2:
                cfg["tools"].append({"owner": parts[-2], "repo": parts[-1]})
    else:
        cfg = {}

    # env overrides
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or cfg.get("github", {}).get("token", "")
    cfg.setdefault("github", {})["token"] = token

    ding = cfg.setdefault("notify", {}).setdefault("dingding", {})
    ding["webhook"] = os.getenv("DINGDING_WEBHOOK", ding.get("webhook", ""))
    ding["secret"] = os.getenv("DINGDING_SECRET", ding.get("secret", ""))
    if os.getenv("DINGDING_WEBHOOK"):
        ding["enable"] = True

    feishu = cfg.setdefault("notify", {}).setdefault("feishu", {})
    feishu["webhook"] = os.getenv("FEISHU_WEBHOOK", feishu.get("webhook", ""))
    if os.getenv("FEISHU_WEBHOOK"):
        feishu["enable"] = True

    return cfg


def load_keywords_config() -> Dict[str, Any]:
    return _read_yaml(KEYWORDS_CONFIG)


def load_noise_config() -> Dict[str, Any]:
    return _read_yaml(NOISE_CONFIG)


def load_skills_config() -> Dict[str, Any]:
    return _read_yaml(SKILLS_CONFIG)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
