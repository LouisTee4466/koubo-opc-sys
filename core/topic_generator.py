"""
选题生成模块
基于情报数据 + 账号配置,用结构化输出生成 10 条选题(便宜模型 + 好 prompt)。
"""

from core import llm

SYSTEM = """你是一位专业的短视频选题策略师,为口播视频账号设计高播放量选题。

方法:先从竞品高互动内容里提炼「为什么爆」的规律(钩子、情绪、结构、选题角度),
再结合账号定位与历史经验,产出差异化、可执行的选题。不要照搬竞品,要在其规律上做出更强的变体。

每条选题要具体到「场景 / 冲突 / 结果」,避免空泛。"""

TOPICS_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "description": "10 条选题",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "视频标题(中文,30字以内,含具体场景/结果)"},
                    "angle": {"type": "string", "description": "内容角度(50字以内,核心讲解逻辑)"},
                    "reference": {"type": "string", "description": "参考了哪条热点或竞品爆款规律(20字以内)"},
                    "why_viral": {"type": "string", "description": "为什么可能爆(60字以内,点明钩子/情绪/差异点)"},
                },
                "required": ["title", "angle", "why_viral"],
            },
        }
    },
    "required": ["topics"],
}


def generate_topics(
    intelligence_data: dict,
    ai_profile_summary: str,
    ai_experience_notes: str,
    positioning: str,
    target_audience: str,
    key_selling_points: str,
) -> list[dict]:
    """生成 10 条选题,返回 list[dict]。"""
    tavily_summary = _format_tavily(intelligence_data.get("tavily", []))
    tikhub_summary = _format_tikhub(intelligence_data.get("tikhub"))

    profile = ai_profile_summary or f"账号定位:{positioning}\n目标受众:{target_audience}\n核心卖点:{key_selling_points}"
    experience = ai_experience_notes or "(暂无历史经验数据)"

    user_msg = f"""## 账号定位
{profile}

## 历史经验结论(请重点参考)
{experience}

## 热点情报(Tavily)
{tavily_summary}

## 竞品高互动内容(TikHub,已按互动率排序)
{tikhub_summary}

先在心里提炼这些竞品「为什么爆」,再生成 10 条最有可能爆量的口播视频选题。"""

    result = llm.structured(SYSTEM, user_msg, TOPICS_SCHEMA, model=llm.MODEL_CHEAP, max_tokens=3000)
    return result.get("topics", [])


def _format_tavily(items: list) -> str:
    if not items:
        return "(无数据)"
    lines = []
    for item in items[:10]:
        if "error" in item:
            continue
        lines.append(f"- [{item.get('query','')}] {item.get('title','')}:{item.get('content','')[:100]}")
    return "\n".join(lines) or "(无数据)"


def _format_tikhub(items) -> str:
    if not items:
        return "(无竞品数据)"
    lines = []
    for v in items:
        if "error" in v:
            continue
        rate = v.get("engagement_rate")
        rate_str = f" 互动率{rate:.1%}" if isinstance(rate, (int, float)) else ""
        lines.append(
            f"- @{v.get('account','')} | 播放{v.get('play_count',0)} 点赞{v.get('like_count',0)} "
            f"评论{v.get('comment_count',0)}{rate_str} | {v.get('desc','')[:80]}"
        )
    return "\n".join(lines) or "(无竞品数据)"
