"""
集中封装 Claude 调用:模型分层 + 结构化输出(强制 tool use)+ 重试。

- 结构化输出用「强制工具调用」实现,直接拿到已解析的 dict,彻底告别手撕 JSON。
- 模型分层:便宜活给 Haiku,质量活给 Sonnet,均可用环境变量覆盖:
    MODEL_CHEAP   (默认 claude-haiku-4-5)  选题 / 审核 / 平台改写
    MODEL_QUALITY (默认 claude-sonnet-5)   口播正文 / 周复盘
"""

import os
import anthropic
from core.retry import retry

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

MODEL_CHEAP = os.getenv("MODEL_CHEAP", "claude-haiku-4-5")
MODEL_QUALITY = os.getenv("MODEL_QUALITY", "claude-sonnet-5")


@retry()
def structured(system: str, user: str, schema: dict, model: str,
               tool_name: str = "emit_result", max_tokens: int = 2500) -> dict:
    """强制模型按 schema 调用一个工具,返回已校验的 dict(消灭手撕 JSON)。"""
    msg = _client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        tools=[{
            "name": tool_name,
            "description": "按给定 schema 输出结构化结果。",
            "input_schema": schema,
        }],
        tool_choice={"type": "tool", "name": tool_name},
        messages=[{"role": "user", "content": user}],
    )
    for block in msg.content:
        if getattr(block, "type", None) == "tool_use":
            return block.input
    raise RuntimeError("结构化输出:模型未返回 tool_use 块")


@retry()
def text(system: str, user: str, model: str, max_tokens: int = 1500) -> str:
    """普通文本输出(用于周复盘等自由文本场景)。"""
    msg = _client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
    return "\n".join(parts).strip()
