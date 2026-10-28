"""pytest 全局夹具：必须在导入 app 之前设置环境变量。

- MOCK_COZE=true：全链路 Mock，不发任何真实 Coze 请求（PRD 8.1）
- JIYE_DB_PATH：db.DB_PATH 指向临时测试库，不污染根目录 app.db（PRD 8.2）
- server：session 级真实 uvicorn 线程（127.0.0.1:8766），同步 httpx 直连，
  SSE 分块行为与生产一致（新版 httpx 的 ASGITransport 仅支持异步，故不用）
"""
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

os.environ["MOCK_COZE"] = "true"
_TMPDIR = tempfile.mkdtemp(prefix="jiuye_test_")
os.environ["JIYE_DB_PATH"] = str(Path(_TMPDIR) / "test_app.db")

import glob  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
import uvicorn  # noqa: E402

from backend import db  # noqa: E402  (此时 DB_PATH 已指向临时库)
from backend.app import app  # noqa: E402

SERVER_URL = "http://127.0.0.1:8766"
TABLES = ("user_knowledge_map", "resume_evaluations", "audio_analyses",
          "interview_questions", "users")

# 测试账号（temp_db 每次测后清库，固定账号可反复注册）
TEST_USER = "tuser"
TEST_PWD = "test123456"
TEST_IDC = "110101199003077758"

_CREATED_UIDS: set[int] = set()


@pytest.fixture(scope="session")
def server():
    """后台线程跑一个真实 uvicorn 实例（lifespan 会执行 init_db，落在临时库）"""
    config = uvicorn.Config(app, host="127.0.0.1", port=8766, log_level="error")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):  # 就绪探测
        try:
            httpx.get(SERVER_URL + "/docs", timeout=1)
            break
        except Exception:
            if not thread.is_alive():
                raise RuntimeError("测试服务器启动失败（8766 端口可能被占用）")
            time.sleep(0.1)
    yield SERVER_URL
    srv.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def temp_db():
    """每个测试一个干净库：先建表，测后清空全部业务表"""
    db.init_db()
    yield db
    conn = db.get_connection()
    try:
        for t in TABLES:
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def auth_client(server, temp_db):
    """注册 + 登录 → 带 Bearer 的 httpx Client（直连测试服务器）"""
    with httpx.Client(base_url=server, timeout=60) as client:
        r = client.post("/api/users/register", json={
            "username": TEST_USER, "password": TEST_PWD, "id_card": TEST_IDC,
            "phone": "13800138000", "email": "t@example.com",
        })
        assert r.status_code == 201, r.text
        r = client.post("/api/users/login", json={"username": TEST_USER, "password": TEST_PWD})
        assert r.status_code == 200, r.text
        data = r.json()
        _CREATED_UIDS.add(data["user"]["id"])
        client.headers["Authorization"] = f"Bearer {data['access_token']}"
        yield client


def collect_sse(client: httpx.Client, path: str, body: dict):
    """POST SSE 收集器（与技能 pitfalls #8/#13 同构）：\\n\\n 拆事件、按行取 event:/data:"""
    events: list[tuple[str, str]] = []
    with client.stream("POST", path, json=body) as resp:
        ctype = resp.headers.get("content-type", "")
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"
        buf = ""
        for chunk in resp.iter_text():
            buf += chunk
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                ev = data = None
                for line in block.split("\n"):
                    if line.startswith("event: "):
                        ev = line[7:]
                    elif line.startswith("data: "):
                        data = line[6:]
                events.append((ev, data))
    return ctype, events


@pytest.fixture(scope="session", autouse=True)
def _cleanup_uploads():
    """会话结束：清理测试期间上传到 test_files/ 的文件 + 删除临时库目录"""
    yield
    base = Path(__file__).resolve().parent
    for sub in ("docs", "audio"):
        for uid in _CREATED_UIDS:
            for f in glob.glob(str(base / "test_files" / sub / f"{uid}_*")):
                os.remove(f)
    shutil.rmtree(_TMPDIR, ignore_errors=True)
