"""命令行入口（CLI）—— 也是评审的主要复现入口。

为什么选 CLI 而不是 Notebook / HTTP API：
一条命令可复制粘贴、"肉眼可见"，而且**退出码可以被断言**：
- 0 = 任务完成
- 2 = 达到步数/工具调用上限，属于明确的未完成状态
- 3 = 模型决策失败（连续多次无法给出合规动作）
- 4 = 模型服务不可用（网络不可达 / 鉴权失败 / 限流），属于环境问题而非决策问题

这让"未完成 ≠ 假装成功"这条要求变得机器可验证，而不是靠评审读文字判断。
4 与 3 分开的意义：网络故障不该被算到模型的决策能力头上。

用法示例见 README.md 与 examples/README.md。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .agent.graph import STATUS_DECISION_FAILED, STATUS_EXCEEDED, build_agent, state_to_json
from .agent.llm import (
    LLMClient,
    LLMDecisionError,
    LLMUnavailableError,
    ScriptedMockLLMClient,
    build_llm_client,
)
from .config import FAIL_INJECTION_TARGETS, Settings, load_settings
from .tools.schedule_tools import ScheduleTools

EXIT_OK = 0
EXIT_UNFINISHED = 2
EXIT_MODEL_FAILED = 3
EXIT_MODEL_UNAVAILABLE = 4
EXIT_OTHER = 1


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="content-schedule-agent",
        description="素材审核与发布排期 Agent（ReAct，仅保存草稿，不具备发布能力）",
    )
    p.add_argument("--material", required=True, help="素材 ID，如 M01")
    p.add_argument("--request-id", default=None, help="幂等键；缺省时按素材自动生成")
    p.add_argument(
        "--requirement",
        default="请审核该素材并给出发布排期建议",
        help="用户的排期要求原文",
    )
    p.add_argument(
        "--provider",
        choices=("auto", "deepseek", "mock"),
        default="auto",
        help="auto = 有 Key 用真实模型，无 Key 降级为 mock",
    )
    p.add_argument(
        "--mock-script",
        default=None,
        help="JSON 文件路径；指定后使用脚本化动作序列（离线复现用）",
    )
    p.add_argument(
        "--fail-injection",
        choices=FAIL_INJECTION_TARGETS,
        default=None,
        help="注入工具故障，用于复现「工具失败」场景",
    )
    p.add_argument("--max-steps", type=int, default=None, help="覆盖默认步数上限")
    p.add_argument(
        "--drafts-path",
        default=None,
        help="草稿存储文件路径；缺省用 runs/drafts.json。"
        "给每个示例单独指定可以避免多次运行互相干扰",
    )
    p.add_argument(
        "--out",
        default=None,
        help="把完整运行状态写入该文件（JSON）；缺省只打印到标准输出",
    )
    return p


def _make_llm(args: argparse.Namespace, settings: Settings) -> LLMClient:
    script: Optional[list[dict[str, Any]]] = None
    if args.mock_script:
        script = json.loads(Path(args.mock_script).read_text(encoding="utf-8"))
        return ScriptedMockLLMClient(script)
    return build_llm_client(settings, provider=args.provider)


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    overrides: dict[str, Any] = {}
    if args.fail_injection:
        overrides["fail_injection"] = args.fail_injection
    if args.max_steps is not None:
        overrides["max_steps"] = args.max_steps
    settings = load_settings(**overrides)

    request_id = args.request_id or f"REQ-{args.material}"

    try:
        llm = _make_llm(args, settings)
    except ValueError as exc:
        print(f"[启动失败] {exc}", file=sys.stderr)
        return EXIT_OTHER

    drafts = Path(args.drafts_path) if args.drafts_path else None
    tools = ScheduleTools(settings, drafts_path=drafts)
    agent = build_agent(llm=llm, tools=tools, settings=settings)

    started = datetime.now()
    try:
        state = agent.run(
            request_id=request_id,
            material_id=args.material,
            user_requirement=args.requirement,
        )
    except LLMUnavailableError as exc:
        print(
            f"[模型服务不可用] {exc}\n"
            "提示：这属于环境问题，不是模型决策失败。可检查 DEEPSEEK_API_KEY 与网络，\n"
            "或改用离线脚本复现流程：python scripts/run_examples.py --provider mock",
            file=sys.stderr,
        )
        return EXIT_MODEL_UNAVAILABLE
    except LLMDecisionError as exc:
        print(f"[模型决策失败] {exc}", file=sys.stderr)
        return EXIT_MODEL_FAILED

    payload = json.loads(state_to_json(state))
    payload["_meta"] = {
        "started_at": started.isoformat(timespec="seconds"),
        "elapsed_sec": round((datetime.now() - started).total_seconds(), 2),
        "provider": type(llm).__name__,
        "model": getattr(llm, "model_name", "unknown"),
        "fail_injection": settings.fail_injection,
        "max_steps": settings.max_steps,
        "max_tool_calls": settings.max_tool_calls,
    }

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"[已写入] {out}")
    print(text)

    if state["task_status"] == STATUS_EXCEEDED:
        return EXIT_UNFINISHED
    if state["task_status"] == STATUS_DECISION_FAILED:
        return EXIT_MODEL_FAILED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
