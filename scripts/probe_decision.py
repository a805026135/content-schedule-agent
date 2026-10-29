"""探针：检验模型是否「照抄示例」，以及是否真的根据工具返回值调整下一步。

用法：.venv/Scripts/python.exe scripts/probe_decision.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent.llm import ALL_ACTIONS, DeepSeekLLMClient  # noqa: E402
from src.config import load_settings  # noqa: E402

SYSTEM = (
    "你是一个素材审核与发布排期助手。目标是找出待处理素材，形成无冲突、可确认的发布排期。\n"
    "硬约束：你只能保存草稿，不能发布；未经用户同意不得擅自改变硬性发布要求。"
)


def probe(client, label, conversation):
    action = client.decide(
        system_prompt=SYSTEM,
        conversation=conversation,
        allowed_actions=ALL_ACTIONS,
    )
    print(f"--- {label} ---")
    print(json.dumps(action.model_dump(), ensure_ascii=False, indent=2))
    return action


def main() -> int:
    client = DeepSeekLLMClient(load_settings())

    # 1) 冷启动：什么都不给
    probe(
        client,
        "冷启动 M04",
        [{"role": "user", "content": "待处理素材 M04，用户要求 15:00 发布。给出第一个动作。"}],
    )

    # 2) fetch 已返回：必需字段缺失
    probe(
        client,
        "已 fetch，M02 缺审核材料",
        [
            {"role": "user", "content": "待处理素材 M02，用户要求 20:00 发布。"},
            {"role": "assistant", "content": '{"action":"fetch_material","action_input":{"material_id":"M02"},"rationale":"读取素材元数据"}'},
            {"role": "user", "content": (
                "工具 fetch_material 返回：\n"
                '{"material_id":"M02","title":"达人肖像授权待办","missing_fields":'
                '["copyright.portrait","review_doc_ids"],"requested_slot":"20:00"}\n'
                "请选择下一个动作。"
            )},
        ],
    )

    # 3) 规则风险 + 时段已被占用
    probe(
        client,
        "M01 规则通过但 18:00 被占用",
        [
            {"role": "user", "content": "待处理素材 M01，用户要求 18:00 发布。"},
            {"role": "assistant", "content": '{"action":"fetch_material","action_input":{"material_id":"M01"},"rationale":"读取素材元数据"}'},
            {"role": "user", "content": (
                "工具 fetch_material 返回：\n"
                '{"material_id":"M01","title":"新品开箱短片","missing_fields":[],"requested_slot":"18:00"}\n'
                "请选择下一个动作。"
            )},
            {"role": "assistant", "content": '{"action":"check_rules","action_input":{"material_id":"M01"},"rationale":"检查审核规则"}'},
            {"role": "user", "content": (
                "工具 check_rules 返回：\n"
                '{"material_id":"M01","severity":"pass","hits":[]}\n'
                "请选择下一个动作。"
            )},
            {"role": "assistant", "content": '{"action":"query_slots","action_input":{"date":"2026-10-29","platform":"douyin"},"rationale":"查询可用时段"}'},
            {"role": "user", "content": (
                "工具 query_slots 返回：\n"
                '{"available":["19:00","21:00"],"occupied":["18:00","20:00"]}\n'
                "注意用户要求的是 18:00，但该时段已被占用。请选择下一个动作。"
            )},
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
