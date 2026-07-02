"""
文案生成 + 风险审核模块

文案走「起草 → 自我挑刺 → 修订」三步:
  1. 好模型起草(hooks / body / ctas)
  2. 便宜模型挑刺(钩子够不够狠、有没有 AI 腔、是否具体)
  3. 好模型据此修订
全部用结构化输出(强制 tool use),不再手撕 JSON。
"""

import json
from core import llm

COPY_SYSTEM = """你是专业的短视频口播文案写手。你的文案特点:
- 口语化、自然流畅,像真人在说话,不用书面语、不用「首先其次最后」排比
- 开场3秒必须抓住注意力
- 正文有具体数据和可执行步骤,让观众觉得「拿走就能用」"""

CRITIQUE_SYSTEM = """你是挑剔的短视频内容主编,专挑口播文案的毛病。
重点看:钩子是否够狠、是否有 AI 腔/空话套话、是否足够具体可执行、节奏是否拖沓。
只指出问题并给出可操作的修改方向,不要重写文案。"""

REVIEW_SYSTEM = """你是专业的内容合规审核员,审查中文短视频口播文案的合规风险。
审核维度:
1. 广告法违禁词:绝对化用语(最、第一、唯一、永久、保证、再也不等)
2. 平台敏感词:可能触发限流的诱导互动(「扣1」「私信我」等)
3. 数据真实性风险:过于具体、无法核实的数字承诺
4. 第三方平台生态敏感词"""

SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "hooks": {
            "type": "array", "description": "3 个开场钩子备选",
            "items": {"type": "object", "properties": {
                "type": {"type": "string", "description": "钩子类型,如 悬念型/共鸣型/福利型"},
                "text": {"type": "string", "description": "钩子文案(15-25字)"},
            }, "required": ["type", "text"]},
        },
        "body": {"type": "string", "description": "口播正文(200-400字,口语化,含具体步骤和数据)"},
        "ctas": {
            "type": "array", "description": "2 个结尾 CTA 备选",
            "items": {"type": "object", "properties": {
                "type": {"type": "string", "description": "CTA 类型,如 关注引导/私信引导"},
                "text": {"type": "string", "description": "CTA 文案(20-35字)"},
            }, "required": ["type", "text"]},
        },
    },
    "required": ["hooks", "body", "ctas"],
}

CRITIQUE_SCHEMA = {
    "type": "object",
    "properties": {
        "weaknesses": {"type": "array", "items": {"type": "string"}, "description": "具体问题清单"},
        "revision_guidance": {"type": "string", "description": "给修订者的整体修改方向(100字以内)"},
    },
    "required": ["weaknesses", "revision_guidance"],
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "risk_level": {"type": "string", "description": "低风险 / 中风险 / 高风险"},
        "issues": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "type": {"type": "string"},
                "original": {"type": "string"},
                "reason": {"type": "string"},
                "suggestion": {"type": "string"},
            }, "required": ["type", "reason"]},
        },
        "summary": {"type": "string", "description": "总体评估(50字以内)"},
        "approved": {"type": "boolean"},
    },
    "required": ["risk_level", "summary", "approved"],
}


def generate_script(
    final_topic: str,
    ai_profile_summary: str,
    ai_experience_notes: str,
    positioning: str,
    target_audience: str,
    key_selling_points: str,
) -> dict:
    """起草 → 自评 → 修订,返回含 hooks / body / ctas 的 dict。"""
    profile = ai_profile_summary or f"账号定位:{positioning}\n目标受众:{target_audience}\n核心卖点:{key_selling_points}"
    experience = ai_experience_notes or "(暂无历史经验数据)"

    base_ctx = f"""## 账号配置
{profile}

## 历史经验(请参考爆款规律)
{experience}

## 本次选题
{final_topic}"""

    # 1) 起草(好模型)
    draft = llm.structured(
        COPY_SYSTEM,
        base_ctx + "\n\n请据此写一版口播文案。",
        SCRIPT_SCHEMA, model=llm.MODEL_QUALITY,
    )

    # 2) 自我挑刺(便宜模型)
    critique = llm.structured(
        CRITIQUE_SYSTEM,
        base_ctx + f"\n\n## 待审文案\n{json.dumps(draft, ensure_ascii=False)}\n\n请挑出问题并给出修改方向。",
        CRITIQUE_SCHEMA, model=llm.MODEL_CHEAP, max_tokens=1200,
    )

    # 3) 修订(好模型)
    revised = llm.structured(
        COPY_SYSTEM,
        base_ctx
        + f"\n\n## 初稿\n{json.dumps(draft, ensure_ascii=False)}"
        + f"\n\n## 主编意见\n问题:{json.dumps(critique.get('weaknesses', []), ensure_ascii=False)}"
        + f"\n方向:{critique.get('revision_guidance', '')}"
        + "\n\n请据此修订出更强的一版(钩子更狠、更具体、去 AI 腔)。",
        SCRIPT_SCHEMA, model=llm.MODEL_QUALITY,
    )
    return revised


def review_script(hooks: list, body: str, ctas: list) -> dict:
    """审核完整文案,返回风险评估结果(便宜模型)。"""
    full_text = "\n".join([
        *[f"【钩子-{h.get('type','')}】{h.get('text','')}" for h in hooks],
        f"\n【正文】\n{body}",
        *[f"【CTA-{c.get('type','')}】{c.get('text','')}" for c in ctas],
    ])
    user_msg = f"请审核以下口播文案,没问题时 issues 返回空数组、approved 为 true:\n\n---\n{full_text}\n---"
    return llm.structured(REVIEW_SYSTEM, user_msg, REVIEW_SCHEMA, model=llm.MODEL_CHEAP, max_tokens=1200)
