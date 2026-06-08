""""GitHub Security Monitor - 配置加载"""
import yaml
import os
import json
import requests
from pathlib import Path
from typing import List, Dict, Any

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
DATA_DIR = Path(__file__).parent.parent / "data"


def load_config() -> Dict:
    """加载 YAML 配置，支持环境变量覆盖"""
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)['all_config']

    # 环境变量覆盖
    cfg['github_token'] = os.getenv('GITHUB_TOKEN', cfg.get('github_token', ''))
    cfg['dingding']['webhook'] = os.getenv('DINGDING_WEBHOOK', cfg['dingding'].get('webhook', ''))
    cfg['dingding']['secretKey'] = os.getenv('DINGDING_SECRET', cfg['dingding'].get('secretKey', ''))
    cfg['feishu'] = cfg.get('feishu', {})
    cfg['feishu']['webhook'] = os.getenv('FEISHU_WEBHOOK', cfg['feishu'].get('webhook', ''))

    return cfg


def load_records() -> Dict:
    """加载已有监控记录"""
    records_file = DATA_DIR / "records.json"
    if records_file.exists():
        import json
        with open(records_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"items": [], "total": 0, "last_updated": "", "by_type": {}}


def save_records(data: Dict):
    """保存监控记录"""
    import json
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_DIR / "records.json", 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_executions() -> List[Dict]:
    """加载执行历史"""
    exec_file = DATA_DIR / "executions.json"
    if exec_file.exists():
        import json
        with open(exec_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


def save_executions(data: List[Dict]):
    """保存执行历史"""
    import json
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_DIR / "executions.json", 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_trending() -> Dict:
    """加载热门飙升"""
    trending_file = DATA_DIR / "trending.json"
    if trending_file.exists():
        import json
        with open(trending_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"items": [], "updated_at": "", "total": 0}


def save_trending(data: Dict):
    """保存热门飙升"""
    import json
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_DIR / "trending.json", 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_duplicate(records: Dict, repo_url: str) -> bool:
    """检查 URL 是否已存在"""
    return any(item.get('repo_url') == repo_url for item in records.get('items', []))


def translate_en_to_zh(text: str) -> str:
    """使用 Google Translate API 将英文翻译为中文（免费）"""
    if not text or not text.strip():
        return text
    # 如果已经是中文为主，跳过
    chinese_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    if chinese_count > len(text) * 0.3:
        return text
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": text[:1500]}
        resp = requests.get(url, params=params, timeout=5)
        if resp.status_code == 200:
            result = resp.json()
            translated = ''.join([s[0] for s in result[0] if s[0]])
            return translated if translated else text
        return text
    except Exception:
        return text
