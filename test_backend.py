"""阶段三后端自动化测试（MOCK_COZE=true）—— 断言严格对照 coze-chat 技能事件定义

覆盖 PRD 8.2 断言清单：
1. 三个工作流 SSE：content-type / delta / done{} / 无 error / 历史表 +1 且契约正确
2. Bot SSE：delta / done 带 conversation_id；回传 cid 多轮一致
3. 无 token / 假 token 调 SSE 接口 → 401 JSON（鉴权在流开启前）
4. 上传接口：小文件上传成功；非法 kind / 扩展名 → 400
5. user_knowledge_map CRUD：上传 success → GET 字段 → DELETE 恢复 none/空
6. 完整性回归：阶段一/二接口抽查（登录、profile-status）
7. 校验失败走 SSE error（技能规则 4），不以纯 JSON 返回
"""
import json

import httpx
import pytest

from conftest import SERVER_URL, TEST_PWD, TEST_USER, collect_sse

DOCX_BYTES = b"PK\x03\x04-fake-docx-bytes"
MP3_BYTES = b"ID3-fake-mp3-bytes"
MD_TEXT = "# 面试题\n\n1. 自我介绍（考察点：表达结构）"


def _upload(client, kind, name, data):
    return client.post("/api/files/upload", data={"kind": kind},
                       files={"file": (name, data, "application/octet-stream")})


# ---------------------------------------------------------------------------
# 1. 三个工作流 SSE（模块 7/8/9）
# ---------------------------------------------------------------------------


def test_resume_evaluate_sse_and_history(auth_client):
    c = auth_client
    r = _upload(c, "resume", "my.docx", DOCX_BYTES)
    assert r.status_code == 200, r.text
    up = r.json()
    assert up["file_id"].startswith("mock-file-")  # Mock：假 file_id，无真实请求
    assert up["file_path"].startswith("test_files/docs/")

    ctype, events = collect_sse(c, "/api/resume/evaluate", {"file_name": up["file_name"]})
    # 技能工作流协议：content-type 为 text/event-stream；delta* → done{}；无 error
    assert ctype.startswith("text/event-stream")
    kinds = [e for e, _ in events]
    assert "delta" in kinds
    assert kinds[-1] == "done"
    assert "error" not in kinds
    assert events[-1][1] == "{}"  # 工作流 done 的 data 为 {}（无 conversation_id）

    # 流结束落库（Mock 同样入库）：input_data / result_data 契约（PRD 5.1）
    hist = c.get("/api/resume/history").json()
    assert len(hist) == 1
    assert hist[0]["input_data"]["file_name"] == up["file_name"]
    assert hist[0]["input_data"]["saved_path"].startswith("test_files/docs/")
    # 只传 file_name 的兜底路径：Mock 现场生成新假 id（真实模式为现场重传，本就是新 id）
    assert hist[0]["input_data"]["file_id"].startswith("mock-file-")
    assert "匹配度打分" in hist[0]["result_data"]["report"]

    # 显式透传 file_id（前端正常会把上传返回值带上）→ 落库与传入一致
    collect_sse(c, "/api/resume/evaluate",
                {"file_name": up["file_name"], "file_id": up["file_id"]})
    hist2 = c.get("/api/resume/history").json()
    assert len(hist2) == 2
    assert hist2[0]["input_data"]["file_id"] == up["file_id"]

    # 单条详情 + 越权/不存在 → 404
    detail = c.get(f"/api/resume/history/{hist[0]['id']}")
    assert detail.status_code == 200 and "report" in detail.json()["result_data"]
    assert c.get("/api/resume/history/99999").status_code == 404


def test_audio_analyze_sse_and_history(auth_client):
    c = auth_client
    r = _upload(c, "audio", "a.mp3", MP3_BYTES)
    assert r.status_code == 200
    up = r.json()

    ctype, events = collect_sse(c, "/api/audio/analyze", {"file_name": up["file_name"]})
    assert ctype.startswith("text/event-stream")
    kinds = [e for e, _ in events]
    assert "delta" in kinds and kinds[-1] == "done" and "error" not in kinds
    assert events[-1][1] == "{}"

    hist = c.get("/api/audio/history").json()
    assert len(hist) == 1
    assert hist[0]["input_data"]["file_name"] == up["file_name"]
    assert "语速评价" in hist[0]["result_data"]["report"]


def test_questions_generate_text_sse_and_history(auth_client):
    c = auth_client
    ctype, events = collect_sse(
        c, "/api/questions/generate", {"resume_text": "三年 Python 经验，做过 FastAPI 项目"}
    )
    assert ctype.startswith("text/event-stream")
    kinds = [e for e, _ in events]
    assert "delta" in kinds and kinds[-1] == "done" and "error" not in kinds

    hist = c.get("/api/questions/history").json()
    assert len(hist) == 1
    assert hist[0]["input_data"]["source"] == "text"
    assert "resume_text" in hist[0]["input_data"]
    assert "questions_markdown" in hist[0]["result_data"]


# ---------------------------------------------------------------------------
# 2. Bot SSE（模块 10）：conversation_id 回传与多轮
# ---------------------------------------------------------------------------


def test_bot_sse_and_multi_turn(auth_client):
    c = auth_client
    ctype, events = collect_sse(c, "/api/interview/chat", {"message": "你好"})
    assert ctype.startswith("text/event-stream")
    kinds = [e for e, _ in events]
    assert "delta" in kinds and kinds[-1] == "done" and "error" not in kinds
    done_data = json.loads(events[-1][1])
    assert "conversation_id" in done_data  # Bot 协议独有，与工作流 done{} 区分
    cid = done_data["conversation_id"]
    assert cid.startswith("mock-conv-")

    # 多轮策略 A：回传 cid → done 下发同一 cid
    _, events2 = collect_sse(c, "/api/interview/chat", {"message": "继续", "conversation_id": cid})
    assert json.loads(events2[-1][1])["conversation_id"] == cid


# ---------------------------------------------------------------------------
# 3. 鉴权：无 token / 假 token 调 SSE 接口 → 401 JSON
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path,body", [
    ("/api/resume/evaluate", {"file_name": "x.docx"}),
    ("/api/audio/analyze", {"file_name": "x.mp3"}),
    ("/api/questions/generate", {"resume_text": "x"}),
    ("/api/interview/chat", {"message": "x"}),
])
def test_sse_requires_auth(path, body, server):
    # 401 发生在流开启与任何 DB 访问之前，无需建库
    with httpx.Client(base_url=server, timeout=30) as client:
        r = client.post(path, json=body)  # 不带 Authorization
        assert r.status_code == 401
        assert r.headers["content-type"].startswith("application/json")  # 鉴权在流开启前
        r = client.post(path, json=body, headers={"Authorization": "Bearer fake.token.here"})
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# 4. 上传接口校验
# ---------------------------------------------------------------------------


def test_upload_validation(auth_client):
    c = auth_client
    assert _upload(c, "bogus", "x.docx", DOCX_BYTES).status_code == 400      # kind 白名单
    assert _upload(c, "resume", "x.exe", b"MZ").status_code == 400           # 扩展名白名单
    assert _upload(c, "resume", "x.mp3", b"ID3").status_code == 400          # 类型与 kind 不匹配
    assert _upload(c, "audio", "a.wav", b"RIFF").status_code == 200          # .wav 合法


# ---------------------------------------------------------------------------
# 5. 知识库闭环 CRUD（user_knowledge_map，PRD 8.2 #5）
# ---------------------------------------------------------------------------


def test_knowledge_crud(auth_client):
    c = auth_client
    # 初始状态：全空
    st = c.get("/api/knowledge").json()
    assert st["resume_sync_status"] == "none" and st["bot_bound"] is False

    # 上传简历知识库 → success（Mock：不发真实请求，直接按状态机落库）
    r = c.post("/api/knowledge/resume", files={"file": ("kb.docx", DOCX_BYTES, "application/octet-stream")})
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["resume_sync_status"] == "success" and st["resume_file_name"].endswith(".docx")
    assert st["bot_bound"] is True

    # 上传面试题知识库（.md）→ success；resume 状态不受影响
    r = c.post("/api/knowledge/questions", files={"file": ("qs.md", MD_TEXT.encode(), "text/markdown")})
    st = r.json()
    assert st["questions_sync_status"] == "success" and st["bot_bound"] is True

    # GET 与 POST 出参一致
    st = c.get("/api/knowledge").json()
    assert st["resume_sync_status"] == "success" and st["questions_sync_status"] == "success"

    # 非法扩展名 / 非法 kind
    assert c.post("/api/knowledge/resume", files={"file": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 400
    assert c.post("/api/knowledge/whatever", files={"file": ("x.md", b"x", "text/markdown")}).status_code == 400

    # 删除即解绑：先删 resume（questions 仍在 → bot_bound 保持 true）
    st = c.delete("/api/knowledge/resume").json()
    assert st["resume_sync_status"] == "none" and st["resume_file_name"] == ""
    assert st["bot_bound"] is True
    # 再删 questions → 两类全空 → 数据集一并解绑
    st = c.delete("/api/knowledge/questions").json()
    assert st["questions_sync_status"] == "none" and st["bot_bound"] is False


# ---------------------------------------------------------------------------
# 6. 校验失败走 SSE error（技能规则 4：所有响应一律 SSE）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path,body", [
    ("/api/resume/evaluate", {"file_name": "../etc/passwd.docx"}),   # 路径穿越
    ("/api/resume/evaluate", {"file_name": "999_other.docx"}),       # 非本人文件
    ("/api/resume/evaluate", {}),                                     # 缺 file_name
    ("/api/questions/generate", {}),                                  # 文本/文件都缺
])
def test_workflow_validation_is_sse(auth_client, path, body):
    ctype, events = collect_sse(auth_client, path, body)
    assert ctype.startswith("text/event-stream")  # 不是纯 JSON
    kinds = [e for e, _ in events]
    assert "error" in kinds
    assert kinds[-1] == "done"  # error 后仍补 done 收尾（统一协议）


def test_chat_validation_is_sse(auth_client):
    ctype, events = collect_sse(auth_client, "/api/interview/chat", {"message": ""})
    assert ctype.startswith("text/event-stream")
    kinds = [e for e, _ in events]
    assert "error" in kinds and kinds[-1] == "done"
    assert "conversation_id" in json.loads(events[-1][1])  # Bot 协议 done 收尾


# ---------------------------------------------------------------------------
# 7. 阶段一/二回归抽查
# ---------------------------------------------------------------------------


def test_regression_auth_and_profile(server, temp_db):
    with httpx.Client(base_url=server, timeout=30) as c:
        assert c.post("/api/users/register", json={
            "username": TEST_USER, "password": TEST_PWD, "id_card": "110101199003077758",
        }).status_code == 201
        # 登录失败统一 400（不区分用户不存在/密码错误）
        r = c.post("/api/users/login", json={"username": TEST_USER, "password": "wrong!"})
        assert r.status_code == 400
        # 登录成功 → profile-status complete=false（注册时未填 phone/email）
        token = c.post("/api/users/login", json={"username": TEST_USER, "password": TEST_PWD}).json()["access_token"]
        r = c.get("/api/users/profile-status", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200 and r.json()["complete"] is False


# ---------------------------------------------------------------------------
# 8. 并发回归（sqlite3 跨线程）
# FastAPI 把同步依赖的建立与销毁分别丢进 anyio 线程池，线程池不保证是同一个线程；
# 并发时 get_db 的连接可能在 A 线程创建、在 B 线程 close，默认 check_same_thread=True
# 会抛 sqlite3.ProgrammingError。db.get_connection 已改为 check_same_thread=False，
# 本用例固化该场景：混合并打多个查库接口，必须全部 200。
# ---------------------------------------------------------------------------


def test_concurrent_db_requests_no_thread_error(auth_client):
    from concurrent.futures import ThreadPoolExecutor

    paths = [
        "/api/users/me",
        "/api/users/profile-status",
        "/api/resume/history",
        "/api/audio/history",
        "/api/questions/history",
        "/api/knowledge",
    ]

    def hit(i):
        return auth_client.get(paths[i % len(paths)]).status_code

    with ThreadPoolExecutor(max_workers=24) as ex:
        codes = list(ex.map(hit, range(150)))

    assert set(codes) == {200}, f"并发请求出现异常状态码：{sorted(set(codes))}"
