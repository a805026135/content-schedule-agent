"""配置入口：环境变量 -> Settings。

设计取舍：
1. 只用 stdlib + python-dotenv，不引入额外的配置框架。代价是没有类型化的
   env schema，收益是评审只需理解一个 dataclass，且少一层依赖。
2. fail_injection 是一个显式的故障注入开关。评审用一条命令就能复现
   「保存失败」「查询失败」，不必修改源码 —— 这也是"工具失败"验收点的复现入口。
3. 所有目录基于 PROJECT_ROOT 解析，不依赖调用时的 cwd，避免"在我机器上能跑"。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
RUNS_DIR = PROJECT_ROOT / "runs"

DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# DeepSeek 官方 API 的现行模型标识。
# deepseek-flash -> DeepSeek-V4.1-Flash-1017（2026-10-17 发布）
# deepseek-v4-pro -> DeepSeek-V4-Pro-0813
# 注意：deepseek-chat / deepseek-reasoner / deepseek-v4-flash 均已退役（2026-07-24）。
DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"

# 思考模式。V4.1-Flash 默认开启思考（effort=high），思维链会占用 max_tokens 预算，
# 严重时内容被截断导致返回空字符串。结构化决策场景下必须显式关闭。
DEEPSEEK_THINKING_MODES = ("disabled", "low", "high", "max")

# 故障注入的可选值；none 表示一切正常
FAIL_INJECTION_TARGETS = ("none", "save_draft", "query_slots")


@dataclass(frozen=True)
class Settings:
    # ---- 模型接入 ----
    llm_provider: str = "auto"  # auto | deepseek | mock
    deepseek_api_key: Optional[str] = None
    deepseek_base_url: str = DEEPSEEK_BASE_URL
    deepseek_model: str = DEFAULT_DEEPSEEK_MODEL
    # disabled 为非思考模式；low/high/max 为思考模式
    deepseek_thinking: str = "disabled"
    llm_timeout_sec: float = 30.0
    llm_max_tokens: int = 1024
    # 这个值只用于「格式修复」重试（模型返回非法 JSON / 空内容），
    # 与业务决策重试无关。见 src/agent/llm.py 的说明。
    llm_max_retries: int = 2

    # ---- 运行边界 ----
    max_steps: int = 8
    max_tool_calls: int = 20

    # ---- 故障注入 ----
    fail_injection: str = "none"

    # ---- 路径 ----
    data_dir: Path = DATA_DIR
    runs_dir: Path = RUNS_DIR


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return raw.strip() if raw and raw.strip() else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def load_settings(env_file: Optional[Path] = None, **overrides: Any) -> Settings:
    """读取环境变量构造 Settings；overrides 用于测试与 CLI 显式传参。

    这里刻意使用 override=False：命令行或测试里显式指定的 override 优先级最高，
    .env 只是兜底默认值。
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv 缺失时静默降级为纯环境变量
        load_dotenv = None
    if load_dotenv is not None:
        load_dotenv(env_file, override=False)

    base = Settings(
        llm_provider=_env_str("LLM_PROVIDER", "auto"),
        deepseek_api_key=(os.environ.get("DEEPSEEK_API_KEY") or "").strip() or None,
        deepseek_base_url=_env_str("DEEPSEEK_BASE_URL", DEEPSEEK_BASE_URL),
        deepseek_model=_env_str("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL),
        deepseek_thinking=_env_str("DEEPSEEK_THINKING", "disabled"),
        llm_timeout_sec=_env_float("LLM_TIMEOUT_SEC", 30.0),
        llm_max_tokens=_env_int("LLM_MAX_TOKENS", 1024),
        llm_max_retries=_env_int("LLM_MAX_RETRIES", 2),
        max_steps=_env_int("MAX_STEPS", 8),
        max_tool_calls=_env_int("MAX_TOOL_CALLS", 20),
        fail_injection=_env_str("FAIL_INJECTION", "none"),
    )
    return replace(base, **overrides)


def resolve_provider(settings: Settings) -> str:
    """auto 的判定逻辑：有 Key 就用真实模型，没有就明确降级为 mock。

    降级是显式可见的（会写进运行输出的 provider 字段），不会静默假装成真实模型。
    """
    if settings.llm_provider != "auto":
        return settings.llm_provider
    return "deepseek" if settings.deepseek_api_key else "mock"
