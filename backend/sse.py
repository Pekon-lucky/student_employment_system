"""SSE 事件工具（后端 → 前端统一协议，严格对照 coze-chat 技能）

前端只认以下事件（技能"前后端协议"章节）：
  工作流场景（模块 7/8/9）：delta / interrupt / done / error —— done 的 data 为 {}
  Bot 场景（模块 10）    ：delta / done / error               —— done 的 data 含 conversation_id
技能规则 4：所有响应一律 SSE —— 校验失败也必须先发 error 事件再结束，不能返回纯 JSON
"""
import json
from typing import Iterator

from fastapi.responses import StreamingResponse


def _sse(event: str, data: dict) -> str:
    """格式化一条 SSE 事件：event/data 行 + 空行结尾（事件边界）"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_response(events) -> StreamingResponse:
    """把 (event, data) 元组可迭代对象包装成 SSE StreamingResponse（统一响应头）"""
    return StreamingResponse(
        event_stream(events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def sse_error(message: str) -> str:
    """技能统一 error 事件：参数校验失败、上游 Coze 失败都走这里"""
    return _sse("error", {"message": message})


def event_stream(events: Iterator[tuple[str, dict]]) -> Iterator[str]:
    """把 (event, data) 元组流转成 SSE 文本流，路由层直接喂给 StreamingResponse"""
    for event, data in events:
        yield _sse(event, data)


def _clean_message(content: str) -> str:
    """工作流节点输出清洗（workflow-guide"输出清洗" + 技能 pitfalls #17）

    工作流会把两类"噪音"混进节点输出，转发前端前必须清洗：
    1. 参数 schema：'[{"type":"string","name":"input","required":true}]' → 丢弃
    2. End 节点 JSON 包装：'{"output":"结果文本"}' → 只取 output 的值
    其余（非法 JSON、普通文本）→ 原样返回
    """
    text = (content or "").strip()
    if not text:
        return ""
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return text
    if isinstance(obj, list) and obj and all(
        isinstance(x, dict) and "name" in x and "type" in x for x in obj
    ):
        return ""  # 参数 schema 噪音
    if isinstance(obj, dict) and "output" in obj:
        out = obj.get("output")
        if isinstance(out, str):
            return out
        if out is not None:
            return str(out)
    return text
