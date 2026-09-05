"""Notification helpers (DingTalk / Feishu)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Sequence

import requests

# Notification dedup state lives next to the other data files. Repo layout:
# monitor/notify.py -> parent.parent = project root
STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "notification_state.json"


def _load_seen() -> Dict[str, str]:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_seen(seen: Dict[str, str]) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"  notification state save failed: {e}")


def _fingerprint(item: Dict[str, Any]) -> str:
    """Dedupe key: same repo + same monitor type + same day = same notification."""
    raw = (
        f"{item.get('repo_url') or ''}|{item.get('monitor_type') or ''}"
        f"|{(item.get('discovered_at') or '')[:10]}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def filter_new_notified(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return items not already notified; mark the rest as seen (persisted)."""
    seen = _load_seen()
    fresh: List[Dict[str, Any]] = []
    changed = False
    for item in items:
        fp = _fingerprint(item)
        if fp in seen:
            continue
        seen[fp] = (item.get("discovered_at") or "")[:10]
        changed = True
        fresh.append(item)
    # keep the state file from growing forever: drop entries older than 30 days
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - 30 * 86400))
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    if changed:
        _save_seen(seen)
    return fresh


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
    # dedupe: re-runs of the same window must not re-notify the same repos
    fresh = filter_new_notified(filtered)
    skipped = len(filtered) - len(fresh)
    if skipped:
        print(f"  [notify] skipped {skipped} already-notified items")
    if not fresh:
        return
    lines = [f"{prefix} {len(fresh)} 个高分项目（展示 Top {min(len(fresh), max_items)}）:\n"]
    for item in fresh[:max_items]:
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


def notify_alert(cfg: Dict[str, Any], subject: str, detail: str = "") -> None:
    """Ops alert: token invalid, quota exhausted, circuit breaker tripped.

    Sent unconditionally (not gated by dedupe or score) — these need eyes.
    """
    notify = cfg.get("notify") or {}
    alert = notify.get("alerts") or {}
    if alert.get("enable") is False:
        return
    text = f"{subject}"
    if detail:
        text += f"\n\n{detail}"
    title = f"[GitHub安全监控] ⚠️ {subject}"
    ding = notify.get("dingding") or {}
    if ding.get("enable"):
        send_dingding(ding.get("webhook", ""), ding.get("secret", ""), title, text)
    feishu = notify.get("feishu") or {}
    if feishu.get("enable"):
        send_feishu(feishu.get("webhook", ""), title, text)
    print(f"  [notify] ALERT: {subject}")


def alert_from_results(cfg: Dict[str, Any], client: Any, results: Dict[str, Any]) -> None:
    """Inspect run results + client metrics and fire ops alerts when warranted."""
    if not isinstance(client, object) or not hasattr(client, "metrics"):
        return
    m = client.metrics()
    alerts: List[str] = []
    if m.get("circuit_open"):
        alerts.append(f"熔断触发：本轮 {m['circuit_open']} 次请求被拒绝（连续限流或网络错误达到阈值）")
    if m.get("rate_limited", 0) >= 3:
        alerts.append(f"GitHub 限流 {m['rate_limited']} 次（search/core 配额吃紧，考虑配置 GH_TOKEN 或降低频率）")
    if m.get("budget_exhausted"):
        alerts.append(f"请求预算耗尽 {m['budget_exhausted']} 次，部分来源被跳过")
    for name, v in results.items():
        if isinstance(v, dict) and int(v.get("kept") or 0) < 0:
            err = str(v.get("error") or "")[:120]
            alerts.append(f"来源 {name} 失败: {err}")
    if alerts:
        notify_alert(cfg, "监控运行异常", "\n".join(f"- {a}" for a in alerts))
