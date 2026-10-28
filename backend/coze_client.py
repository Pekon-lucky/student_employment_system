"""Coze 访问层：SDK 单例 + Mock 假流 + 真实流适配器（严格遵循 coze-chat 技能规范）

统一事件元组协议 —— 本模块的生成器（Mock 与真实适配器同构）yield (event, data)
二元组，路由层经 sse.sse_response() 转成 SSE 流：

  工作流（模块 7/8/9）：("delta", {"text": ...})* → ("done", {})                ← 无 conversation_id
  Bot（模块 10）      ：("delta", {"text": ...})* → ("done", {"conversation_id": ...})
  失败路径            ：("error", {"message": ...})，随后仍补 ("done", ...) 收尾
"""
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from . import config


@lru_cache(maxsize=1)
def get_coze():
    """Coze SDK 单例。

    - MOCK_COZE=true → 返回 None，调用方判空后走本模块的 Mock 假流
    - 非 Mock 且未配置 COZE_TOKEN → 抛 RuntimeError（避免带空令牌打真实请求）
    """
    if config.MOCK_COZE:
        return None
    if not config.COZE_TOKEN:
        raise RuntimeError("未配置 COZE_TOKEN：请在 .env 填入 pat_ 个人访问令牌，或设置 MOCK_COZE=true")
    # 延迟导入：Mock 模式下连 cozepy 都不加载（规避其 authlib 弃用告警，见技能 pitfalls #14d）
    import httpx
    from cozepy import COZE_CN_BASE_URL, Coze, TokenAuth
    from cozepy.request import SyncHTTPClient

    # 显式注入 HTTP client：cozepy 默认「读 600s / 连接 5s」，连接 5s 在网络抖动或
    # 走代理时极易超时；超时值统一走 .env（COZE_TIMEOUT / COZE_CONNECT_TIMEOUT）。
    http_client = SyncHTTPClient(
        timeout=httpx.Timeout(config.COZE_TIMEOUT, connect=config.COZE_CONNECT_TIMEOUT),
    )
    return Coze(
        auth=TokenAuth(token=config.COZE_TOKEN),
        base_url=config.COZE_BASE_URL or COZE_CN_BASE_URL,
        http_client=http_client,
    )


def describe_coze_error(exc: Exception) -> str:
    """把 Coze/网络异常翻译成「可操作的中文提示」，同时保留原始 code/msg/logid 便于排查。

    分流依据（按命中顺序）：CozeAPIError 业务码 → httpx 超时/连接 → 其它。
    cozepy 采用延迟导入：Mock 模式下本模块不加载 SDK。
    """
    try:
        from cozepy.exception import CozeAPIError
    except Exception:  # 未安装/未加载 cozepy（Mock 模式）
        CozeAPIError = None  # type: ignore

    if CozeAPIError is not None and isinstance(exc, CozeAPIError):
        code = getattr(exc, "code", None)
        msg = getattr(exc, "msg", "") or str(exc)
        logid = getattr(exc, "logid", "") or ""
        tail = f"（code={code}" + (f", logid={logid}）" if logid else "）")
        if code == 4100:
            return ("Coze 令牌无效或已过期" + tail + "：请到 Coze 控制台 → 个人设置 → 访问令牌 "
                    "重新生成 pat_ 令牌，更新 .env 的 COZE_TOKEN 后重启服务")
        low = msg.lower()
        if "permission" in low or "no auth" in low or "forbidden" in low or "not allowed" in low:
            return f"Coze 令牌权限不足，缺少该资源的操作权限{tail}：{msg}"
        return f"Coze 接口返回错误{tail}：{msg}"

    import httpx
    if isinstance(exc, httpx.TimeoutException):
        return (f"Coze 接口请求超时（读 {config.COZE_TIMEOUT:.0f}s / 连接 "
                f"{config.COZE_CONNECT_TIMEOUT:.0f}s），可检查网络或在 .env 调大 COZE_TIMEOUT")
    if isinstance(exc, httpx.ConnectError):
        return "无法连接 Coze 服务，请检查网络、代理或防火墙设置"
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text[:200] if exc.response is not None else ""
        return f"Coze 接口返回 HTTP {exc.response.status_code}：{body}"
    return f"{type(exc).__name__}: {exc}"


def coze_error_status(exc: Exception) -> int:
    """路由层回给前端的 HTTP 状态码：超时 → 504（网关超时），其余 → 502（上游失败）"""
    import httpx
    return 504 if isinstance(exc, httpx.TimeoutException) else 502


def log_remote_call(label: str, payload: dict) -> None:
    """调试用：把即将发给 Coze 的请求体打到终端，肉眼核对字段与取值。

    - IntEnum 会还原成数值（如 format_type=0、document_source=5），与线上 JSON 完全一致
    - 长字符串（base64 等）截断，避免刷屏
    """
    def shorten(value):
        if hasattr(value, "model_dump"):  # pydantic 模型转成与线上 JSON 同构的 dict
            value = value.model_dump()
        if isinstance(value, str) and len(value) > 120:
            return f"{value[:120]}…(共 {len(value)} 字符)"
        if isinstance(value, dict):
            return {k: shorten(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [shorten(v) for v in value]
        return value

    print(f"[coze-payload] {label} => "
          f"{json.dumps(shorten(payload), ensure_ascii=False, default=str)}", flush=True)


# ---------------------------------------------------------------------------
# Mock 假数据与假流（MOCK_COZE=true 时使用；事件序列严格对照 coze-chat 技能）
# ---------------------------------------------------------------------------

MOCK_RESUME_REPORT = """## 匹配度打分

**82 / 100** —— 与目标岗位（Python 后端开发）整体匹配度较高，项目经历与技能栈基本吻合，但成果量化不足。

## 核心优势

- 技术栈对口：Python / FastAPI / SQLite 有完整项目落地经验；
- 具备前后端联调能力，能独立完成从需求到部署的闭环；
- 项目功能描述清晰，模块划分合理。

## 存在的不足

- 项目成果缺少量化数据（QPS、用户量、响应时间等）；
- 技能清单过于笼统，"熟悉"较多、"精通"支撑不足；
- 缺少与目标岗位强相关的关键词（如 Docker、Redis）。

## 修改建议

1. 用数字量化项目成果，例如"接口平均响应 < 50ms，支撑 1000+ 并发"；
2. 按 JD 关键词重排技能清单，删掉与岗位无关的条目；
3. 项目描述采用 STAR 结构：背景 → 任务 → 行动 → 结果。
"""

MOCK_AUDIO_REPORT = """## 语速评价

**良好**。整体语速约每分钟 180 字，处于面试舒适区间；自我介绍段落偏快，建议在关键成果处适当停顿。

## 清晰度评价

**中等偏上**。发音清楚，但存在少量口头禅（"然后"、"就是"共出现 9 次），影响专业感。

## 逻辑连贯性分析

自我介绍遵循了"基本信息 → 项目经历 → 求职意向"的结构，逻辑完整；但项目部分从"项目 A"直接跳到"项目 C"，缺少过渡句。

## 改进建议

1. 用 15 秒预告结构开场："我将从三个方面介绍自己"；
2. 项目之间用一句过渡语衔接，如"讲完性能优化，再聊聊团队协作"；
3. 刻意练习减少口头禅，回答前停顿 1 秒组织语言。
"""

MOCK_QUESTIONS_REPORT = """## 模拟面试题（共 5 题）

### 1. 请介绍简历中最能代表你技术水平的项目，并说明你的技术选型理由（技术题）

**考察点**：项目真实性、技术深度、选型思维与表达结构。

### 2. 你在项目中遇到过最难的技术问题是什么？如何定位并解决？（技术题）

**考察点**：问题排查的方法论、debug 能力、复盘意识。

### 3. FastAPI 相比 Flask/Django 的取舍？为什么本项目选它？（技术题）

**考察点**：框架底层理解、异步模型认知、技术选型的权衡能力。

### 4. 讲一次你和团队成员意见不一致的经历，最终如何解决？（行为题）

**考察点**：沟通协作、同理心、以目标为导向解决分歧的能力。

### 5. 如果实习期间同时接到两个紧急任务，你会如何安排？（行为题）

**考察点**：优先级管理、向上沟通、抗压能力。
"""

# 模块 → Mock 报告文本（路由 Mock 分支按模块取用）
MOCK_REPORTS = {
    "resume": MOCK_RESUME_REPORT,
    "audio": MOCK_AUDIO_REPORT,
    "questions": MOCK_QUESTIONS_REPORT,
}


def _chunk_text(text: str, parts: int = 8) -> list[str]:
    """把完整报告切成 parts 段，模拟流式增量输出"""
    size = max(1, math.ceil(len(text) / parts))
    return [text[i:i + size] for i in range(0, len(text), size)]


def mock_workflow_stream(report_text: str, parts: int = 8) -> Iterator[tuple[str, dict]]:
    """工作流 Mock 假流：delta* → done {}（对照技能工作流协议，无 conversation_id）"""
    for chunk in _chunk_text(report_text, parts):
        yield ("delta", {"text": chunk})
    yield ("done", {})


def mock_bot_stream(chunks: list[str] | None = None,
                    conversation_id: str = "") -> Iterator[tuple[str, dict]]:
    """Bot Mock 假流：delta* → done {"conversation_id": ...}（对照技能 Bot 协议，多轮策略 A）

    首轮不传 conversation_id 时生成 mock-conv-<随机>；前端保存后回传即保持多轮一致。
    """
    for chunk in (chunks or ["（Mock）你好，我是你的模拟面试官。我们开始今天的面试吧："
                             "请先用两分钟介绍你简历里最有代表性的一个项目。"]):
        yield ("delta", {"text": chunk})
    yield ("done", {"conversation_id": conversation_id or f"mock-conv-{uuid4().hex[:12]}"})


# ---------------------------------------------------------------------------
# Coze 文件与真实流适配器（MOCK_COZE=false 时使用；事件属性对照 cozepy SDK 源码核实）
# ---------------------------------------------------------------------------


def upload_file_id(path) -> str:
    """把本地文件上传到 Coze Files API 换取 file_id；Mock 模式返回假 id（不发真实请求）"""
    coze = get_coze()
    if coze is None:
        return f"mock-file-{uuid4().hex[:12]}"
    with Path(path).open("rb") as fp:
        return coze.files.upload(file=fp).id


def real_workflow_stream(coze, workflow_id: str, parameters: dict) -> Iterator[tuple[str, dict]]:
    """工作流真实流（技能 workflow-guide + PRD 3.2）：
    MESSAGE → _clean_message 清洗 → delta；ERROR → error；INTERRUPT → interrupt；
    DONE 被 SDK 静默过滤（pitfalls #16）→ 迭代结束后补 done {}
    """
    from cozepy import WorkflowEventType

    from .sse import _clean_message

    try:
        for event in coze.workflows.runs.stream(workflow_id=workflow_id, parameters=parameters):
            if event.event == WorkflowEventType.MESSAGE:
                text = _clean_message(event.message.content if event.message else "")
                if text:
                    yield ("delta", {"text": text})
            elif event.event == WorkflowEventType.ERROR:
                msg = (event.error.error_message if event.error else "") or "工作流执行失败"
                yield ("error", {"message": msg})
            elif event.event == WorkflowEventType.INTERRUPT:
                data = event.interrupt.interrupt_data if event.interrupt else None
                yield ("interrupt", {"event_id": data.event_id if data else "",
                                     "type": data.type if data else 0})
    except Exception as exc:  # Token 失效等非 SSE 错误（技能规则 3：拦截 JSON 错误）
        yield ("error", {"message": f"AI 服务异常：{describe_coze_error(exc)}"})
    yield ("done", {})


def real_bot_stream(coze, bot_id: str, user_id: str, message: str,
                    conversation_id: str = "") -> Iterator[tuple[str, dict]]:
    """Bot 真实流（技能主文档五规则 + fastapi-cozepy 模板同构）：
    只处理 type=answer（过滤 verbose）、跳过 completed 全文、逐事件捕获 conversation_id、
    空回复兜底 error；done 下发 conversation_id（多轮策略 A）
    """
    from cozepy import ChatEventType, Message, MessageType

    captured_cid = conversation_id
    has_content = False
    try:
        for event in coze.chat.stream(
            bot_id=bot_id,
            user_id=user_id,
            additional_messages=[Message.build_user_question_text(message)],
            conversation_id=conversation_id or None,  # pitfalls #14a：SDK 传 None 禁空串
        ):
            if event.chat and event.chat.conversation_id:
                captured_cid = event.chat.conversation_id
            if event.event == ChatEventType.CONVERSATION_MESSAGE_DELTA:
                if event.message and event.message.type == MessageType.ANSWER and event.message.content:
                    has_content = True
                    yield ("delta", {"text": event.message.content})
            elif event.event == ChatEventType.CONVERSATION_CHAT_FAILED:
                msg = "对话失败"
                if event.chat and event.chat.last_error:
                    msg = event.chat.last_error.msg or msg
                yield ("error", {"message": msg})
            # created / in_progress / completed → 静默（completed 全文跳过，规则 2/3）
    except Exception as exc:
        yield ("error", {"message": f"面试服务异常：{describe_coze_error(exc)}"})
    if not has_content:
        yield ("error", {"message": "Coze 未返回任何内容"})
    yield ("done", {"conversation_id": captured_cid})
