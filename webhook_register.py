"""
Airtable Webhook 注册 + 续期脚本
用法：
  python webhook_register.py setup    # 首次注册，写入 .env
  python webhook_register.py refresh  # 检查并续期（每 6 天运行一次）
  python webhook_register.py list     # 查看已注册列表
"""

import os
import re
import sys
import subprocess
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

PAT       = os.getenv("AIRTABLE_PAT")
BASE_ID   = os.getenv("AIRTABLE_BASE_ID")
TBL_PIPE  = os.getenv("AIRTABLE_PIPELINE_TABLE")
MODAL_URL = os.getenv("MODAL_WEBHOOK_URL", "")
ENV_FILE  = os.path.join(os.path.dirname(__file__), ".env")

HEADERS = {
    "Authorization": f"Bearer {PAT}",
    "Content-Type": "application/json",
}
WEBHOOKS_URL = f"https://api.airtable.com/v0/bases/{BASE_ID}/webhooks"


def _write_env(key: str, value: str):
    """直接替换 .env 里对应行，不加引号。"""
    with open(ENV_FILE, "r") as f:
        content = f.read()
    pattern = rf"^{re.escape(key)}=.*$"
    new_line = f"{key}={value}"
    if re.search(pattern, content, flags=re.MULTILINE):
        content = re.sub(pattern, new_line, content, flags=re.MULTILINE)
    else:
        content = content.rstrip("\n") + f"\n{new_line}\n"
    with open(ENV_FILE, "w") as f:
        f.write(content)


def _sync_modal_secret():
    """把完整 .env 同步到 Modal Secret。
    `modal secret create --force` 是整份覆盖，只传个别 key 会把其余 key 全部清掉，所以必须整份同步。"""
    cmd = [
        "modal", "secret", "create", "oral-video-director-secrets",
        "--from-dotenv", ENV_FILE,
        "--force",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print("   ✅ Modal Secret 已同步更新")
    else:
        print(f"   ⚠️  Modal Secret 更新失败：{result.stderr.strip()}")


def list_webhooks() -> list[dict]:
    resp = requests.get(WEBHOOKS_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json().get("webhooks", [])


def register_webhook(notification_url: str) -> dict:
    payload = {
        "notificationUrl": notification_url,
        "specification": {
            "options": {
                "filters": {
                    "dataTypes": ["tableData"],
                    "recordChangeScope": TBL_PIPE,
                }
            }
        },
    }
    resp = requests.post(WEBHOOKS_URL, headers=HEADERS, json=payload, timeout=15)
    if not resp.ok:
        print(f"Error: {resp.text}")
        resp.raise_for_status()
    return resp.json()


def delete_webhook(webhook_id: str):
    resp = requests.delete(f"{WEBHOOKS_URL}/{webhook_id}", headers=HEADERS, timeout=15)
    resp.raise_for_status()


def setup():
    """首次部署：删除旧 Webhook，注册新的，写入 .env + Modal Secret。"""
    if not MODAL_URL:
        print("❌ 未设置 MODAL_WEBHOOK_URL。请先 `modal deploy modal_app.py` 拿到你自己的 URL，")
        print("   填入 .env 的 MODAL_WEBHOOK_URL 后再运行本命令。")
        sys.exit(1)
    existing = list_webhooks()
    for wh in existing:
        print(f"🗑️  删除旧 Webhook {wh['id']}")
        try:
            delete_webhook(wh["id"])
        except Exception as e:
            print(f"   ⚠️ 删除失败：{e}")

    print(f"🔗 注册 Webhook → {MODAL_URL}")
    result = register_webhook(MODAL_URL)
    webhook_id = result["id"]
    webhook_secret = result.get("macSecretBase64", "")
    expiry = result.get("expirationTime", "")

    _write_env("AIRTABLE_WEBHOOK_ID", webhook_id)
    _write_env("AIRTABLE_WEBHOOK_SECRET", webhook_secret)
    _sync_modal_secret()

    print(f"✅ Webhook 注册成功")
    print(f"   ID：{webhook_id}")
    print(f"   到期：{expiry}")
    print(f"   .env + Modal Secret 均已更新")


def refresh():
    """检查到期时间，剩余不足 2 天时用官方 refresh 接口续期（不换 ID/secret）。"""
    existing = list_webhooks()
    now = datetime.now(timezone.utc)
    threshold = now + timedelta(days=2)

    for wh in existing:
        expiry_str = wh.get("expirationTime", "")
        if not expiry_str:
            continue
        expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
        if expiry <= threshold:
            print(f"🔄 续期 Webhook {wh['id']}（到期：{expiry_str}）")
            try:
                resp = requests.post(f"{WEBHOOKS_URL}/{wh['id']}/refresh", headers=HEADERS, timeout=15)
                resp.raise_for_status()
                print(f"   ✅ 续期成功，新到期：{resp.json().get('expirationTime')}")
            except Exception as e:
                print(f"   ❌ 续期失败：{e}")
        else:
            remaining = expiry - now
            print(f"✅ Webhook {wh['id']} 有效（剩余 {remaining.days} 天）")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "setup":
        setup()
    elif cmd == "refresh":
        refresh()
    elif cmd == "list":
        webhooks = list_webhooks()
        print(f"共 {len(webhooks)} 个 Webhook：")
        for wh in webhooks:
            print(f"  ID:  {wh['id']}")
            print(f"  URL: {wh.get('notificationUrl', '（无）')}")
            print(f"  到期：{wh.get('expirationTime', 'N/A')}")
    else:
        print(f"未知命令：{cmd}")
        sys.exit(1)
