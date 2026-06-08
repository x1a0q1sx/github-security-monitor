"""用户仓库监控 — 检查关注用户的新仓库"""
import re
import datetime
from typing import List, Dict
from datetime import timedelta

from monitor.config import load_config, load_records, save_records, is_duplicate

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


def run_user() -> List[Dict]:
    users = cfg.get('user_list', [])
    if not users:
        print("[USER] 未配置监控用户，跳过")
        return []

    records = load_records()
    today = datetime.date.today()
    yesterday = today - timedelta(days=1)
    all_found = []

    for user in users:
        api = f"https://api.github.com/users/{user}/repos?sort=created&per_page=10"
        print(f"[USER] 检查: {user}")

        resp = github_api_request(api)
        if not resp:
            continue

        for repo in resp.json():
            if repo.get('fork', False):
                continue

            created_str = repo.get('created_at', '')
            created_dates = re.findall(r'\d{4}-\d{2}-\d{2}', created_str)
            if not created_dates:
                continue
            created_date = created_dates[0]

            if created_date != str(today) and created_date != str(yesterday):
                continue

            url = repo['html_url']
            if is_duplicate(records, url):
                continue

            name = repo['name']
            desc = repo.get('description', '') or ''
            tags = _gen_tags(name, desc)

            all_found.append({
                'repo_name': name,
                'repo_url': url,
                'repo_description': desc,
                'monitor_type': 'user_repo',
                'monitor_keyword': f'用户:{user}',
                'tags': tags,
                'author': user,
                'stars': repo.get('stargazers_count', 0),
                'language': repo.get('language', ''),
                'created_at': repo.get('created_at', ''),
                'pushed_at': repo.get('pushed_at', ''),
                'discovered_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })

        import time
        time.sleep(0.5)

    if all_found:
        records['items'].extend(all_found)
        records['total'] = len(records['items'])
        records['last_updated'] = datetime.datetime.now().isoformat()
        by_type = records.get('by_type', {})
        for r in all_found:
            t = r['monitor_type']
            by_type[t] = by_type.get(t, 0) + 1
        records['by_type'] = by_type
        save_records(records)

    print(f"[USER] 检查 {len(users)} 用户, 新增 {len(all_found)} 个")
    return all_found


def _gen_tags(name: str, desc: str) -> str:
    text = f"{name} {desc}".lower()
    tags = ['大佬新作']
    for tag, kws in {
        '漏洞利用': ['exploit', 'vulnerability', 'poc'],
        '免杀': ['bypass', 'evasion'],
        '红队': ['redteam', 'c2', 'beacon'],
        '提权': ['privilege', 'escalation'],
        'Web安全': ['webshell', 'sql', 'xss'],
    }.items():
        for kw in kws:
            if kw in text and tag not in tags:
                tags.append(tag)
                break
    return ','.join(tags)
