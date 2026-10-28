"""文件上传路由：落盘 + file_id（Mock 假 id / 真实经 Coze Files API）

安全要点（PRD 6 设计说明）：
- kind / 扩展名白名单校验
- 文件名一律服务端重命名 {uid}_{timestamp}_{rand}{ext}，杜绝重名与路径穿越
- 上传目录固定为 test_files/docs|audio，文件名不允许含路径分隔符
"""
import time
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from .. import coze_client
from ..security import get_current_user

router = APIRouter(prefix="/api/files", tags=["文件上传"])

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DOCS_DIR = PROJECT_ROOT / "test_files" / "docs"
AUDIO_DIR = PROJECT_ROOT / "test_files" / "audio"

UPLOAD_DIRS = {"resume": DOCS_DIR, "audio": AUDIO_DIR}
ALLOWED_EXTS = {"resume": {".doc", ".docx"}, "audio": {".mp3", ".wav"}}
MAX_BYTES = {"resume": 10 * 1024 * 1024, "audio": 20 * 1024 * 1024}


class FileValidationError(ValueError):
    """文件定位 / 归属校验失败（调用方转 SSE error 事件）"""


def server_filename(user_id: int, ext: str, tag: str = "") -> str:
    """服务端重命名：{uid}_{timestamp}_{tag}{rand}{ext}（ext 须已白名单化，防路径穿越）"""
    return f"{user_id}_{int(time.time())}_{tag}{uuid4().hex[:8]}{ext}"


def locate_uploaded_file(user_id: int, file_name: str, kind: str) -> Path:
    """按重命名规则定位当前用户已上传的文件（模块 7/8/9 评估入参）"""
    if not file_name or Path(file_name).name != file_name:  # 禁目录分隔符
        raise FileValidationError("file_name 不合法")
    if not file_name.startswith(f"{user_id}_"):  # 只能引用自己的文件
        raise FileValidationError("文件不存在或不属于当前用户")
    if Path(file_name).suffix.lower() not in ALLOWED_EXTS.get(kind, set()):
        raise FileValidationError("文件类型不支持")
    path = UPLOAD_DIRS[kind] / file_name
    if not path.is_file():
        raise FileValidationError("文件不存在或已被清理，请重新上传")
    return path


@router.post("/upload")
async def upload_file(
    kind: str = Form(...),
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    if kind not in UPLOAD_DIRS:
        raise HTTPException(status_code=400, detail="kind 仅支持 resume 或 audio")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTS[kind]:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型{ext or '（无后缀）'}，仅允许 {'、'.join(sorted(ALLOWED_EXTS[kind]))}",
        )
    data = await file.read()
    if len(data) > MAX_BYTES[kind]:
        raise HTTPException(status_code=400, detail="文件超过大小限制（简历 10MB / 录音 20MB）")

    save_dir = UPLOAD_DIRS[kind]
    save_dir.mkdir(parents=True, exist_ok=True)
    save_name = server_filename(user["id"], ext)
    save_path = save_dir / save_name
    save_path.write_bytes(data)

    # Mock：不调 Coze，直接假 file_id；真实：经 Coze Files API 上换取真 id（PRD 6 #1）
    file_id = coze_client.upload_file_id(save_path)
    return {
        "file_name": save_name,
        "file_path": save_path.relative_to(PROJECT_ROOT).as_posix(),
        "file_id": file_id,
    }
