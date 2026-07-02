"""
发布模块
Blotato 多平台发布 + 数据回收
"""

import os
import requests
from dotenv import load_dotenv

from core.retry import retry

load_dotenv()

BLOTATO_API_KEY = os.getenv("BLOTATO_API_KEY")
BLOTATO_BASE = "https://backend.blotato.com/v2"
BLOTATO_HEADERS = {
    "blotato-api-key": BLOTATO_API_KEY,
    "Content-Type": "application/json",
}

BLOTATO_IG_ACCOUNT_ID = os.getenv("BLOTATO_IG_ACCOUNT_ID", "")
BLOTATO_FB_ACCOUNT_ID = os.getenv("BLOTATO_FB_ACCOUNT_ID", "")
BLOTATO_FB_PAGE_ID = os.getenv("BLOTATO_FB_PAGE_ID", "")


# ============================================================
# Blotato 发布
# ============================================================

@retry()
def _upload_to_cdn(video_url: str) -> str:
    resp = requests.post(
        f"{BLOTATO_BASE}/media",
        headers=BLOTATO_HEADERS,
        json={"url": video_url},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["url"]


@retry()
def _post_blotato(payload: dict) -> dict:
    resp = requests.post(
        f"{BLOTATO_BASE}/posts",
        headers=BLOTATO_HEADERS,
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()  # 交给 @retry:429/5xx 自动重试,其它立即抛出
    return resp.json()


def _get_post_url(submission_id: str) -> str:
    """发布后查询帖子真实 URL（Blotato 异步，可能为空）"""
    try:
        resp = requests.get(
            f"{BLOTATO_BASE}/posts/{submission_id}",
            headers=BLOTATO_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        # GET /v2/posts/{uuid} 返回 publicUrl（列表接口用 state.postUrl）
        return data.get("publicUrl", "") or data.get("state", {}).get("postUrl", "")
    except Exception:
        return ""


def _get_numeric_post_id(public_url: str) -> str:
    """通过 publicUrl 在帖子列表里找到 Blotato 内部数字 id"""
    if not public_url:
        return ""
    try:
        resp = requests.get(
            f"{BLOTATO_BASE}/posts",
            headers=BLOTATO_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        for item in resp.json().get("items", []):
            item_url = item.get("state", {}).get("postUrl", "")
            if item_url and item_url == public_url:
                return str(item.get("id", ""))
    except Exception:
        pass
    return ""


def publish_all_platforms(
    video_url: str,
    instagram_caption: str,
    facebook_caption: str,
) -> dict:
    """
    发布到 Instagram 和 Facebook。
    返回 {instagram: {success, post_id, post_url}, facebook: {...}}
    post_id = Blotato postSubmissionId（用于数据回收）
    post_url = 真实平台链接
    """
    print("📤 转存视频到 Blotato CDN...")
    cdn_url = _upload_to_cdn(video_url)
    print(f"✅ CDN URL：{cdn_url}")

    results = {}

    tasks = [
        ("instagram", {
            "post": {
                "accountId": BLOTATO_IG_ACCOUNT_ID,
                "content": {"text": instagram_caption, "mediaUrls": [cdn_url], "platform": "instagram"},
                "target": {"targetType": "instagram", "mediaType": "reel", "shareToFeed": True},
            }
        }),
        ("facebook", {
            "post": {
                "accountId": BLOTATO_FB_ACCOUNT_ID,
                "content": {"text": facebook_caption, "mediaUrls": [cdn_url], "platform": "facebook"},
                "target": {"targetType": "facebook", "mediaType": "reel", "pageId": BLOTATO_FB_PAGE_ID},
            }
        }),
    ]

    for platform, payload in tasks:
        print(f"\n🚀 发布到 {platform.upper()}...")
        try:
            result = _post_blotato(payload)
            submission_id = result.get("postSubmissionId", "")
            post_url = _get_post_url(submission_id) if submission_id else ""
            results[platform] = {
                "success": True,
                "post_id": submission_id,
                "post_url": post_url,
            }
            print(f"✅ {platform} 发布成功")
            if post_url:
                print(f"   链接：{post_url}")
        except Exception as e:
            results[platform] = {"success": False, "error": str(e)}
            print(f"❌ {platform} 发布失败：{e}")

    return results


# ============================================================
# Blotato 数据回收
# ============================================================

def fetch_platform_stats(platform: str, submission_id: str) -> dict:
    """
    通过 Blotato /v2/analytics 拉取 IG/FB 视频数据。
    返回 {play, like, comment, share}
    """
    try:
        # 先拿 numeric post id
        resp = requests.get(
            f"{BLOTATO_BASE}/posts/{submission_id}",
            headers=BLOTATO_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        post_data = resp.json()
        # 单条接口返回 publicUrl，列表接口返回 state.postUrl
        post_url = post_data.get("publicUrl", "") or post_data.get("state", {}).get("postUrl", "")
        numeric_id = _get_numeric_post_id(post_url)

        if not numeric_id:
            return {"play": 0, "like": 0, "comment": 0, "share": 0, "post_url": post_url}

        # 拉 analytics
        r = requests.get(
            f"{BLOTATO_BASE}/analytics",
            headers=BLOTATO_HEADERS,
            params={"postId": numeric_id},
            timeout=15,
        )
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            return {"play": 0, "like": 0, "comment": 0, "share": 0, "post_url": post_url}

        data = items[0]
        return {
            "play": data.get("views", data.get("plays", data.get("impressions", 0))),
            "like": data.get("likes", data.get("reactions", 0)),
            "comment": data.get("comments", 0),
            "share": data.get("shares", data.get("reposts", 0)),
            "post_url": post_url,
        }
    except Exception as e:
        print(f"⚠️  Blotato 数据获取失败 ({platform})：{e}")
        return {"play": 0, "like": 0, "comment": 0, "share": 0, "post_url": ""}
