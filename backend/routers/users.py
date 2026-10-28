"""用户模块路由：注册 / 登录 / 忘记密码 / 用户信息 / 完整性检查"""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..db import get_db
from ..schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    RegisterRequest,
    UserOut,
    UserUpdateRequest,
)
from ..security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api/users", tags=["用户模块"])

# 信息完整性判断需检查的所有字段（全部非空才算完整）
PROFILE_FIELDS = ("username", "password", "id_card", "phone", "email")


def check_complete(user: dict) -> dict:
    missing = [f for f in PROFILE_FIELDS if not user.get(f)]
    return {"complete": not missing, "missing": missing}


@router.post("/register", status_code=201, summary="注册")
def register(req: RegisterRequest, db: sqlite3.Connection = Depends(get_db)):
    # 唯一性预检查（数据库 UNIQUE 约束兜底）
    if db.execute("SELECT id FROM users WHERE username = ?", (req.username,)).fetchone():
        raise HTTPException(status_code=409, detail="用户名已被注册")
    if db.execute("SELECT id FROM users WHERE id_card = ?", (req.id_card,)).fetchone():
        raise HTTPException(status_code=409, detail="该身份证号已注册")
    cur = db.execute(
        "INSERT INTO users (username, password, id_card, phone, email) VALUES (?, ?, ?, ?, ?)",
        (req.username, hash_password(req.password), req.id_card, req.phone or "", req.email or ""),
    )
    db.commit()  # 写操作显式提交
    user = db.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    return {"message": "注册成功", "user": UserOut(**user)}


@router.post("/login", summary="登录")
def login(req: LoginRequest, db: sqlite3.Connection = Depends(get_db)):
    user = db.execute("SELECT * FROM users WHERE username = ?", (req.username,)).fetchone()
    # 统一提示"用户名或密码错误"，不区分两种情况，防账号探测
    if not user or not verify_password(req.password, user["password"]):
        raise HTTPException(status_code=400, detail="用户名或密码错误")
    return {
        "access_token": create_access_token(user["id"], user["username"]),
        "token_type": "bearer",
        "user": UserOut(**user),
        "profile_complete": check_complete(user),
    }


@router.post("/forgot-password", summary="忘记密码（用户名 + 身份证校验）")
def forgot_password(req: ForgotPasswordRequest, db: sqlite3.Connection = Depends(get_db)):
    user = db.execute(
        "SELECT * FROM users WHERE username = ? AND id_card = ?",
        (req.username, req.id_card),
    ).fetchone()
    if not user:
        raise HTTPException(status_code=400, detail="用户名与身份证号不匹配")
    db.execute(
        "UPDATE users SET password = ? WHERE id = ?",
        (hash_password(req.new_password), user["id"]),
    )
    db.commit()
    return {"message": "密码重置成功，请使用新密码登录"}


@router.get("/me", summary="获取当前用户信息")
def get_me(user: dict = Depends(get_current_user)):
    return {"user": UserOut(**user)}


@router.put("/me", summary="修改用户信息（手机号/邮箱，传空串即清空）")
def update_me(
    req: UserUpdateRequest,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    # exclude_unset：只取请求里真正传了的字段——"传了空串"是清空，"没传"是不修改
    updates = req.model_dump(exclude_unset=True)
    fields, params = [], []
    for field in ("phone", "email"):
        if field in updates and updates[field] is not None:  # null 防御：避免把 NOT NULL 列写成 NULL
            fields.append(f"{field} = ?")
            params.append(updates[field])  # 空串也覆盖旧值 → 实现真正的清空
    if not fields:
        raise HTTPException(status_code=400, detail="请至少提供 phone 或 email 中一项")
    params.append(user["id"])
    db.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", params)
    db.commit()
    fresh = db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
    return {"message": "修改成功", "user": UserOut(**fresh)}


@router.put("/password", summary="修改密码（登录态，校验旧密码）")
def change_password(
    req: ChangePasswordRequest,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    if not verify_password(req.old_password, user["password"]):
        raise HTTPException(status_code=400, detail="旧密码不正确")
    db.execute(
        "UPDATE users SET password = ? WHERE id = ?",
        (hash_password(req.new_password), user["id"]),
    )
    db.commit()
    return {"message": "密码修改成功"}


@router.get("/profile-status", summary="用户信息完整性检查（所有字段非空）")
def profile_status(user: dict = Depends(get_current_user)):
    return check_complete(user)
