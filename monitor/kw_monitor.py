"""关键词评分监控 — 抓取最近 N 小时更新的匹配项目"""
import re
import time
import datetime
from typing import List, Dict
from datetime import timedelta

from monitor.config import load_config, load_records, save_records, is_duplicate, translate_en_to_zh

cfg = load_config()


def github_api_request(url: str, timeout: int = 15):
    import requests
    headers = {}
    if cfg.get('github_token'):
        headers['Authorization'] = f"token {cfg['github_token']}"
    resp = requests.get(url, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        return None
    return resp


def run_keyword(since_hours: int = 7) -> List[Dict]:
    keywords = cfg.get('scored_keywords', [])
    threshold = cfg.get('score_threshold', 5)
    if not keywords:
        print("[KEYWORD] 未配置关键词，跳过")
        return []

    records = load_records()
    processed = set()
    found = []
    author_counter: Dict[str, int] = {}

    for item in keywords:
        kw = item['keyword']
        score = item['score']
        api = f"https://api.github.com/search/repositories?q={kw}&sort=updated&per_page=30"
        print(f"[KEYWORD] 搜索: {kw} (分值:{score})")

        resp = github_api_request(api)
        if not resp:
            time.sleep(0.5)
            continue

        data = resp.json()
        for repo in data.get('items', []):
            url = repo['html_url']
            if url in processed:
                continue
            processed.add(url)

            name = repo.get('name', '')
            author = url.split("/")[-2]

            if author in cfg.get('black_user', []):
                continue

            pushed = repo.get('pushed_at', '')
            pushed_dates = re.findall(r'\d{4}-\d{2}-\d{2}', pushed)
            if not pushed_dates:
                continue
            pushed_date = pushed_dates[0]

            today = datetime.date.today()
            valid = False
            for i in range(3):
                if pushed_date == str(today - timedelta(days=i)):
                    valid = True
                    break
            if not valid:
                continue

            author_counter[author] = author_counter.get(author, 0) + 1
            if author_counter[author] > 3:
                continue

            if is_duplicate(records, url):
                continue

            desc = repo.get('description', '') or ''
            text = f"{name} {desc}".lower()
            total_score = 0
            matched = []
            for ki in keywords:
                kk = ki['keyword']
                ks = ki['score']
                if kk.lower() in text:
                    total_score += ks
                    matched.append(f"{kk}({ks})")

            if total_score < threshold:
                continue

            tags = _gen_keyword_tags(name, desc, kw)
            found.append({
                'repo_name': name,
                'repo_url': url,
                'repo_description': desc,
                'monitor_type': 'keyword',
                'monitor_keyword': ', '.join(matched),
                'tags': tags,
                'author': author,
                'stars': repo.get('stargazers_count', 0),
                'language': repo.get('language', ''),
                'created_at': repo.get('created_at', ''),
                'pushed_at': repo.get('pushed_at', ''),
                'description_cn': translate_en_to_zh(desc),
                'discovered_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })

        time.sleep(0.5)

    if found:
        records['items'].extend(found)
        records['total'] = len(records['items'])
        records['last_updated'] = datetime.datetime.now().isoformat()
        by_type = records.get('by_type', {})
        for r in found:
            t = r['monitor_type']
            by_type[t] = by_type.get(t, 0) + 1
        records['by_type'] = by_type
        save_records(records)

    print(f"[KEYWORD] 搜索 {len(keywords)} 关键词, 新增 {len(found)} 个")
    return found


def _gen_keyword_tags(name: str, desc: str, keyword: str) -> str:
    text = f"{name} {desc}".lower()
    tags = [keyword] if keyword else []
    for tag, kws in {
        '免杀': ['bypass', 'evasion', '免杀'],
        'POC': ['poc', 'proof of concept'],
        '漏洞利用': ['exploit', 'vulnerability'],
        'Web安全': ['webshell', 'sql', 'xss', 'rce'],
        '内网渗透': ['lateral', 'intranet', '内网'],
        '权限提升': ['privilege', 'escalation'],
        '红队': ['redteam', 'c2', 'cobalt'],
    }.items():
        for kw in kws:
            if kw in text and tag not in tags:
                tags.append(tag)
                break
    return ','.join(tags) if tags else '其他'
