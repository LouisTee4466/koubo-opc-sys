"""
发布模块
Blotato 多平台发布 + 数据回收
"""

import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

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


@retry()
def _blotato_get(path: str, params: dict | None = None) -> dict:
    resp = requests.get(
        f"{BLOTATO_BASE}{path}",
        headers=BLOTATO_HEADERS,
        params=params,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError(f"响应不是 JSON object：{type(data).__name__}")
    return data


def _items(data: dict) -> list:
    items = data.get("items")
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


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

_PAGE = 100
_MAX_ITEMS = 300


def _url_key(url) -> tuple:
    # publicUrl 与 postUrl 可能差在 ?utm/igsh、#fragment 或 host 大小写，只比 scheme+host+path
    if not isinstance(url, str) or not url:
        return ()
    u = urlsplit(url.strip())
    return (u.scheme.lower(), u.netloc.lower(), u.path.rstrip("/"))


def _same_url(a, b) -> bool:
    ka = _url_key(a)
    return bool(ka) and ka == _url_key(b)


def _find_published_id(platform: str, public_url: str) -> str:
    """publicUrl → Blotato published-post id（analytics 只认这个 id，不认 submissionId）"""
    try:
        for offset in range(0, _MAX_ITEMS, _PAGE):
            data = _blotato_get("/published-posts", {
                "platform": platform, "sortBy": "newest", "offset": offset, "limit": _PAGE,
            })
            items = _items(data)
            for item in items:
                if _same_url(item.get("postUrl"), public_url):
                    return str(item.get("id", ""))
            if len(items) < _PAGE:
                break
    except Exception as e:
        print(f"⚠️  [{platform}] published-posts 查询失败：{e}")

    # 回退到 posts 列表；其默认窗口只看前后 7 天，72h 回收要把 since 拉到 10 天前
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cursor = None
        for _ in range(_MAX_ITEMS // _PAGE):
            # 数组参数是重复 key（status=a&status=b），requests 对 list 值正好这样编码
            params = {"status": ["published"], "platform": [platform], "since": since, "limit": _PAGE}
            if cursor:
                params["cursor"] = cursor
            data = _blotato_get("/posts", params)
            for item in _items(data):
                state = item.get("state")
                if isinstance(state, dict) and _same_url(state.get("postUrl"), public_url):
                    return str(item.get("id", ""))
            cursor = data.get("cursor")
            if not cursor:
                break
    except Exception as e:
        print(f"⚠️  [{platform}] posts 列表查询失败：{e}")

    return ""


def _to_int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _map_metrics(platform: str, metrics: dict) -> dict:
    like = metrics.get("likesCount")
    if like is None and platform == "facebook":
        # FB 没有 likesCount 时只给按类型拆分的 reactions
        like = sum(_to_int(v) for v in (metrics.get("facebookPostReactionsByType") or {}).values())
    play = metrics.get("viewsCount")
    if play is None:
        play = metrics.get("reachCount")
    return {
        "play": _to_int(play),
        "like": _to_int(like),
        "comment": _to_int(metrics.get("commentsCount")),
        "share": _to_int(metrics.get("sharesCount")),
    }


def fetch_platform_stats(platform: str, submission_id: str) -> dict:
    """
    Blotato 数据回收：submission → publicUrl → published-post id → analytics。
    永不抛异常，失败返回全 0（modal_app 依赖 {play, like, comment, share, post_url}）。
    """
    zero = {"play": 0, "like": 0, "comment": 0, "share": 0, "post_url": ""}
    try:
        post = _blotato_get(f"/posts/{submission_id}")
    except Exception as e:
        print(f"⚠️  [{platform}] 查询 submission {submission_id} 失败：{e}")
        return zero

    public_url = post.get("publicUrl")
    if not isinstance(public_url, str) or not public_url:
        print(f"⚠️  [{platform}] submission {submission_id} 尚无 publicUrl"
              f"（status={post.get('status')} {post.get('errorMessage') or ''}）")
        return zero
    zero["post_url"] = public_url

    published_id = _find_published_id(platform, public_url)
    if not published_id:
        print(f"⚠️  [{platform}] 在 published-posts / posts 里找不到 {public_url}，无法拉 analytics")
        return zero

    try:
        data = _blotato_get(f"/posts/{published_id}/analytics")
    except Exception as e:
        print(f"⚠️  [{platform}] analytics 拉取失败 (id={published_id})：{e}")
        return zero

    metrics = data.get("metrics")
    if not isinstance(metrics, dict):
        print(f"⚠️  [{platform}] analytics 响应缺少 metrics (id={published_id})")
        return zero
    return {**_map_metrics(platform, metrics), "post_url": public_url}
