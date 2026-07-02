"""
HeyGen 视频生成模块
提交异步生成任务，返回 task_id 供 Modal 延迟轮询
"""

import os
import requests
from dotenv import load_dotenv

from core.retry import retry

load_dotenv()

HEYGEN_API_KEY = os.getenv("HEYGEN_API_KEY")
BASE_URL = "https://api.heygen.com"
HEADERS = {"X-Api-Key": HEYGEN_API_KEY, "Content-Type": "application/json"}


@retry()
def submit_video(script: str, avatar_id: str, voice_id: str) -> str:
    """
    提交 HeyGen 视频生成任务，返回 video_id（即 task_id）
    9:16 竖屏，1080×1920
    """
    payload = {
        "video_inputs": [
            {
                "character": {
                    "type": "avatar",
                    "avatar_id": avatar_id,
                    "avatar_style": "normal",
                },
                "voice": {
                    "type": "text",
                    "input_text": script,
                    "voice_id": voice_id,
                    "speed": 1.0,
                },
            }
        ],
        "dimension": {"width": 1080, "height": 1920},
        "aspect_ratio": None,
    }

    resp = requests.post(
        f"{BASE_URL}/v2/video/generate",
        json=payload,
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    video_id = resp.json()["data"]["video_id"]
    return video_id


@retry()
def check_video_status(video_id: str) -> dict:
    """
    查询视频生成状态
    返回 {status: "pending"|"processing"|"completed"|"failed", video_url, thumbnail_url}
    """
    resp = requests.get(
        f"{BASE_URL}/v1/video_status.get",
        params={"video_id": video_id},
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    return {
        "status": data.get("status", "unknown"),
        "video_url": data.get("video_url", ""),
        "thumbnail_url": data.get("thumbnail_url", ""),
        "duration": data.get("duration", 0),
        "error": data.get("error", {}),
    }
