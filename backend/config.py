"""全局配置：从项目根目录 .env 读取"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# JWT 配置
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-do-not-use-in-prod")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "24"))

# Coze 配置（阶段三对接，遵循 coze-chat 技能规范）
COZE_TOKEN = os.getenv("COZE_TOKEN", "")  # pat_ 个人访问令牌；MOCK 模式可不填
COZE_SPACE_ID = os.getenv("COZE_SPACE_ID", "")
COZE_WORKFLOW_RESUME = os.getenv("COZE_WORKFLOW_RESUME", "")       # 模块 7 简历评估
COZE_WORKFLOW_QUESTIONS = os.getenv("COZE_WORKFLOW_QUESTIONS", "")  # 模块 9 面试题生成
COZE_WORKFLOW_AUDIO = os.getenv("COZE_WORKFLOW_AUDIO", "")         # 模块 8 录音分析
COZE_BOT_INTERVIEW = os.getenv("COZE_BOT_INTERVIEW", "")           # 模块 10 模拟面试 Bot
# MOCK_COZE=true：不创建 Coze 实例、不发真实请求（测试期默认；真实联调改为 false）
MOCK_COZE = os.getenv("MOCK_COZE", "true").strip().lower() in ("1", "true", "yes")

# SDK 超时（秒）。cozepy 默认「读 600s / 连接 5s」，连接阶段 5s 在国内网络下常不够，
# 这里显式抬高并允许用 .env 覆盖。读超时别小于工作流最长耗时。
COZE_TIMEOUT = float(os.getenv("COZE_TIMEOUT", "600"))
COZE_CONNECT_TIMEOUT = float(os.getenv("COZE_CONNECT_TIMEOUT", "20"))
# 国内版固定 api.coze.cn；如用国际版可改成 https://api.coze.com
COZE_BASE_URL = os.getenv("COZE_BASE_URL", "")

# 工作流入参变量名映射（PRD 3.2：以 Coze 工作流实际编排为准，实现期核对一次即可，不改业务代码）
WORKFLOW_INPUT_KEYS = {
    "resume": {"file": "resume_file"},                             # 模块 7：简历文件 file_id
    "audio": {"file": "audio_file"},                               # 模块 8：录音文件 file_id
    "questions": {"file": "resume_data", "text": "resume_text"},   # 模块 9：resume_data 须为 JSON 字符串
}
