"""工具更新监控 — 检查红队工具是否有更新"""
import datetime
from typing import List, Dict

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


def run_tool() -> List[Dict]:
    tools = cfg.get('tools_list', [])
    if not tools:
        print("[TOOL] 未配置监控工具，跳过")
        return []

    records = load_records()
    found = []

    for url in tools:
        print(f"[TOOL] 检查: {url}")
        resp = github_api_request(url)
        if not resp:
            continue

        repo = resp.json()
        name = repo.get('full_name', repo.get('name', 'Unknown'))
        html_url = repo.get('html_url', '')
        desc = repo.get('description', '') or ''

        if is_duplicate(records, html_url):
            continue

        tags = _gen_tags(name, desc)
        found.append({
            'repo_name': name,
            'repo_url': html_url,
            'repo_description': desc,
            'monitor_type': 'tool',
            'monitor_keyword': '红队工具',
            'tags': tags,
            'author': repo.get('owner', {}).get('login', ''),
            'stars': repo.get('stargazers_count', 0),
            'language': repo.get('language', ''),
            'created_at': repo.get('created_at', ''),
            'pushed_at': repo.get('pushed_at', ''),
                'description_cn': translate_en_to_zh(desc),
            'discovered_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })

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

    print(f"[TOOL] 检查 {len(tools)} 工具, 新增 {len(found)} 个")
    return found


def _gen_tags(name: str, desc: str) -> str:
    text = f"{name} {desc}".lower()
    tags = ['工具更新']
    for tag, kws in {
        '免杀': ['bypass', 'evasion'],
        'C2': ['c2', 'command', 'beacon'],
        '漏洞利用': ['exploit'],
        'Webshell': ['webshell'],
        '后渗透': ['post exploitation', 'lateral'],
    }.items():
        for kw in kws:
            if kw in text and tag not in tags:
                tags.append(tag)
                break
    return ','.join(tags)
