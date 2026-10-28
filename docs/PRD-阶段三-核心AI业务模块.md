# 学生就业帮助系统 — 阶段三 PRD：核心 AI 业务模块（模块 7-10）

| 项目 | 内容 |
|---|---|
| 版本 | v1.0 |
| 日期 | 2026-10-28 |
| 前置依赖 | 阶段一（用户模块/鉴权/4 张表）、阶段二（前端 5 页 + api.js/toast.js/SSE MIME 修复）已验收 |
| 规范遵循 | **全文严格遵循本地 coze-chat 技能规范**（主文档 + workflow-guide + cozepy-sdk-guide + pitfalls 20 条） |
| 技术底座 | 原生 HTML/CSS/JS (ES6 modules) + marked.js CDN ｜ FastAPI + 原生 sqlite3（禁 ORM）｜ cozepy SDK |

---

## 1. 背景与目标

阶段一/二已完成用户体系与前端骨架。阶段三交付 4 个 AI 业务模块，形成"简历评估 → 线下改简历 → 生成面试题 → 知识库闭环 → 模拟面试"的业务闭环：

- **模块 7 简历评估**：上传 Word 简历 → 工作流流式输出评估报告
- **模块 8 录音分析**：上传面试录音 → 工作流流式输出分析报告
- **模块 9 面试题生成**：输入修改后简历（文本或文件）→ 工作流流式生成 5+ 道带考察点的面试题
- **模块 10 模拟面试**：简历 + 面试题构建**单用户动态知识库** → Bot 流式多轮文字面试

所有 AI 交互均为 **SSE 流式**，前端打字机效果 + marked.js 渲染 Markdown。

## 2. 总体架构

```
浏览器 (ES6 modules)                FastAPI 后端                        Coze 平台
┌──────────────┐   JSON/fetch    ┌──────────────────┐   cozepy SDK   ┌─────────────┐
│ pages/*.js   │ ──────────────> │ routers/ai.py    │ ──workflows──> │ 3 个工作流    │
│ core/sse.js  │ <────────────── │ routers/interview│ ──chat.stream─>│ 模拟面试 Bot  │
│ (SSE 解析)    │  SSE 事件流      │ routers/files.py │ ──datasets───> │ 知识库/文件API │
└──────────────┘                 └──────────────────┘               └─────────────┘
        ▲                                │
        └── token 鉴权（阶段二 JWT）        ├── sqlite3 历史表 + user_knowledge_map
                                        └── MOCK_COZE=true 时全部 Coze 调用替换为假流（测试）
```

**分层原则**：前端只认技能定义的简化 SSE 协议；后端是唯一与 Coze 对话的一方；所有业务表读写走 `db.py` 的 dict_factory 连接。

## 3. Coze 对接契约（严格对照 coze-chat 技能）

### 3.1 后端 → 前端统一事件协议（唯一接口）

**工作流场景（模块 7/8/9）— 四种事件**（workflow-guide）：

```
event: delta      data: {"text": "增量文字"}
event: interrupt  data: {"event_id": "...", "type": 5}     ← 本期工作流为单次执行，协议保留、暂不触发
event: done       data: {}                                  ← 工作流无 conversation_id
event: error      data: {"message": "错误描述"}
```

**Bot 场景（模块 10）— 三种事件**（技能主文档）：

```
event: delta      data: {"text": "增量文字"}
event: done       data: {"conversation_id": "..."}          ← 供前端保存，多轮回传
event: error      data: {"message": "错误描述"}
```

**强制规则**（技能五条核心规则 + 踩坑 #9）：
1. 所有响应一律 `text/event-stream`，**包括参数校验失败**（先返回 `event: error` 再结束，不返回纯 JSON）
2. 只处理 `type == "answer"` 的消息，过滤 `verbose`（pitfalls #2/#12：data 可能是字符串，需 isinstance(dict) 防御）
3. 跳过 `message.completed` 全文，delta 已逐字构建，重复发送会造成尾部复制（pitfalls #3）
4. conversation_id 从事件提取、经 done 下发、下次回传（**策略 A**）；SDK 传 `cid or None`，禁止空串（pitfalls #14a）
5. 工作流 DONE 事件被 SDK 静默过滤 → **迭代结束即完成**，循环后发 done（pitfalls #16）

### 3.2 工作流调用规范（模块 7/8/9）

```python
from cozepy import COZE_CN_BASE_URL, Coze, TokenAuth, WorkflowEventType

for event in coze.workflows.runs.stream(
    workflow_id=config.COZE_WORKFLOW_RESUME,          # 按模块选择
    parameters={...},                                  # 键名匹配工作流输入变量
):
    if event.event == WorkflowEventType.MESSAGE:       # 节点输出 → 清洗 → delta
        text = _clean_message(event.message.content)   # 见下方清洗规则
    elif event.event == WorkflowEventType.ERROR:       # error.error_message → error
    elif event.event == WorkflowEventType.INTERRUPT:   # → interrupt 事件（本期不预期）
    # DONE 不出现在迭代里（pitfalls #16）
```

**输出清洗 `_clean_message()`**（workflow-guide + pitfalls #17，必须实现）：
- 过滤参数 schema：JSON 数组且元素含 `name`/`type` 字段 → 丢弃
- 提取 End 节点包装：`{"output": "文本"}` → 只取 output 值
- 非法 JSON / 其他字符串 → 原样返回

**文件入参约定**：后端先经 Coze Files API（`coze.files.upload`）上传文件取得 `file_id`，再以 `parameters` 传入工作流；**入参变量名以 Coze 工作流实际编排为准，统一收敛到 `config.py` 的映射字典**（实现期与工作流核对一次即可，不改业务代码）。

### 3.3 Bot 调用规范（模块 10）

```python
from cozepy import ChatEventType, Message, MessageType

for event in coze.chat.stream(
    bot_id=config.COZE_BOT_INTERVIEW,
    user_id=str(user_id),                              # 业务用户标识
    additional_messages=[Message.build_user_question_text(message)],
    conversation_id=cid or None,                       # 多轮策略 A：前端回传
):
    if event.event == ChatEventType.CONVERSATION_MESSAGE_DELTA:
        if event.message.type == MessageType.ANSWER:   # 过滤 verbose
            yield _sse("delta", {"text": event.message.content})
    elif event.event == ChatEventType.CONVERSATION_CHAT_FAILED:
        yield _sse("error", {"message": event.chat.last_error.msg})  # ChatError 对象取 .msg（pitfalls #14b）
    # COMPLETED → 循环结束发 done（带 conversation_id）；IN_PROGRESS/CREATED 忽略
```

### 3.4 单用户动态知识库闭环（模块 10）

**闭环三步**（每次上传知识库文件时按序执行，任一步失败 → sync_status=failed 并 Toast 报错）：

```
① 删旧：coze.datasets.documents.delete(dataset_id, document_id)   # 有旧文档时
② 传新：coze.datasets.documents.upload(dataset_id, file)          # 得到新 document_id
③ 绑定：coze.bots.update(bot_id, dataset_ids=[用户的 dataset_id]) # 更新 Bot 知识库
```

- 首次上传时若无 `dataset_id`：先 `coze.datasets.create(space_id, name="就业系统-用户{uid}知识库")` 创建用户专属数据集
- `sync_status` 状态机：`none → syncing → success | failed`，前端实时展示
- ⚠️ 已知限制：Bot 的 `dataset_ids` 是全局配置，多用户先后绑定会互相覆盖。本期定位为**单用户演示场景**（串行使用），PRD 如实记录该限制；多用户隔离（按用户克隆 Bot）列为后续优化

### 3.5 资源 ID 与配置（`.env` 扩展）

```ini
COZE_TOKEN=pat_xxx            # 正式个人访问令牌（pat_ 前缀，pitfalls #19；cztei_ 是测试令牌不可用）
COZE_SPACE_ID=7699303055962964008
COZE_WORKFLOW_RESUME=7700863208177565705      # 模块 7 简历评估工作流
COZE_WORKFLOW_QUESTIONS=7700880187995815978   # 模块 9 面试题生成工作流
COZE_WORKFLOW_AUDIO=7700886592182124594       # 模块 8 录音分析工作流
COZE_BOT_INTERVIEW=7701523779528622114        # 模块 10 模拟面试 Bot
MOCK_COZE=true                # true=全链路 Mock（测试期默认），false=真实调用
```

`config.py` 新增以上字段；`coze_client.py` 提供 `get_coze()` 单例，`MOCK_COZE=true` 时返回 None（调用方走 Mock 分支）。

## 4. 模块功能需求

### 4.1 模块 7：简历评估（resume.html）

| 项 | 说明 |
|---|---|
| 输入 | 上传 `.doc/.docx` 简历（≤10MB），存 `test_files/docs/` |
| 输出 | 流式 Markdown 报告，固定四节：**匹配度打分（1-100）、核心优势、存在的不足、修改建议** |
| 交互 | 右侧报告区打字机输出（delta 追加 + marked 重渲）；**左侧历史记录列表**，点击回看（GET 单条 → 渲染 result_data） |
| 衔接 | 学生根据"修改建议"线下修改简历 → 修改稿用于模块 9 / 模块 10 知识库 |
| 落库 | 流结束后聚合全文写入 `resume_evaluations`（Mock 模式同样入库） |

### 4.2 模块 8：录音分析（audio.html）

- 输入：`.mp3/.wav`（≤20MB），存 `test_files/audio/`
- 输出：流式报告四节：**语速评价、清晰度评价、逻辑连贯性分析、改进建议**
- 交互与落库同模块 7（历史表 `audio_analyses`）

### 4.3 模块 9：面试题生成（questions.html）

- 输入（二选一）：① 粘贴简历文本；② 上传修改后的 `.doc/.docx`
- 输出：流式生成 **至少 5 道面试题（技术题 + 行为题混合），每题附"考察点"**
- 交互：流式预览 + "保存到历史"；`interview_questions` 表 `result_data` 存题目 Markdown，供回看与**导出 .md**（模块 10 知识库上传的一键来源，P2 增强）
- 落库：`input_data` 记录来源（文本摘要/文件名），`result_data` 存题目全文

### 4.4 模块 10：模拟面试（interview.html）

**页面结构（三区）**：

1. **知识库管理区（左侧栏）**
   - 两个上传位：`简历知识库`（.doc/.docx）、`面试题知识库`（.md）
   - 每个位置显示：当前绑定文件名 + 同步状态徽标（同步中/成功/失败）
   - 上传 → 触发 3.4 闭环 → 状态轮询/响应更新；提供"解除绑定"按钮（DELETE）
   - P2 增强：面试题上传位提供"从模块 9 历史一键选用"按钮
2. **聊天区（主区，类 ChatGPT）**
   - AI 充当面试官，依据知识库提问；根据回答**评价或追问**
   - 气泡布局：用户右侧（纯文本转义），AI 左侧（**marked.js 渲染 Markdown** + 打字指示器，pitfalls #7）
   - 流式：delta 追加到当前 AI 气泡实时重渲
   - 多轮：登录后首条消息不传 conversation_id（新会话），此后回传 done 下发的 id（存 `localStorage.interview_cid_{userId}`）
3. **右上角"清空聊天记录"**：**仅清空前端当前会话状态**（消息数组 + conversation_id 键），不删后端/Coze 数据

**页面准入**：JWT 守卫（同阶段二）；未上传知识库时聊天区顶部出现常驻提示条（非阻塞），引导先完成知识库绑定。

## 5. 数据契约（sqlite3，dict_factory 直出 JSON）

### 5.1 已有三张历史表（阶段一建好，本期启用）

| 表 | input_data（JSON 文本） | result_data（JSON 文本） |
|---|---|---|
| `resume_evaluations` | `{"file_name","saved_path","file_id"}` | `{"report": "Markdown 全文"}` |
| `audio_analyses` | `{"file_name","saved_path","file_id"}` | `{"report": "Markdown 全文"}` |
| `interview_questions` | `{"source":"text/file","resume_text"(截断 8000 字),"file_name"}` | `{"questions_markdown": "..."}` |

公共字段：`id, user_id(FK→users ON DELETE CASCADE), created_at(datetime('now','localtime'))`。

### 5.2 新增 `user_knowledge_map`（阶段三建表，`db.py` DDL 追加）

```sql
CREATE TABLE IF NOT EXISTS user_knowledge_map (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id               INTEGER NOT NULL UNIQUE,       -- 每用户一行（单用户知识库）
    dataset_id            TEXT NOT NULL DEFAULT '',      -- 用户专属 Coze 数据集 ID
    resume_doc_id         TEXT NOT NULL DEFAULT '',      -- 简历文档 document_id
    resume_file_name      TEXT NOT NULL DEFAULT '',
    resume_sync_status    TEXT NOT NULL DEFAULT 'none',  -- none/syncing/success/failed
    questions_doc_id      TEXT NOT NULL DEFAULT '',      -- 面试题文档 document_id
    questions_file_name   TEXT NOT NULL DEFAULT '',
    questions_sync_status TEXT NOT NULL DEFAULT 'none',
    bot_bound             INTEGER NOT NULL DEFAULT 0,    -- Bot 是否已绑定该数据集
    updated_at            TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
```

`init_db()` 启动时自动补建（CREATE IF NOT EXISTS，对老库无破坏）。

## 6. API 接口定义（全部 JWT 鉴权，除注明外）

| # | 方法 | 路径 | 入参 | 出参 / 行为 |
|---|---|---|---|---|
| 1 | POST | `/api/files/upload` | multipart：`kind`(resume\|audio)、`file` | `{file_name, file_path, file_id}`；真实模式经 Coze Files API 取 file_id，**Mock 模式只存盘、不发真实请求** |
| 2 | POST | `/api/resume/evaluate` | JSON `{"file_name"}` | **SSE 工作流流**（delta/done/error；interrupt 保留）；结束写历史表 |
| 3 | GET | `/api/resume/history` | — | `[{id, input_data, result_data, created_at}]` 倒序 |
| 4 | GET | `/api/resume/history/{id}` | — | 单条详情（回看渲染） |
| 5-7 | 同构 | `/api/audio/analyze`、`/api/audio/history(/{id})` | 同上 | 同上（audio_analyses） |
| 8 | POST | `/api/questions/generate` | JSON `{"resume_text"}` 或 `{"file_name"}` | SSE 工作流流；结束写 `interview_questions` |
| 9 | GET | `/api/questions/history(/{id})` | — | 同上 |
| 10 | POST | `/api/interview/chat` | JSON `{"message", "conversation_id"}` | **SSE Bot 流**（delta/done{conversation_id}/error）；校验失败也返回 SSE error |
| 11 | GET | `/api/knowledge` | — | 绑定状态：`{resume_file_name, resume_sync_status, questions_file_name, questions_sync_status, bot_bound}` |
| 12 | POST | `/api/knowledge/{kind}` | multipart：`kind`(resume\|questions)、`file`(.doc/.docx 或 .md) | 执行 3.4 闭环（删旧→传新→绑 Bot），行内状态 `syncing→success/failed`，返回最新绑定状态 |
| 13 | DELETE | `/api/knowledge/{kind}` | — | 删远端文档（有则删）+ 清空对应映射字段，返回最新状态 |

> 设计说明：**"上传即绑定"**（接口 12 = 上传+绑定闭环一体），**"删除即解绑"**（接口 13），满足"删除/上传/绑定"三动作且前端少一次往返；`kind` 白名单校验，防目录穿越；文件名服务端重命名（`{uid}_{timestamp}{ext}`）。

## 7. 前端工程化

### 7.1 目录结构（含阶段二文件迁移）

```
frontend/
├── index.html / login.html / register.html / forgot.html / profile.html   # 既有
├── resume.html  audio.html  questions.html  interview.html               # ★ 新增 4 页（均引 marked CDN + 同步内联路由守卫）
├── test_sse.html                # ★ SSE 调试页（见 8.3）
├── css/style.css                # 追加：报告布局、历史侧栏、聊天气泡、打字指示器、同步徽标
└── js/
    ├── auth.js  main.js  profile.js          # 既有（仅更新 import 路径）
    ├── core/
    │   ├── api.js               # ★ 迁自 js/api.js（request/requireAuth/tokenStore/logout）
    │   ├── toast.js             # ★ 迁自 js/toast.js
    │   ├── sse.js               # ★ 新增：POST SSE 解析（见 7.2）
    │   └── ui.js                # ★ 新增：模块 7/8/9 公共 UI + 聊天组件（见 7.3）
    └── pages/
        ├── resume.js  audio.js  questions.js   # ★ 仅组装配置，逻辑复用 core/ui.js
        └── interview.js                        # ★ 模块 10 专有逻辑
```

### 7.2 `core/sse.js` — POST SSE 解析（对照技能 pitfalls #8/#13）

```javascript
// fetch + ReadableStream POST（禁用 EventSource，它只支持 GET）
export async function streamSSE(path, body, { onDelta, onInterrupt, onDone, onError, signal } = {}) {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${tokenStore.get()}` },
    body: JSON.stringify(body), signal,
  });
  // 技能规则 4：所有响应都是流；非 200 也按 error 事件处理
  const reader = resp.body.getReader(), decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = processBuffer(buffer, handlers);   // 按 \n\n 分割完整事件（事件内按 \n 拆行取 event:/data:）
  }                                             // 残尾 buffer 留待下个 chunk —— 严格 pitfalls #13
}
```

分发规则：`delta` → onDelta(text)；`done` → onDone(data)；`interrupt` → onInterrupt({event_id,type})；`error` → onError(message)（内部同时 Toast）。

### 7.3 `core/ui.js` — 抽离模块 7/8/9 公共逻辑（DRY 核心）

| 函数 | 职责 |
|---|---|
| `initReportPage(cfg)` | 一份配置跑通"选文件→上传→SSE 流式→打字机→入库刷新历史"全流程；模块 7/8/9 仅传 `{uploadAccept, ssePath, buildBody, historyPath, reportTitle}` |
| `renderHistoryList(el, items, onView)` | 左侧历史列表渲染 + 点击回看（渲染 `result_data` 的 markdown） |
| `appendChatMessage(el, role, content, {streaming})` | 聊天气泡；`streaming=true` 注入三点打字指示器（pitfalls #7）；用户消息转义、AI 消息 `marked.parse` |
| `renderMarkdownStream(el, acc)` | delta 追加 → `marked.parse(acc)` 整体重渲（实现最简、体验一致） |

### 7.4 marked.js

- 4 个新页面与 index.html 同款 CDN 引入：`https://cdn.jsdelivr.net/npm/marked/marked.min.js`
- AI 输出（受控内容）走 `marked.parse`；**用户输入一律先 HTML 转义**再入气泡（技能 flask-vanilla 前端同策略，防注入）

## 8. ★ 核心测试方案（MOCK_COZE=true）★

### 8.1 Mock 设计（`coze_client.py`）

- `MOCK_COZE=true` 时**不创建 Coze 实例、不发任何真实请求**
- 工作流 Mock 生成器：按技能事件顺序 yield 假增量（如评估报告四节文本分 8 段 delta）→ 循环结束发 `done {}`
- Bot Mock：yield 数段 delta → `done {"conversation_id": "mock-conv-<uid>-<n>"}`（回传后可验证多轮回传）
- 知识库闭环 Mock：跳过 SDK 调用，直接按状态机落库 `success`（上传接口接收文件并存盘）
- 事件格式与真实路径**完全一致**，保证测试断言对真实链路同样有效

### 8.2 后端自动化 `test_backend.py`（pytest + httpx，SSE 断言严格对照技能事件定义）

- **前置**：`conftest.py` 在导入 app 前设置 `MOCK_COZE=true`、`DB_PATH` 指向临时测试库（不污染 app.db）；fixture 完成"注册→登录→带 Bearer 的 client"
- **断言清单**：
  1. 三个工作流 SSE：响应 `content-type == text/event-stream`；流中**必须出现 `event: delta` 行与 `event: done` 行**（技能工作流协议：done 的 data 为 `{}`）；无 `event: error`；流结束后对应历史表 +1 条（GET history 断言）
  2. Bot SSE（`/api/interview/chat`）：必须含 `event: delta`、`event: done`；**done 的 data 必须含 `conversation_id` 键**（Bot 协议独有，与工作流区分）；第二次请求回传该 cid → 200
  3. 无 token / 假 token 调 SSE 接口 → 401 JSON（鉴权在流开启前）
  4. 上传接口：构造小文件上传 → 200 返回 file_name/file_path；非法 kind → 4xx
  5. `user_knowledge_map` CRUD：POST /api/knowledge/resume（.doc）→ sync_status=success；GET /api/knowledge 字段断言；POST questions（.md）→ success；DELETE → 恢复 none/空
  6. 完整性回归：阶段一/二接口抽查（登录、profile-status）不回归

### 8.3 前端 SSE 调试页 `frontend/test_sse.html`（独立、不进导航）

- **区 A 工作流模拟**：内置假事件序列（setTimeout 逐个发射 delta/done，走与生产相同的 `core/sse.js` 解析路径之外再叠加纯前端模拟模式），验证打字机、Markdown 渲染（含标题/表格/列表）、完成态
- **区 B Bot 聊天模拟**：模拟三轮问答 + conversation_id 展示，验证气泡/打字指示器/Markdown
- **区 C 真实后端联调**：填地址+token，直连 `MOCK_COZE=true` 的后端端点，原始事件日志面板逐行打印 `event:`/`data:`，供人工对照技能协议核对
- 复用 `core/sse.js`、`core/ui.js`、`core/toast.js`，确保"调试通过 = 生产可用"

### 8.4 端到端人工验收步骤（真实 Coze 配置后）

1. `.env` 填入 `pat_` 令牌与 4 个资源 ID，`MOCK_COZE=false` 重启
2. **模块 7**：上传 `test_files/docs/test.docx` → 右侧流式输出四节报告 → 刷新页面 → 左侧历史可回看
3. **模块 8**：上传 `test_files/audio/test.mp3` → 流式四节分析 → 历史回看
4. **模块 9**：粘贴/上传修改后简历 → 流式输出 5+ 题（含考察点）→ 历史可查
5. **模块 10**：知识库区上传最终简历(.doc)与面试题(.md) → 两个徽标变"成功" → 发消息开始面试 → AI 依据知识库提问/评价/追问 → 多轮上下文连贯 → "清空聊天记录"后新会话重新开始
6. 断网/错误令牌场景：页面出现 error 事件 Toast，无整页崩溃

## 9. 验收标准汇总

| 模块 | 必须（P0） | 增强（P2） |
|---|---|---|
| 7 简历评估 | doc/docx 上传、流式四节报告、历史回看 | — |
| 8 录音分析 | mp3/wav 上传、流式四节报告、历史回看 | — |
| 9 面试题生成 | 文本/文件双入口、≥5 题含考察点、历史落库 | 导出 .md 一键入知识库 |
| 10 模拟面试 | 知识库闭环三步、同步状态展示、流式多轮聊天、Markdown 渲染、前端清空 | 同步中动效、从历史一键选用 |
| 工程 | test_backend.py 全绿、test_sse.html 三区可用、SSE 事件与技能协议逐字一致 | — |

## 10. 已知限制与风险

1. **Bot dataset_ids 全局覆盖**：多用户先后绑定互相覆盖（3.4 节），本期单用户演示定位
2. **工作流入参名**：以 Coze 实际编排为准，实现期核对一次 config 映射
3. **SDK dataset/bots 命名空间**：技能文档未覆盖知识库 API，按 Coze 官方 SDK 文档实现，Mock 层隔离风险
4. **Coze 文件上传大小/格式限制**：以平台规则为准，前端做前置校验（扩展名+大小）
5. **Token 额度**：真实调用消耗 Coze 资源，测试期一律 `MOCK_COZE=true`

## 11. 实施顺序（确认 PRD 后执行）

1. 后端基座：config/.env 扩展 → db.py 加表 → coze_client.py（单例+Mock）→ sse.py（_sse/清洗/两个流生成器）
2. 路由：files.py → ai.py（模块 7/8/9）→ interview.py（Bot + 知识库）→ app.py 注册
3. `test_backend.py` + `conftest.py` 全绿（MOCK）
4. 前端：core/ 迁移与新增（sse.js/ui.js）→ 4 页面 HTML + pages/*.js → index.html 入口卡片激活
5. `test_sse.html` 调试页 + 人工端到端（先 MOCK 后真实）
