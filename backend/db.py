"""SQLite 连接管理与建表（原生 sqlite3，无 ORM）"""
import os
import sqlite3
from pathlib import Path

_DEFAULT_DB = Path(__file__).resolve().parent.parent / "app.db"
# 测试可通过环境变量指向临时库（conftest.py 在导入 app 前设置，避免污染主库）
DB_PATH = Path(os.environ.get("JIYE_DB_PATH") or _DEFAULT_DB)

DDL = """
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT NOT NULL UNIQUE,               -- 唯一，注册后不可修改
    password   TEXT NOT NULL,                      -- bcrypt 哈希
    id_card    TEXT NOT NULL UNIQUE,               -- 18 位身份证号，唯一，找回密码凭证
    phone      TEXT NOT NULL DEFAULT '',
    email      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS resume_evaluations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    input_data  TEXT NOT NULL,                     -- JSON 文本（简历内容/文件信息）
    result_data TEXT NOT NULL,                     -- JSON 文本（AI 评估结果）
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audio_analyses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    input_data  TEXT NOT NULL,
    result_data TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS interview_questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    input_data  TEXT NOT NULL,
    result_data TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 模块 10：单用户动态知识库映射（PRD 5.2），每用户一行
CREATE TABLE IF NOT EXISTS user_knowledge_map (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id               INTEGER NOT NULL UNIQUE,       -- 每用户一行
    dataset_id            TEXT NOT NULL DEFAULT '',      -- 用户专属 Coze 数据集 ID
    resume_doc_id         TEXT NOT NULL DEFAULT '',      -- 简历文档 document_id
    resume_file_name      TEXT NOT NULL DEFAULT '',
    resume_sync_status    TEXT NOT NULL DEFAULT 'none',  -- none/syncing/success/failed
    questions_doc_id      TEXT NOT NULL DEFAULT '',      -- 面试题文档 document_id
    questions_file_name   TEXT NOT NULL DEFAULT '',
    questions_sync_status TEXT NOT NULL DEFAULT 'none',
    bot_bound             INTEGER NOT NULL DEFAULT 0,    -- Bot 是否已绑定该数据集
    updated_at            TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""


def dict_factory(cursor, row):
    """行工厂：让 sqlite3 查询结果直接返回 dict"""
    fields = [col[0] for col in cursor.description]
    return dict(zip(fields, row))


def get_connection() -> sqlite3.Connection:
    # check_same_thread=False 的原因：
    # FastAPI 把同步依赖（get_db）的建立和销毁分别交给 anyio 线程池执行，而线程池
    # 不保证两次调度到同一线程；并发时连接可能在 A 线程创建、在 B 线程关闭/使用，
    # 默认的 check_same_thread=True 会抛 ProgrammingError。
    # 安全性：连接是「每请求一个」，只在该请求内使用，绝不跨请求/跨线程并发共享，
    # 因此关闭线程归属检查不会引入竞争。
    conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    conn.row_factory = dict_factory
    conn.execute("PRAGMA foreign_keys = ON")  # 外键约束需每个连接单独开启
    return conn


def get_db():
    """FastAPI 依赖：每请求新建连接，用完即关。

    连接在本请求的线程池线程内创建并使用，天然规避 check_same_thread 报错。
    写操作由路由内显式 commit，这里只负责开关连接。
    """
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """应用启动时执行建表脚本"""
    with get_connection() as conn:
        conn.executescript(DDL)
