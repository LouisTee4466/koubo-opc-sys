"""
告警通知模块（叶子工具，仅依赖 os + requests）

设置环境变量 ALERT_WEBHOOK_URL（Slack 或 Discord 的 Incoming Webhook）即可启用推送。
未设置时自动跳过，只打印日志，绝不影响主流程。
"""

import os
import requests

_ICONS = {"error": "🔴", "warn": "🟠", "info": "🟢", "money": "💰"}


def notify(message: str, level: str = "info") -> None:
    """推送一条告警。任何异常都被吞掉，保证不影响主流程。"""
    text = f"{_ICONS.get(level, '🔔')} [Oral Video Director] {message}"
    print(f"[notify] {text}")

    url = os.getenv("ALERT_WEBHOOK_URL", "").strip()
    if not url:
        return
    try:
        # 同时带 text / content 两个键，兼容 Slack（text）与 Discord（content）
        requests.post(url, json={"text": text, "content": text}, timeout=10)
    except Exception as e:
        print(f"[notify] 推送失败（已忽略）：{e}")
