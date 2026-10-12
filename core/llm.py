"""
集中封装 Claude 调用:模型分层 + 结构化输出(output_config.format)+ 重试。

- 结构化输出用 JSON Schema 约束返回格式,直接拿到已解析的 dict,彻底告别手撕 JSON。
  (Sonnet 5.5 / Opus 5.5 不再接受强制 tool_choice,所以不走「强制工具调用」。)
- 模型分层:轻活给 Sonnet,质量活给 Opus,均可用环境变量覆盖:
    MODEL_CHEAP   (默认 claude-sonnet-5-5)  选题 / 审核 / 平台改写
    MODEL_QUALITY (默认 claude-opus-5-5)    口播正文 / 周复盘
    EFFORT_CHEAP / EFFORT_QUALITY (默认 low / medium)  思考深度,越高越慢越贵
"""

import json
import os
import anthropic
from core.retry import retry

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

MODEL_CHEAP = os.getenv("MODEL_CHEAP") or "claude-sonnet-5-5"
MODEL_QUALITY = os.getenv("MODEL_QUALITY") or "claude-opus-5-5"
EFFORT_CHEAP = os.getenv("EFFORT_CHEAP") or "low"
EFFORT_QUALITY = os.getenv("EFFORT_QUALITY") or "medium"

# 这两个模型的思考无法关闭,且思考 token 计入 max_tokens,上限给足才不会截断正文
_MAX_TOKENS = 16000

# 安全分类器误拒时由服务端自动换模型重试,仅这两个模型支持 "default" 模式
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5"}


def _strict(schema):
    """结构化输出要求每个 object 都声明 additionalProperties: false,递归补上。"""
    if isinstance(schema, dict):
        out = {k: _strict(v) for k, v in schema.items()}
        if out.get("type") == "object":
            out.setdefault("additionalProperties", False)
        return out
    if isinstance(schema, list):
        return [_strict(v) for v in schema]
    return schema


def _complete(system: str, user: str, model: str, max_tokens: int, schema: dict | None = None) -> str:
    output_config = {"effort": EFFORT_QUALITY if model == MODEL_QUALITY else EFFORT_CHEAP}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": _strict(schema)}

    extra = {}
    if model in _FALLBACK_MODELS:
        extra = {"betas": [_FALLBACK_BETA], "fallbacks": "default"}

    msg = _client.beta.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        output_config=output_config,
        messages=[{"role": "user", "content": user}],
        **extra,
    )
    if msg.stop_reason == "refusal":
        category = getattr(msg.stop_details, "category", None)
        raise RuntimeError(f"模型拒绝了本次请求(category={category}),请调整选题或文案后重试")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError(f"输出被 max_tokens={max_tokens} 截断,请调高上限或降低 EFFORT")
    # 返回内容开头可能是 thinking 块,按类型取 text
    return "\n".join(b.text for b in msg.content if b.type == "text").strip()


@retry()
def structured(system: str, user: str, schema: dict, model: str,
               max_tokens: int = _MAX_TOKENS) -> dict:
    """让模型按 schema 输出 JSON,返回已解析的 dict(消灭手撕 JSON)。"""
    return json.loads(_complete(system, user, model, max_tokens, schema))


@retry()
def text(system: str, user: str, model: str, max_tokens: int = _MAX_TOKENS) -> str:
    """普通文本输出(用于周复盘等自由文本场景)。"""
    return _complete(system, user, model, max_tokens)
