"""CVE 监控模块 — 抓取最近 N 小时更新的 CVE 项目"""
import re
import datetime
from typing import List, Dict
from datetime import timedelta

from monitor.config import load_config, load_records, save_records, is_duplicate, translate_en_to_zh

cfg = load_config()


def github_api_request(url: str, timeout: int = 15):
    """GitHub API 请求"""
    import requests
    headers = {}
    if cfg.get('github_token'):
        headers['Authorization'] = f"token {cfg['github_token']}"
    resp = requests.get(url, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        print(f"  API error {resp.status_code}: {resp.text[:200]}")
        return None
    return resp


def run_cve(since_hours: int = 7) -> List[Dict]:
    """获取最近 N 小时更新的 CVE 项目"""
    year = datetime.datetime.now().year
    api = f"https://api.github.com/search/repositories?q=CVE-{year}&sort=updated&per_page=50"
    print(f"[CVE] 搜索: CVE-{year}")

    resp = github_api_request(api)
    if not resp:
        return []

    data = resp.json()
    items = data.get('items', [])
    today = datetime.date.today()
    cutoff = datetime.datetime.now() - timedelta(hours=since_hours)
    black_list = cfg.get('black_user', [])
    records = load_records()
    found = []

    for item in items:
        try:
            url = item['html_url']
            author = url.split("/")[-2]
            if author in black_list:
                continue

            raw_name = item['name'].upper()
            cve_match = re.findall(r'(CVE-\d+-\d+)', raw_name)
            if not cve_match:
                continue
            cve_name = cve_match[0].upper()

            pushed = item.get('pushed_at', '')
            pushed_dates = re.findall(r'\d{4}-\d{2}-\d{2}', pushed)
            if not pushed_dates:
                continue
            pushed_date = pushed_dates[0]

            # 时间窗口过滤
            if pushed_date != str(today) and pushed_date != str(today - timedelta(days=1)):
                continue

            if is_duplicate(records, url):
                continue

            desc = item.get('description', '') or ''
            stars = item.get('stargazers_count', 0)
            lang = item.get('language', '')

            tags = _gen_tags(cve_name, desc, 'cve')

            found.append({
                'repo_name': cve_name,
                'repo_url': url,
                'repo_description': desc,
                'monitor_type': 'cve',
                'monitor_keyword': 'CVE',
                'tags': tags,
                'author': author,
                'stars': stars,
                'language': lang,
                'created_at': item.get('created_at', ''),
                'pushed_at': item.get('pushed_at', ''),
                'description_cn': translate_en_to_zh(desc),
                'discovered_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })
        except Exception:
            continue

    # 保存到 records.json
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

    print(f"[CVE] 发现 {len(items)} 个, 新增 {len(found)} 个")
    return found


def _gen_tags(name: str, desc: str, mtype: str) -> str:
    text = f"{name} {desc}".lower()
    tags = []
    for tag, keywords in {
        '漏洞利用': ['exploit', 'vulnerability'],
        'POC': ['poc', 'proof of concept'],
        'Payload': ['payload'],
        '免杀': ['bypass', 'evasion', '免杀'],
        '红队': ['redteam', 'red team'],
        '蓝队': ['blueteam', 'defense'],
    }.items():
        for kw in keywords:
            if kw in text and tag not in tags:
                tags.append(tag)
                break
    if 'CVE' not in tags:
        tags.insert(0, 'CVE')
    return ','.join(tags) if tags else 'CVE'
