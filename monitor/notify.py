"""Notification helpers (DingTalk / Feishu)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
import urllib.parse
from typing import Any, Dict, List, Sequence

import requests


def _ding_sign(secret: str) -> tuple[str, str]:
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return timestamp, sign


def send_dingding(webhook: str, secret: str, title: str, content: str) -> bool:
    if not webhook:
        return False
    url = webhook
    if secret:
        ts, sign = _ding_sign(secret)
        sep = "&" if "?" in webhook else "?"
        url = f"{webhook}{sep}timestamp={ts}&sign={sign}"
    text = content if len(content) <= 5000 else content[:5000] + "\n\n...truncated"
    payload = {"msgtype": "markdown", "markdown": {"title": title, "text": f"### {title}\n\n{text}"}}
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"  dingding failed: {e}")
        return False


def send_feishu(webhook: str, title: str, content: str) -> bool:
    if not webhook:
        return False
    text = f"{title}\n\n{content}"
    if len(text) > 3000:
        text = text[:3000] + "\n..."
    payload = {"msg_type": "text", "content": {"text": text}}
    try:
        r = requests.post(webhook, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"  feishu failed: {e}")
        return False


def notify_new_records(cfg: Dict[str, Any], items: Sequence[Dict[str, Any]], prefix: str = "新增") -> None:
    notify = cfg.get("notify") or {}
    min_score = float(notify.get("min_notify_score") or 0)
    max_items = int(notify.get("max_items_per_message") or 20)
    filtered = [i for i in items if float(i.get("final_score") or 0) >= min_score]
    if not filtered:
        return
    lines = [f"{prefix} {len(filtered)} 个高分项目（展示 Top {min(len(filtered), max_items)}）:\n"]
    for item in filtered[:max_items]:
        score = item.get("final_score", 0)
        stars = item.get("stars", 0)
        conf = item.get("confidence", "")
        lines.append(
            f"- [{item.get('repo_name')}]({item.get('repo_url')}) "
            f"⭐{stars} score={score} ({conf})"
        )
    text = "\n".join(lines)
    title = f"[GitHub安全监控] {prefix}"
    ding = notify.get("dingding") or {}
    if ding.get("enable"):
        send_dingding(ding.get("webhook", ""), ding.get("secret", ""), title, text)
    feishu = notify.get("feishu") or {}
    if feishu.get("enable"):
        send_feishu(feishu.get("webhook", ""), title, text)


def notify_skills(cfg: Dict[str, Any], skills: Sequence[Dict[str, Any]]) -> None:
    if not skills:
        return
    notify = cfg.get("notify") or {}
    top = skills[:10]
    lines = ["Skill 发现推荐 Top:\n"]
    for s in top:
        final = (s.get("scores") or {}).get("final", 0)
        lines.append(f"- {s.get('display_name') or s.get('name')} ({s.get('source')}) score={final}")
        if s.get("install"):
            lines.append(f"  `{s.get('install')}`")
    text = "\n".join(lines)
    title = "[GitHub安全监控] Skills"
    ding = notify.get("dingding") or {}
    if ding.get("enable"):
        send_dingding(ding.get("webhook", ""), ding.get("secret", ""), title, text)


def notify_summary(cfg: Dict[str, Any], results: Dict[str, Any]) -> None:
    total = 0
    lines = ["本次执行汇总:\n"]
    for k, v in results.items():
        if isinstance(v, dict):
            kept = v.get("kept", v.get("updated", 0))
        else:
            kept = v
        try:
            kept_n = int(kept)
        except Exception:
            kept_n = 0
        if kept_n < 0:
            lines.append(f"- {k}: ERROR")
        else:
            lines.append(f"- {k}: {kept_n}")
            total += max(kept_n, 0)
    lines.append(f"\n合计新项目: {total}")
    if total <= 0:
        return
    title = "[GitHub安全监控] 执行汇总"
    text = "\n".join(lines)
    notify = cfg.get("notify") or {}
    ding = notify.get("dingding") or {}
    if ding.get("enable"):
        send_dingding(ding.get("webhook", ""), ding.get("secret", ""), title, text)
    feishu = notify.get("feishu") or {}
    if feishu.get("enable"):
        send_feishu(feishu.get("webhook", ""), title, text)
