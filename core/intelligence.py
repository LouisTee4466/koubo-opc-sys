"""
情报采集模块
Tavily 热点采集 + TikHub 竞品监控(抓更多、按互动率排序、带重试)。
"""

import os
import requests
from dotenv import load_dotenv

from core.retry import retry

load_dotenv()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
TIKHUB_API_KEY = os.getenv("TIKHUB_API_KEY")

# 竞品每账号抓取条数,取互动率最高的前 N 条进选题
_FETCH_PER_ACCOUNT = 20
_TOP_PER_ACCOUNT = 5


# ============================================================
# Tavily 热点采集
# ============================================================

@retry()
def _tavily_search(query: str) -> dict:
    resp = requests.post(
        "https://api.tavily.com/search",
        json={
            "api_key": TAVILY_API_KEY,
            "query": query,
            "search_depth": "basic",
            "max_results": 5,
            "include_answer": True,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_tavily_intel(industry: str) -> list[dict]:
    """用行业关键词搜索三个维度的热点,返回合并结果列表。"""
    queries = [
        f"{industry} trending content this week",
        f"{industry} viral videos",
        f"{industry} latest trends 2026",
    ]
    results = []
    for query in queries:
        try:
            data = _tavily_search(query)
            for item in data.get("results", []):
                results.append({
                    "query": query,
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "content": item.get("content", "")[:300],
                    "answer_summary": data.get("answer", "")[:200],
                })
        except Exception as e:
            results.append({"query": query, "error": str(e)})
    return results


# ============================================================
# TikHub 竞品监控
# ============================================================

@retry()
def _tikhub_fetch(username: str) -> dict:
    resp = requests.get(
        "https://api.tikhub.io/api/v1/tiktok/app/v3/fetch_user_post_videos",
        headers={"Authorization": f"Bearer {TIKHUB_API_KEY}"},
        params={"unique_id": username, "count": _FETCH_PER_ACCOUNT, "max_cursor": 0, "sort_type": 0},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_tikhub_intel(competitor_accounts: str) -> list[dict]:
    """传入逗号分隔的 TikTok 账号,返回每个账号「互动率最高」的前几条视频。"""
    if not competitor_accounts or not competitor_accounts.strip():
        return []

    accounts = [a.strip() for a in competitor_accounts.split(",") if a.strip()]
    all_videos = []

    for username in accounts:
        try:
            data = _tikhub_fetch(username)
            videos = data.get("data", {}).get("aweme_list", [])
            parsed = []
            for v in videos:
                stats = v.get("statistics", {})
                play = stats.get("play_count", 0) or 0
                like = stats.get("digg_count", 0) or 0
                comment = stats.get("comment_count", 0) or 0
                share = stats.get("share_count", 0) or 0
                # 互动率 =(赞+评+转)/ 播放,比裸播放量更能反映「为什么爆」
                engagement_rate = (like + comment + share) / play if play else 0.0
                parsed.append({
                    "account": username,
                    "desc": v.get("desc", "")[:150],
                    "play_count": play,
                    "like_count": like,
                    "comment_count": comment,
                    "share_count": share,
                    "engagement_rate": round(engagement_rate, 4),
                })
            # 按互动率降序,取前 N —— 抓的是「最能打」的,不是「最新」的
            parsed.sort(key=lambda x: x["engagement_rate"], reverse=True)
            all_videos.extend(parsed[:_TOP_PER_ACCOUNT])
        except Exception as e:
            all_videos.append({"account": username, "error": str(e)})

    return all_videos


# ============================================================
# 主入口:合并情报
# ============================================================

def gather_intelligence(industry: str, competitor_accounts: str) -> dict:
    """完整情报采集,返回结构化 dict,写入 Intelligence Data 字段。"""
    print(f"🔍 Tavily 热点采集:{industry}")
    tavily_data = fetch_tavily_intel(industry)

    if competitor_accounts and competitor_accounts.strip():
        print(f"🎯 TikHub 竞品监控(取高互动内容):{competitor_accounts}")
        tikhub_data = fetch_tikhub_intel(competitor_accounts)
    else:
        print("⚠️  无竞品账号,跳过 TikHub 采集")
        tikhub_data = None

    return {"industry": industry, "tavily": tavily_data, "tikhub": tikhub_data}
