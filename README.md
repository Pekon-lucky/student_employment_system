# 学生就业帮助系统

技术栈：原生 HTML/CSS/JS (ES6 modules) + Python FastAPI + uv + 原生 sqlite3（无 ORM）。

## 启动

```bash
uv sync                                  # 安装依赖（已有 .venv 可跳过）
uv run uvicorn backend.app:app --reload  # 启动后端
```

- 接口文档（Swagger UI）：http://127.0.0.1:8000/docs
- 前端页面：http://127.0.0.1:8000/（未登录会自动跳到 /login.html；请勿直接双击 HTML 文件打开，ES6 modules 需要 http 服务）

首次启动会在项目根目录自动创建 `app.db` 并建表。

## 目录结构

```
├── backend/
│   ├── app.py           # FastAPI 入口：CORS、lifespan 建表、路由注册
│   ├── config.py        # .env 配置读取（JWT / Coze 预留）
│   ├── db.py            # sqlite3 连接管理 + dict_factory + 建表 SQL
│   ├── security.py      # bcrypt 密码哈希 + JWT 生成校验 + 鉴权依赖
│   ├── schemas.py       # pydantic 请求/响应模型
│   ├── coze_client.py   # Coze 客户端（阶段二，遵循 coze-chat 技能规范）
│   └── routers/
│       └── users.py     # 用户模块接口
├── frontend/            # 原生前端：5 个页面 + css/style.css + js/api.js、auth.js、main.js、profile.js
├── test_files/
│   ├── docs/            # 存放简历
│   └── audio/           # 存放录音
└── app.db               # SQLite 数据库（自动创建，不入库）
```

## 阶段一接口

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| POST | `/api/users/register` | 注册（username / id_card 唯一，phone/email 选填） | 否 |
| POST | `/api/users/login` | 登录，返回 JWT + 用户信息 + 完整性 | 否 |
| POST | `/api/users/forgot-password` | 用户名 + 身份证校验后重置密码 | 否 |
| GET | `/api/users/me` | 获取当前用户信息 | 是 |
| PUT | `/api/users/me` | 修改手机号 / 邮箱（username 不可改） | 是 |
| PUT | `/api/users/password` | 修改密码（校验旧密码） | 是 |
| GET | `/api/users/profile-status` | 信息完整性检查（所有字段非空） | 是 |

受保护接口在 Swagger 授权按钮中粘贴 `Bearer <token>` 即可测试。

## 配置

复制 `.env.example` 为 `.env`：修改 `JWT_SECRET`；`COZE_TOKEN` / `COZE_BOT_ID` 在阶段二对接 Coze 时填写。

## 数据库

原生 sqlite3，无 ORM。`users` 表 + 三张历史记录表（`resume_evaluations` / `audio_analyses` / `interview_questions`，均含 `user_id` 外键、`input_data`、`result_data`、`created_at`），建表 SQL 见 `backend/db.py`。
