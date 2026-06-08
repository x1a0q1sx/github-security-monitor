"""通知模块 — 支持钉钉/飞书/Server酱/PushPlus/Telegram"""
import hashlib
import hmac
import base64
import time
import urllib.parse
from typing import Dict

from monitor.config import load_config

cfg = load_config()


def send_dingding(title: str, content: str):
    """钉钉机器人通知"""
    ding = cfg.get('dingding', {})
    if not ding.get('enable') or not ding.get('webhook'):
        return False

    import requests
    timestamp = str(round(time.time() * 1000))
    secret = ding.get('secretKey', '')
    sign = ''
    if secret:
        string_to_sign = f'{timestamp}\n{secret}'
        hmac_code = hmac.new(
            secret.encode('utf-8'), string_to_sign.encode('utf-8'),
            digestmod=hashlib.sha256
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))

    webhook = ding['webhook']
    if sign:
        webhook = f"{webhook}&timestamp={timestamp}&sign={sign}"

    text_content = content.replace('\n', '\n\n')
    if len(text_content) > 5000:
        text_content = text_content[:5000] + '\n\n...内容过长已截断'

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": f"# {title}\n\n{text_content}"
        }
    }
    try:
        r = requests.post(webhook, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"  钉钉通知失败: {e}")
        return False


def send_feishu(title: str, content: str):
    """飞书机器人通知"""
    feishu = cfg.get('feishu', {})
    if not feishu.get('enable') or not feishu.get('webhook'):
        return False

    import requests
    text_content = content.replace('\n', '\n\n')
    if len(text_content) > 3000:
        text_content = text_content[:3000] + '\n\n...内容过长已截断'

    payload = {
        "msg_type": "text",
        "content": {"text": f"{title}\n\n{text_content}"}
    }
    try:
        r = requests.post(feishu['webhook'], json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"  飞书通知失败: {e}")
        return False


def notify_batch(new_items: list, prefix: str = ""):
    """批量发送通知，每个新项目一条"""
    if not new_items:
        return

    msg = f"{prefix} 新增 {len(new_items)} 个安全项目:\n"
    for item in new_items:
        msg += f"\n- [{item['repo_name']}]({item['repo_url']})"
        if item.get('stars'):
            msg += f" ⭐{item['stars']}"
        if item.get('tags'):
            msg += f" [{item['tags']}]"

    title = f"[GitHub安全监控] {prefix}"
    send_dingding(title, msg)
    send_feishu(title, msg)


def notify_summary(results: Dict[str, int]):
    """发送执行汇总通知"""
    total = sum(results.values())
    if total == 0:
        return

    msg = f"本次监控执行汇总:\n"
    for name, count in results.items():
        msg += f"  {name}: {count} 个新项目\n"
    msg += f"  总计: {total} 个"

    title = "[GitHub安全监控] 执行汇总"
    send_dingding(title, msg)
    send_feishu(title, msg)
