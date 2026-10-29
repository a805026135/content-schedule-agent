"""接入自检：确认 DeepSeek Key 可用、JSON Output 生效、思考模式已关闭。

用法（在项目根目录执行）：
    .venv/Scripts/python.exe scripts/check_llm.py

只在本地自检使用，不作为交付证据 —— 真正的决策证据来自 examples/real_llm/ 下的 trace。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent.llm import ALL_ACTIONS, DeepSeekLLMClient  # noqa: E402
from src.config import Settings, load_settings  # noqa: E402


def main() -> int:
    settings: Settings = load_settings()
    if not settings.deepseek_api_key:
        print("[FAIL] 未读取到 DEEPSEEK_API_KEY，检查 .env 是否存在且已填写")
        return 1

    print(f"[OK] 读取到 Key：{settings.deepseek_api_key[:8]}...{settings.deepseek_api_key[-4:]}")
    print(f"     model={settings.deepseek_model} thinking={settings.deepseek_thinking}")

    client = DeepSeekLLMClient(settings)
    system_prompt = (
        "你是一个素材审核与发布排期助手。你的目标是找出待处理素材，"
        "形成无冲突、可确认的发布排期。你只能保存草稿，绝不能执行发布动作。"
    )
    conversation = [
        {
            "role": "user",
            "content": (
                "待处理素材 M01，用户要求发布时间 18:00。"
                "你还没有读取任何素材信息，请给出第一个动作。"
            ),
        }
    ]

    action = client.decide(
        system_prompt=system_prompt,
        conversation=conversation,
        allowed_actions=ALL_ACTIONS,
    )
    print("[OK] 模型返回合规动作：")
    print(json.dumps(action.model_dump(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
