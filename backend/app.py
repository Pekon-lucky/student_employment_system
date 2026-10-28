"""学生就业帮助系统 — 后端入口

启动: uv run uvicorn backend.app:app --reload
文档: http://127.0.0.1:8000/docs
前端: http://127.0.0.1:8000/（登录页 /login.html）
"""
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .db import init_db
from .routers import ai, files, interview, users

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# Windows 下 mimetypes 会从注册表读取 .js 的 MIME 类型，常被错误映射为 text/plain，
# 浏览器对 ES6 module script 强制校验 MIME，会导致模块脚本拒绝执行。
# 必须在 StaticFiles 挂载之前显式注册，覆盖注册表的影响。
mimetypes.add_type("application/javascript", ".js", strict=True)
mimetypes.add_type("application/javascript", ".mjs", strict=True)
mimetypes.add_type("text/css", ".css", strict=True)  # 顺带防御 .css 被注册表污染


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # 启动时自动建表
    yield


app = FastAPI(title="学生就业帮助系统", version="0.1.0", lifespan=lifespan)

# CORS 全开（对齐 coze-chat 技能模板，前端独立打开也能调用）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(users.router)
app.include_router(files.router)
app.include_router(ai.router)
app.include_router(interview.router)

# 托管前端静态页面（挂在最后：/docs 与 /api 路由优先匹配；html=True 使 "/" 返回 index.html）
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
