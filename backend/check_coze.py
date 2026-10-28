"""Coze 连通性自检：一次跑完，定位是「令牌/权限/Space/资源 ID」哪一环出问题。

用法（项目根目录）：
    uv run python -m backend.check_coze

只读探测，不创建/删除任何远端资源；不打印令牌字符（仅长度与前缀布尔值）。
"""
import sys

from . import config
from .coze_client import describe_coze_error, get_coze

OK, FAIL, WARN = "[OK]  ", "[FAIL]", "[WARN]"


def check(step: str, fn, hint: str = "") -> tuple[bool, object]:
    try:
        result = fn()
    except Exception as exc:  # 单步失败不中断，继续后面的检查
        print(f"{FAIL} {step}")
        print(f"       {describe_coze_error(exc)}")
        if hint:
            print(f"       提示：{hint}")
        return False, None
    print(f"{OK} {step} -> {result}")
    return True, result


def main() -> int:
    print("=" * 68)
    print("Coze 连通性自检（只读）")
    print("=" * 68)
    print(f"MOCK_COZE      = {config.MOCK_COZE}")
    print(f"COZE_BASE_URL  = {config.COZE_BASE_URL or 'api.coze.cn（默认）'}")
    print(f"COZE_TOKEN     = 长度 {len(config.COZE_TOKEN)}，pat_ 前缀：{config.COZE_TOKEN.startswith('pat_')}")
    print(f"COZE_SPACE_ID  = {config.COZE_SPACE_ID or '（未配置）'}")
    print(f"超时           = 读 {config.COZE_TIMEOUT:.0f}s / 连接 {config.COZE_CONNECT_TIMEOUT:.0f}s")
    print("-" * 68)

    if config.MOCK_COZE:
        print("MOCK_COZE=true：不会发起真实请求。请把 .env 改为 MOCK_COZE=false 后再跑本脚本。")
        return 1

    coze = get_coze()

    # ① 令牌有效性：列工作空间是最轻量的鉴权探测（失败通常为 4100 authentication is invalid）
    ok, _ = check(
        "① 令牌鉴权 coze.workspaces.list()",
        lambda: f"{len(coze.workspaces.list())} 个工作空间",
        hint="先在 Coze 控制台 → 个人设置 → 访问令牌 重新生成 pat_ 令牌，再更新 .env 的 COZE_TOKEN",
    )
    if not ok:
        print("\n结论：令牌不可用，后续检查无意义，已提前结束。")
        return 2

    # ② Space 读权限 + 知识库读权限
    check(
        "② 列知识库 coze.datasets.list(space_id=...)",
        lambda: f"{len(coze.datasets.list(space_id=config.COZE_SPACE_ID))} 个数据集",
        hint="确认 COZE_SPACE_ID 属于该令牌所在账号/空间，且有知识库读权限",
    )

    # ③ Bot 可读性（知识库绑定时走的是 bots.update，先用 retrieve 验证资源 ID 与权限）
    if config.COZE_BOT_INTERVIEW:
        check(
            "③ 读 Bot coze.bots.retrieve(bot_id=...)",
            lambda: coze.bots.retrieve(bot_id=config.COZE_BOT_INTERVIEW).name,
            hint="核对 COZE_BOT_INTERVIEW 是否为当前空间下的 Bot ID",
        )
    else:
        print(f"{WARN} 未配置 COZE_BOT_INTERVIEW，跳过 Bot 检查")

    # ④ 工作流存在性（工作流 API 无检索接口，缺失时会在真正调用时由后端明确报错）
    for label, wid in (("简历评估", config.COZE_WORKFLOW_RESUME),
                       ("录音分析", config.COZE_WORKFLOW_AUDIO),
                       ("面试题生成", config.COZE_WORKFLOW_QUESTIONS)):
        print(f"{WARN if not wid else OK} 工作流[{label}] 已配置：{wid or '（未配置）'}")

    print("-" * 68)
    print("自检完成。① 全绿说明令牌有效，502 需回到后端日志看具体步骤的 code/msg。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
