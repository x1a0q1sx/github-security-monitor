"""Description translation helper with multi-provider fallback."""
from __future__ import annotations

import html
import re
import time
from typing import Iterable, List
from urllib.parse import quote

import requests

from monitor.models import Record

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
    }
)


def is_mostly_chinese(text: str, ratio: float = 0.25) -> bool:
    if not text or not text.strip():
        return False
    # CJK Unified Ideographs
    chinese = sum(1 for c in text if "一" <= c <= "鿿")
    letters = sum(1 for c in text if c.isalpha() or "一" <= c <= "鿿")
    if letters <= 0:
        return False
    return chinese / letters >= ratio


def _google_gtx(text: str, timeout: float = 8.0) -> str:
    url = "https://translate.googleapis.com/translate_a/single"
    params = {
        "client": "gtx",
        "sl": "en",
        "tl": "zh-CN",
        "dt": "t",
        "q": text[:1500],
    }
    resp = _SESSION.get(url, params=params, timeout=timeout)
    if resp.status_code != 200:
        return ""
    result = resp.json()
    translated = "".join(part[0] for part in (result[0] or []) if part and part[0])
    return (translated or "").strip()


def _google_tk_free(text: str, timeout: float = 8.0) -> str:
    # Alternative public endpoint used by many scrapers
    url = "https://translate.google.com/m"
    params = {"sl": "en", "tl": "zh-CN", "q": text[:1000], "hl": "zh-CN"}
    resp = _SESSION.get(url, params=params, timeout=timeout)
    if resp.status_code != 200:
        return ""
    # mobile page contains result in class result-container
    m = re.search(r'class="result-container">(.*?)</div>', resp.text, re.S)
    if not m:
        m = re.search(r'class="t0">(.*?)</div>', resp.text, re.S)
    if not m:
        return ""
    return html.unescape(re.sub(r"<.*?>", "", m.group(1))).strip()


def _mymemory(text: str, timeout: float = 8.0) -> str:
    url = "https://api.mymemory.translated.net/get"
    params = {"q": text[:500], "langpair": "en|zh-CN"}
    resp = _SESSION.get(url, params=params, timeout=timeout)
    if resp.status_code != 200:
        return ""
    data = resp.json()
    translated = ((data.get("responseData") or {}).get("translatedText") or "").strip()
    # MyMemory may echo original or return quota messages
    if not translated or translated.lower() == text.lower():
        return ""
    if "myMEMORY" in translated or "QUOTA" in translated.upper():
        return ""
    return html.unescape(translated)


def translate_en_to_zh(text: str, timeout: float = 8.0) -> str:
    """Translate English text to zh-CN. Returns original on failure / already Chinese."""
    if not text or not text.strip():
        return text or ""
    if is_mostly_chinese(text):
        return text

    providers = (_google_gtx, _google_tk_free, _mymemory)
    for fn in providers:
        try:
            out = fn(text, timeout=timeout)
            if out and out != text and is_mostly_chinese(out, ratio=0.15):
                return out
            # sometimes short tech terms stay latin-heavy; accept if different and non-empty
            if out and out != text and len(out) >= 2:
                # reject pure ascii identical-ish noise
                if any("一" <= c <= "鿿" for c in out):
                    return out
        except Exception:
            continue
    return text


def enrich_records_cn(
    records: Iterable[Record],
    enabled: bool = True,
    sleep_s: float = 0.08,
    only_missing: bool = True,
) -> int:
    """Fill description_cn on Record objects. Returns number newly translated."""
    if not enabled:
        return 0
    done = 0
    for rec in records:
        desc = (rec.repo_description or "").strip()
        if not desc:
            rec.description_cn = rec.description_cn or ""
            continue
        existing = (rec.description_cn or "").strip()
        if only_missing and existing:
            if is_mostly_chinese(existing) or (existing != desc and any("一" <= c <= "鿿" for c in existing)):
                continue
        if is_mostly_chinese(desc):
            rec.description_cn = desc
            continue
        cn = translate_en_to_zh(desc)
        rec.description_cn = cn
        if cn and cn != desc and any("一" <= c <= "鿿" for c in cn):
            done += 1
            if sleep_s:
                time.sleep(sleep_s)
    return done


def enrich_dict_items_cn(
    items: List[dict],
    enabled: bool = True,
    sleep_s: float = 0.08,
    only_missing: bool = True,
) -> int:
    """In-place fill description_cn on raw record dicts."""
    if not enabled:
        return 0
    done = 0
    for item in items:
        desc = (item.get("repo_description") or "").strip()
        if not desc:
            item["description_cn"] = item.get("description_cn") or ""
            continue
        existing = (item.get("description_cn") or "").strip()
        if only_missing and existing:
            if is_mostly_chinese(existing) or (existing != desc and any("一" <= c <= "鿿" for c in existing)):
                continue
        if is_mostly_chinese(desc):
            item["description_cn"] = desc
            continue
        cn = translate_en_to_zh(desc)
        item["description_cn"] = cn
        if cn and cn != desc and any("一" <= c <= "鿿" for c in cn):
            done += 1
            if sleep_s:
                time.sleep(sleep_s)
    return done
