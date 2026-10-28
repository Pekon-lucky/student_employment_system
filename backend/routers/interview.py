"""模拟面试路由：模块 10 Bot 聊天（SSE）+ 单用户动态知识库闭环 CRUD

知识库闭环（PRD 3.4，真实模式三步）：
  ① 删旧 documents.delete  ② 传新 files.upload + documents.create  ③ 绑定 bots.update
Mock 模式跳过全部 SDK 调用，直接按状态机落库 success（PRD 8.1）。
"""
import logging
import sqlite3
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from .. import config, sse
from ..coze_client import (
    coze_error_status,
    describe_coze_error,
    get_coze,
    log_remote_call,
    mock_bot_stream,
    real_bot_stream,
)
from ..db import get_db
from ..schemas import InterviewChatRequest
from ..security import get_current_user
from .files import DOCS_DIR, server_filename

router = APIRouter(tags=["模拟面试与知识库（模块 10）"])
logger = logging.getLogger(__name__)

# 知识库文件规则：kind → 允许扩展名 / 大小上限（questions 为 .md 面试题文本）
KB_EXTS = {"resume": {".doc", ".docx"}, "questions": {".md"}}
KB_MAX_BYTES = {"resume": 10 * 1024 * 1024, "questions": 2 * 1024 * 1024}


# ---------------------------------------------------------------------------
# Bot 聊天（PRD 6 #10）
# ---------------------------------------------------------------------------


@router.post("/api/interview/chat")
def interview_chat(req: InterviewChatRequest, user: dict = Depends(get_current_user)):
    message = (req.message or "").strip()
    if not message:
        # 技能规则 4：校验失败也走 SSE（Bot 协议：error + done 收尾）
        return sse.sse_response(
            [("error", {"message": "message 不能为空"}), ("done", {"conversation_id": ""})]
        )
    cid = (req.conversation_id or "").strip()
    coze = get_coze()
    if coze is None:
        events = mock_bot_stream(conversation_id=cid)
    else:
        events = real_bot_stream(coze, config.COZE_BOT_INTERVIEW, str(user["id"]), message, cid)
    return sse.sse_response(events)


# ---------------------------------------------------------------------------
# 知识库状态与闭环（PRD 6 #11/#12/#13）
# ---------------------------------------------------------------------------


def _get_or_create_row(db_conn, user_id: int) -> dict:
    """user_knowledge_map 每用户一行：无则惰性建行（其余列走 DEFAULT）"""
    row = db_conn.execute("SELECT * FROM user_knowledge_map WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        db_conn.execute("INSERT INTO user_knowledge_map (user_id) VALUES (?)", (user_id,))
        db_conn.commit()
        row = db_conn.execute("SELECT * FROM user_knowledge_map WHERE user_id = ?", (user_id,)).fetchone()
    return row


def _kb_status(row: dict) -> dict:
    """PRD 6 #11 的绑定状态出参"""
    return {
        "resume_file_name": row["resume_file_name"],
        "resume_sync_status": row["resume_sync_status"],
        "questions_file_name": row["questions_file_name"],
        "questions_sync_status": row["questions_sync_status"],
        "bot_bound": bool(row["bot_bound"]),
    }


def _real_sync_knowledge(coze, row: dict, kind: str, save_path: Path, save_name: str, user_id: int,
                         on_dataset_created=None):
    """真实闭环三步（SDK 签名已对照 cozepy 源码核实；PRD 10 风险 3）

    on_dataset_created：数据集创建成功后立刻回调落库，避免后续步骤失败重试时重复建库
    """
    from cozepy import (
        BotKnowledge,
        DocumentBase,
        DocumentChunkStrategy,
        DocumentFormatType,
        DocumentSourceInfo,
    )

    dataset_id = row["dataset_id"]
    if not dataset_id:  # 首次上传：创建用户专属数据集
        ds_payload = {
            "name": f"就业系统-用户{user_id}知识库",
            "space_id": config.COZE_SPACE_ID,
            "format_type": DocumentFormatType.DOCUMENT,
        }
        log_remote_call("datasets.create", ds_payload)
        dataset_id = coze.datasets.create(**ds_payload).dataset_id
        if on_dataset_created:
            on_dataset_created(dataset_id)
    old_doc_id = row[f"{kind}_doc_id"]
    if old_doc_id:  # ① 删旧（SDK 只收 document_ids，无 dataset_id 参数）
        coze.datasets.documents.delete(document_ids=[old_doc_id])

    with save_path.open("rb") as fp:  # ② 传新：Files API 换 file_id → 建 knowledge 文档
        uploaded = coze.files.upload(file=fp)

    # document/create 的三条硬性规范（对照 cozepy 源码 + Coze 官方文档核实）：
    #   1. format_type：0=文档类型（pdf/doc/docx/txt/md…），表格 1、图片 2
    #   2. source_info.document_source：用 Files API 的 file_id 时必须为 5（UPLOAD_FILE_ID）；
    #      传 0 表示"本地文件直传"，那种写法必须带 file_base64 + file_type，配 file_id 会 4000
    #   3. chunk_strategy：上传到**空知识库**（首次）时必填，缺省传 null 会报 4000 Request parameter error；
    #      后续上传服务端沿用首次设置、不支持修改
    doc_payload = {
        "dataset_id": dataset_id,
        "document_bases": [
            DocumentBase(name=save_name, source_info=DocumentSourceInfo.build_file_id(uploaded.id))
        ],
        "chunk_strategy": DocumentChunkStrategy.build_auto(),  # chunk_type=0 自动分段与清洗
        "format_type": DocumentFormatType.DOCUMENT,
    }
    log_remote_call("datasets.documents.create", doc_payload)
    docs = coze.datasets.documents.create(**doc_payload)
    new_doc_id = docs.data[0].document_id if docs.data else ""

    bot_payload = {
        "bot_id": config.COZE_BOT_INTERVIEW,
        "knowledge": BotKnowledge(dataset_ids=[dataset_id], auto_call=True),
    }
    log_remote_call("bots.update", bot_payload)
    coze.bots.update(**bot_payload)  # ③ 绑定（已知限制：全局覆盖，单用户演示场景，PRD 10 风险 1）
    return dataset_id, new_doc_id


@router.get("/api/knowledge")
def knowledge_status(user: dict = Depends(get_current_user), db_conn=Depends(get_db)):
    return _kb_status(_get_or_create_row(db_conn, user["id"]))


@router.post("/api/knowledge/{kind}")
def knowledge_upload(
    kind: str,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    db_conn: sqlite3.Connection = Depends(get_db),
):
    # 同步 def：FastAPI 把整条路由（含 get_db 连接）放同一线程池线程执行，
    # 避免 async 路由跨线程使用 sqlite3 连接报 check_same_thread 错误
    if kind not in KB_EXTS:
        raise HTTPException(status_code=400, detail="kind 仅支持 resume 或 questions")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in KB_EXTS[kind]:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型{ext or '（无后缀）'}，仅允许 {'、'.join(sorted(KB_EXTS[kind]))}",
        )
    data = file.file.read()  # 同步路由里用同步读（SpooledTemporaryFile）
    if len(data) > KB_MAX_BYTES[kind]:
        raise HTTPException(status_code=400, detail="文件超过大小限制（简历 10MB / 面试题 2MB）")

    row = _get_or_create_row(db_conn, user["id"])
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    save_name = server_filename(user["id"], ext, tag="kb")
    save_path = DOCS_DIR / save_name
    save_path.write_bytes(data)

    # 状态机：none → syncing（落库留痕，同步接口内随后落到 success/failed）
    db_conn.execute(
        f"UPDATE user_knowledge_map SET {kind}_sync_status = 'syncing', {kind}_file_name = ? WHERE user_id = ?",
        (save_name, user["id"]),
    )
    db_conn.commit()

    coze = get_coze()
    try:
        if coze is None:
            # Mock：不发真实请求，直接按状态机落 success（PRD 8.1）
            dataset_id = row["dataset_id"] or f"mock-dataset-{user['id']}"
            new_doc_id = f"mock-doc-{uuid4().hex[:12]}"
        else:
            def _save_dataset(ds_id: str) -> None:
                """数据集一建好就落库：后续步骤若失败，重试时复用而非新建"""
                db_conn.execute(
                    "UPDATE user_knowledge_map SET dataset_id = ? WHERE user_id = ?", (ds_id, user["id"])
                )
                db_conn.commit()

            dataset_id, new_doc_id = _real_sync_knowledge(
                coze, row, kind, save_path, save_name, user["id"], on_dataset_created=_save_dataset
            )
        db_conn.execute(
            "UPDATE user_knowledge_map SET dataset_id = ?, "
            f"{kind}_doc_id = ?, {kind}_file_name = ?, {kind}_sync_status = 'success', bot_bound = 1 "
            "WHERE user_id = ?",
            (dataset_id, new_doc_id, save_name, user["id"]),
        )
        db_conn.commit()
    except Exception as exc:
        db_conn.execute(
            f"UPDATE user_knowledge_map SET {kind}_sync_status = 'failed' WHERE user_id = ?", (user["id"],)
        )
        db_conn.commit()
        # 翻译成可读中文（令牌失效/权限不足/超时/其它，含 code+logid），
        # 超时回 504、其余回 502；前端 Toast 直接展示 detail，无需再猜原因。
        raise HTTPException(status_code=coze_error_status(exc), detail=describe_coze_error(exc)) from exc

    row = db_conn.execute("SELECT * FROM user_knowledge_map WHERE user_id = ?", (user["id"],)).fetchone()
    return _kb_status(row)


@router.delete("/api/knowledge/{kind}")
def knowledge_delete(
    kind: str,
    user: dict = Depends(get_current_user),
    db_conn: sqlite3.Connection = Depends(get_db),
):
    if kind not in KB_EXTS:
        raise HTTPException(status_code=400, detail="kind 仅支持 resume 或 questions")
    row = _get_or_create_row(db_conn, user["id"])
    doc_id = row[f"{kind}_doc_id"]

    if doc_id and row["dataset_id"]:
        coze = get_coze()
        if coze is not None:  # 删除即解绑：远端删旧文档（失败不阻断本地解绑，演示场景可接受孤儿文档）
            try:
                coze.datasets.documents.delete(document_ids=[doc_id])
            except Exception as exc:
                # 远端删除失败不阻断本地解绑（演示场景可接受孤儿文档），但要留痕
                logger.warning("Coze 文档删除失败 doc_id=%s：%s", doc_id, describe_coze_error(exc))

    db_conn.execute(
        f"UPDATE user_knowledge_map SET {kind}_doc_id = '', {kind}_file_name = '', {kind}_sync_status = 'none' "
        "WHERE user_id = ?",
        (user["id"],),
    )
    # 两类知识都清空 → 数据集一并解绑
    row = db_conn.execute("SELECT * FROM user_knowledge_map WHERE user_id = ?", (user["id"],)).fetchone()
    if not row["resume_doc_id"] and not row["questions_doc_id"]:
        db_conn.execute(
            "UPDATE user_knowledge_map SET dataset_id = '', bot_bound = 0 WHERE user_id = ?", (user["id"],)
        )
    db_conn.commit()

    row = db_conn.execute("SELECT * FROM user_knowledge_map WHERE user_id = ?", (user["id"],)).fetchone()
    return _kb_status(row)
