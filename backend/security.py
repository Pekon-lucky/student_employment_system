"""密码哈希、JWT 生成校验与鉴权依赖"""
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from . import config
from .db import get_db

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# auto_error=False：缺失 Token 时由自己抛 401（默认抛 403，不符合语义）
bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(raw: str) -> str:
    return pwd_context.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    return pwd_context.verify(raw, hashed)


def create_access_token(user_id: int, username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=config.JWT_EXPIRE_HOURS)
    payload = {"sub": str(user_id), "username": username, "exp": expire}
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def get_current_user(
    cred: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    """JWT 鉴权依赖：解码 token → 查库 → 返回用户 dict（含 password 字段，勿直接返回给前端）"""
    if cred is None:
        raise HTTPException(status_code=401, detail="未登录，请先获取 Token")
    try:
        payload = jwt.decode(
            cred.credentials, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM]
        )
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user
