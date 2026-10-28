"""用户模块的请求/响应模型（pydantic 校验）"""
import re
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

ID_CARD_RE = re.compile(r"\d{17}[\dXx]")
PHONE_RE = re.compile(r"1[3-9]\d{9}")


def _normalize_id_card(v: str) -> str:
    v = (v or "").strip()
    if not ID_CARD_RE.fullmatch(v):
        raise ValueError("身份证号格式不正确（应为 18 位）")
    return v.upper()  # 统一存大写 X，避免忘记密码校验时大小写不一致


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")


def _empty_to_none(v) -> Optional[str]:
    """空串/空白统一为 None（"未填写"语义，注册场景用）"""
    if isinstance(v, str):
        v = v.strip()
    return v or None


def _normalize_contact(v, pattern, err_msg) -> Optional[str]:
    """联系方式通用校验：None 原样返回；空串原样保留（清空语义）；非空则校验格式"""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
    if v and not pattern.fullmatch(v):
        raise ValueError(err_msg)
    return v


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=20, description="用户名（3~20 字符，注册后不可修改）")
    password: str = Field(..., min_length=6, max_length=64, description="密码（至少 6 位）")
    id_card: str = Field(..., description="18 位身份证号（唯一，找回密码凭证）")
    phone: Optional[str] = Field(None, description="手机号，选填")
    email: Optional[EmailStr] = Field(None, description="邮箱，选填")

    @field_validator("username")
    @classmethod
    def clean_username(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("用户名不能为空")
        return v

    @field_validator("id_card")
    @classmethod
    def clean_id_card(cls, v):
        return _normalize_id_card(v)

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, v):
        return _normalize_contact(v, PHONE_RE, "手机号格式不正确（应为 11 位大陆手机号）")

    @field_validator("email", mode="before")
    @classmethod
    def clean_email(cls, v):
        return _empty_to_none(v)  # 注册时留空 = 未填写，非空格式交给 EmailStr 校验


class LoginRequest(BaseModel):
    username: str
    password: str


class ForgotPasswordRequest(BaseModel):
    username: str
    id_card: str
    new_password: str = Field(..., min_length=6, max_length=64, description="新密码（至少 6 位）")

    @field_validator("id_card")
    @classmethod
    def clean_id_card(cls, v):
        return _normalize_id_card(v)


class UserUpdateRequest(BaseModel):
    """仅允许修改手机号 / 邮箱（username 不可修改，id_card 为找回密码凭证不开放修改）。

    字段语义（配合路由中的 model_dump(exclude_unset=True)）：
    - 未传 / 传 null → 不修改该字段
    - 传空字符串 ""  → 清空该字段（用空串覆盖数据库旧值）
    - 传非空值       → 更新为新值
    """
    phone: Optional[str] = Field(None, description='新手机号；传 "" 表示清空')
    email: Optional[str] = Field(None, description='新邮箱；传 "" 表示清空')

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, v):
        return _normalize_contact(v, PHONE_RE, "手机号格式不正确（应为 11 位大陆手机号）")

    @field_validator("email", mode="before")
    @classmethod
    def clean_email(cls, v):
        return _normalize_contact(v, EMAIL_RE, "邮箱格式不正确")


class ChangePasswordRequest(BaseModel):
    """登录状态下修改密码（校验旧密码，区别于忘记密码的身份证校验）"""
    old_password: str = Field(..., description="旧密码")
    new_password: str = Field(..., min_length=6, max_length=64, description="新密码（至少 6 位）")


class UserOut(BaseModel):
    """对外返回的用户信息（不含 password，响应自动脱敏）"""
    id: int
    username: str
    id_card: str
    phone: str
    email: str
    created_at: str


# ---------------------------------------------------------------------------
# 阶段三：AI 模块请求模型
# 刻意宽松（字段全部可选带默认值）：缺失字段不触发 422 JSON，
# 业务校验在路由/流内完成，并按技能规则 4 以 SSE error 事件返回。
# ---------------------------------------------------------------------------


class FileSourceRequest(BaseModel):
    """模块 7/8 工作流入参：file_name 为上传接口返回的服务端重命名文件名"""
    file_name: str = ""
    file_id: str = ""  # 可选透传；缺省时 Mock 给假 id、真实模式兜底重传


class QuestionsGenerateRequest(BaseModel):
    """模块 9 入参：resume_text（粘贴文本）或 file_name（上传的修改稿）二选一"""
    resume_text: str = ""
    file_name: str = ""
    file_id: str = ""


class InterviewChatRequest(BaseModel):
    """模块 10 Bot 聊天入参：conversation_id 多轮策略 A（首轮空，后续回传 done 下发的 id）"""
    message: str = ""
    conversation_id: str = ""
