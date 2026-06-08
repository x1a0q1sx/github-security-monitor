"""热门飙升监控 — 每周抓取安全相关的高 Star 项目"""
import datetime
from typing import List, Dict

from monitor.config import load_config, load_trending, save_trending

cfg = load_config()

SECURITY_QUERIES = [
    "redteam+stars:>50",
    "penetration+testing+stars:>50",
    "cobaltstrike+stars:>50",
    "vulnerability+exploit+stars:>50",
    "CVE+exploit+stars:>50",
    "bug+bounty+stars:>50",
    "offensive+security+stars:>50",
    "malware+analysis+stars:>50",
    "reverse+engineering+stars:>30",
    "threat+hunting+stars:>30",
]


def github_api_request(url: str, timeout: int = 15):
    import requests
    headers = {}
    if cfg.get('github_token'):
        headers['Authorization'] = f"token {cfg['github_token']}"
    resp = requests.get(url, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        return None
    return resp


def run_trending() -> List[Dict]:
    print("[TRENDING] 开始抓取热门安全项目...")
    all_items = []
    seen = set()

    for query in SECURITY_QUERIES:
        api = f"https://api.github.com/search/repositories?q={query}&sort=stars&order=desc&per_page=10"
        print(f"  查询: {query}")

        resp = github_api_request(api)
        if not resp:
            continue

        for repo in resp.json().get('items', []):
            url = repo['html_url']
            if url in seen:
                continue
            seen.add(url)

            stars = repo.get('stargazers_count', 0)
            if stars < 30:
                continue

            all_items.append({
                'repo_name': repo.get('full_name', repo.get('name', '')),
                'repo_url': url,
                'repo_description': repo.get('description', '') or '',
                'stars': stars,
                'forks': repo.get('forks_count', 0),
                'language': repo.get('language', ''),
                'topics': repo.get('topics', []),
                'created_at': repo.get('created_at', ''),
                'updated_at': repo.get('updated_at', ''),
                'query': query
            })

        import time
        time.sleep(0.5)

    # 按 Star 排序取前 30
    all_items.sort(key=lambda x: x['stars'], reverse=True)
    top30 = all_items[:30]

    trending_data = {
        'items': top30,
        'updated_at': datetime.datetime.now().isoformat(),
        'total': len(top30)
    }
    save_trending(trending_data)

    print(f"[TRENDING] 总共抓取 {len(all_items)} 个, 保存 Top {len(top30)} 个")
    return top30
