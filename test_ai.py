import httpx

BASE = "http://127.0.0.1:8000"

def collect_sse(path, body, headers):
    """POST SSE 收集：按 \\n\\n 拆事件（技能前端同构逻辑）"""
    events = []
    with httpx.stream("POST", BASE + path, json=body, headers=headers, timeout=60) as resp:
        print(f"\n== {path} | {resp.status_code} | {resp.headers.get('content-type')}")
        buf = ""
        for chunk in resp.iter_text():
            buf += chunk
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                ev = data = None
                for line in block.split("\n"):
                    if line.startswith("event: "): ev = line[7:]
                    elif line.startswith("data: "): data = line[6:]
                events.append((ev, data))
                # 截断打印，防止控制台刷屏
                print(f"event: {ev}  data: {(data or '')[:60]}")
    return events

with httpx.Client(base_url=BASE, timeout=60) as c:
    # 1. 登录拿 Token
    print("正在登录...")
    login_resp = c.post("/api/users/login", json={"username": "admin", "password": "admin123"})
    if login_resp.status_code != 200:
        print(f"登录失败: {login_resp.text}")
        exit(1)
    
    token = login_resp.json()["access_token"]
    H = {"Authorization": f"Bearer {token}"}
    print("登录成功，开始测试...")

    # 2. ① 上传简历 → ② 简历评估 SSE
    print("\n--- 测试模块 7：简历评估 ---")
    try:
        with open("test_files/docs/王大锤简历.docx", "rb") as f:
            up = c.post("/api/files/upload", headers=H, data={"kind": "resume"},
                        files={"file": ("my.docx", f)}).json()
        collect_sse("/api/resume/evaluate", {"file_name": up["file_name"]}, H)
    except FileNotFoundError:
        print("跳过简历评估：找不到 test_files/docs/王大锤简历.docx")

    # 3. ③ 录音分析 SSE
    print("\n--- 测试模块 8：录音分析 ---")
    try:
        with open("test_files/audio/面试录音1-短.mp3", "rb") as f:
            au = c.post("/api/files/upload", headers=H, data={"kind": "audio"},
                        files={"file": (".mp3", f)}).json()
        collect_sse("/api/audio/analyze", {"file_name": au["file_name"]}, H)
    except FileNotFoundError:
        print("跳过录音分析：找不到 test_files/audio/面试录音1-短.mp3")

    # 4. ④ 面试题生成 SSE（文本入口，无需上传）
    print("\n--- 测试模块 9：面试题生成 ---")
    collect_sse("/api/questions/generate", {"resume_text": "三年 Python 经验，做过 FastAPI 项目"}, H)

    # 5. ⑤ 验证落库
    print("\n--- 验证历史记录落库 ---")
    for p in ("/api/resume/history", "/api/audio/history", "/api/questions/history"):
        resp = c.get(p, headers=H)
        if resp.status_code == 200:
            print(p, "->", len(resp.json()), "条")
        else:
            print(p, "-> 查询失败", resp.status_code)