"""
四平台文案改写模块
将口播正文改写为 TikTok / Instagram / Facebook / YouTube 专属文案(结构化输出 + 便宜模型)。
"""

from core import llm

SYSTEM = """你是精通多平台内容运营的社交媒体专家,熟悉 TikTok / Instagram / Facebook / YouTube 的规则和用户习惯。

各平台特点:
- TikTok:年轻化,直白口语,话题标签引流,结尾引导强,话题标签写在正文末尾
- Instagram:视觉+文字并重,有温度,话题标签精准垂直,写在正文末尾
- Facebook:偏成熟,完整叙事,互动引导自然,适合分享传播,无需话题标签
- YouTube:搜索驱动,标题含搜索关键词,描述详细便于 SEO"""

CAPTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "tiktok": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "标题(30字以内,含核心关键词)"},
                "caption": {"type": "string", "description": "正文(口语化,100字以内,结尾强引导),末尾附3个话题标签"},
            },
            "required": ["title", "caption"],
        },
        "instagram": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "标题(25字以内)"},
                "caption": {"type": "string", "description": "正文(有温度,150字以内),末尾附5个话题标签"},
            },
            "required": ["title", "caption"],
        },
        "facebook": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "标题(30字以内)"},
                "caption": {"type": "string", "description": "正文(200字以内,含互动引导,无需话题标签)"},
            },
            "required": ["title", "caption"],
        },
        "youtube": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "标题(40字以内,含搜索关键词)"},
                "description": {"type": "string", "description": "描述(250字以内,关键词+章节,SEO友好)"},
            },
            "required": ["title", "description"],
        },
    },
    "required": ["tiktok", "instagram", "facebook", "youtube"],
}


def adapt_for_platforms(final_script: str, topic: str) -> dict:
    """传入最终口播文案和选题,返回四平台文案 dict(含 Airtable 字段 + 结构化数据)。"""
    user_msg = f"""## 视频主题
{topic}

## 口播视频原始文案
{final_script}

请为四个平台各改写一版标题与文案。"""

    data = llm.structured(SYSTEM, user_msg, CAPTIONS_SCHEMA, model=llm.MODEL_CHEAP)

    tk = data.get("tiktok", {})
    ig = data.get("instagram", {})
    fb = data.get("facebook", {})
    yt = data.get("youtube", {})

    return {
        "TikTok Caption": f"{tk.get('title', '')}\n\n{tk.get('caption', '')}",
        "Instagram Caption": f"{ig.get('title', '')}\n\n{ig.get('caption', '')}",
        "Facebook Caption": f"{fb.get('title', '')}\n\n{fb.get('caption', '')}",
        "YouTube Description": f"{yt.get('title', '')}\n\n{yt.get('description', '')}",
        "_structured": {"tiktok": tk, "instagram": ig, "facebook": fb, "youtube": yt},
    }
