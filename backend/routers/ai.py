"""AI 工作流路由：模块 7 简历评估 / 模块 8 录音分析 / 模块 9 面试题生成

- 三个模块共用一条 SSE 通路：校验 → 事件生成器（Mock 或真实）→ 流结束聚合全文落库
- 技能规则 4：包括参数校验失败在内，所有响应一律 SSE（error + done 收尾）
- 历史查询为普通 JSON 接口（PRD 6 #3/4/7/9）
"""
import json

from fastapi import APIRouter, Depends, HTTPException

from .. import config, db, sse
from ..coze_client import get_coze, mock_workflow_stream, real_workflow_stream, upload_file_id
from ..db import get_connection, get_db
from ..schemas import FileSourceRequest, QuestionsGenerateRequest
from ..security import get_current_user
from .files import PROJECT_ROOT, FileValidationError, locate_uploaded_file

router = APIRouter(tags=["AI 工作流（模块 7/8/9）"])

# 模块注册表：表名 / 结果字段 / 工作流配置键（表名来自内部字典，非用户输入，可安全拼 SQL）
_MODULES = {
    "resume": {"table": "resume_evaluations", "result_key": "report", "config_key": "COZE_WORKFLOW_RESUME"},
    "audio": {"table": "audio_analyses", "result_key": "report", "config_key": "COZE_WORKFLOW_AUDIO"},
    "questions": {
        "table": "interview_questions",
        "result_key": "questions_markdown",
        "config_key": "COZE_WORKFLOW_QUESTIONS",
    },
}


def _resolve_file_id(path) -> str:
    """file_id 兜底解析：评估时才需要 file_id —— Mock 给假 id；真实模式现场经 Files API 重传"""
    from pathlib import Path

    return upload_file_id(Path(path))


def _prepare(module: str, user_id: int, file_name: str, file_id: str, resume_text: str):
    """准备 input_data（PRD 5.1 契约）与工作流入参；失败抛异常 → 转 SSE error"""
    if module in ("resume", "audio"):
        if not file_name:
            raise ValueError("缺少 file_name：请先调用 /api/files/upload")
        path = locate_uploaded_file(user_id, file_name, module)
        file_id = file_id or _resolve_file_id(path)
        # saved_path 存项目相对路径（与上传接口 file_path 口径一致，不泄露盘符）
        input_data = {"file_name": file_name,
                      "saved_path": path.relative_to(PROJECT_ROOT).as_posix(),
                      "file_id": file_id}
        key = config.WORKFLOW_INPUT_KEYS[module]["file"]
        return input_data, {key: file_id}

    # 模块 9：粘贴文本 / 上传修改稿二选一
    if resume_text.strip():
        text = resume_text.strip()
        input_data = {"source": "text", "resume_text": text[:8000]}  # PRD 5.1：截断 8000 字
        return input_data, {config.WORKFLOW_INPUT_KEYS["questions"]["text"]: text}
    if file_name:
        path = locate_uploaded_file(user_id, file_name, "resume")  # 修改稿走简历目录/类型
        file_id = file_id or _resolve_file_id(path)
        input_data = {"source": "file", "file_name": file_name}
        # resume_data 须为 JSON 字符串（实际键名实现期与工作流编排核对一次，PRD 10 风险 2）
        payload = json.dumps({"file_id": file_id}, ensure_ascii=False)
        return input_data, {config.WORKFLOW_INPUT_KEYS["questions"]["file"]: payload}
    raise ValueError("请提供 resume_text 或 file_name 之一")


def _module_stream(module: str, user_id: int, *, file_name: str = "", file_id: str = "",
                   resume_text: str = ""):
    """统一工作流流：校验 → 转发事件元组 → done 之后聚合全文落库（Mock 同样入库，PRD 4.1）"""
    meta = _MODULES[module]
    acc: list[str] = []
    input_data: dict = {}
    try:
        input_data, params = _prepare(module, user_id, file_name, file_id, resume_text)
        coze = get_coze()
        if coze is None:
            from ..coze_client import MOCK_REPORTS

            events = mock_workflow_stream(MOCK_REPORTS[module])
        else:
            events = real_workflow_stream(coze, getattr(config, meta["config_key"]), params)
        for event, data in events:
            if event == "delta":
                acc.append(data.get("text", ""))
            yield event, data
    except Exception as exc:  # 校验失败 / 准备阶段失败 → 技能规则 4：SSE error + done 收尾
        yield "error", {"message": str(exc) or "AI 服务异常"}
        yield "done", {}
        return

    if acc:  # 有增量才落库（error 且无内容不写历史）
        conn = get_connection()  # 生成器内独立连接：开/关都在同一次 next() 内，无线程问题
        try:
            conn.execute(
                f"INSERT INTO {meta['table']} (user_id, input_data, result_data) VALUES (?, ?, ?)",
                (
                    user_id,
                    json.dumps(input_data, ensure_ascii=False),
                    json.dumps({meta["result_key"]: "".join(acc)}, ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 工作流 SSE 路由（PRD 6 #2/#5/#8）
# ---------------------------------------------------------------------------


@router.post("/api/resume/evaluate")
def resume_evaluate(req: FileSourceRequest, user: dict = Depends(get_current_user)):
    return sse.sse_response(_module_stream("resume", user["id"], file_name=req.file_name, file_id=req.file_id))


@router.post("/api/audio/analyze")
def audio_analyze(req: FileSourceRequest, user: dict = Depends(get_current_user)):
    return sse.sse_response(_module_stream("audio", user["id"], file_name=req.file_name, file_id=req.file_id))


@router.post("/api/questions/generate")
def questions_generate(req: QuestionsGenerateRequest, user: dict = Depends(get_current_user)):
    return sse.sse_response(
        _module_stream("questions", user["id"], file_name=req.file_name, file_id=req.file_id,
                       resume_text=req.resume_text)
    )


# ---------------------------------------------------------------------------
# 历史查询（PRD 6 #3/#4/#7/#9，普通 JSON 接口）
# ---------------------------------------------------------------------------


def _loads(text: str) -> dict:
    try:
        return json.loads(text) if text else {}
    except ValueError:
        return {"raw": text}


def _history_list(db_conn, table: str, user_id: int) -> list[dict]:
    rows = db_conn.execute(
        f"SELECT id, input_data, result_data, created_at FROM {table} WHERE user_id = ? ORDER BY id DESC",
        (user_id,),
    ).fetchall()
    for row in rows:  # JSON 文本解析成对象，前端直接用
        row["input_data"] = _loads(row["input_data"])
        row["result_data"] = _loads(row["result_data"])
    return rows


def _history_detail(db_conn, table: str, user_id: int, record_id: int) -> dict:
    row = db_conn.execute(
        f"SELECT id, input_data, result_data, created_at FROM {table} WHERE id = ? AND user_id = ?",
        (record_id, user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    row["input_data"] = _loads(row["input_data"])
    row["result_data"] = _loads(row["result_data"])
    return row


@router.get("/api/resume/history")
def resume_history(user: dict = Depends(get_current_user), db_conn=Depends(get_db)):
    return _history_list(db_conn, _MODULES["resume"]["table"], user["id"])


@router.get("/api/resume/history/{record_id}")
def resume_history_detail(record_id: int, user: dict = Depends(get_current_user), db_conn=Depends(get_db)):
    return _history_detail(db_conn, _MODULES["resume"]["table"], user["id"], record_id)


@router.get("/api/audio/history")
def audio_history(user: dict = Depends(get_current_user), db_conn=Depends(get_db)):
    return _history_list(db_conn, _MODULES["audio"]["table"], user["id"])


@router.get("/api/audio/history/{record_id}")
def audio_history_detail(record_id: int, user: dict = Depends(get_current_user), db_conn=Depends(get_db)):
    return _history_detail(db_conn, _MODULES["audio"]["table"], user["id"], record_id)


@router.get("/api/questions/history")
def questions_history(user: dict = Depends(get_current_user), db_conn=Depends(get_db)):
    return _history_list(db_conn, _MODULES["questions"]["table"], user["id"])


@router.get("/api/questions/history/{record_id}")
def questions_history_detail(record_id: int, user: dict = Depends(get_current_user), db_conn=Depends(get_db)):
    return _history_detail(db_conn, _MODULES["questions"]["table"], user["id"], record_id)
