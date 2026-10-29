"""提示词与 observation 组装。

一个容易被忽略的点：**工具返回必须以 user 消息的形式重新进入对话**。
如果工具结果只写进某个变量而不进 conversation，模型下一步根本看不到它，
所谓的"根据观察调整动作"就不成立。这里是保证 ReAct 真实性的关键位置。
"""

from __future__ import annotations

import json
from typing import Any

_BASE_SYSTEM_PROMPT = """你是内容团队的「素材审核与发布排期助手」。

## 目标
找出待处理素材的问题，产出一份**无冲突、可确认**的发布排期建议。

## 硬约束（违反即为严重缺陷）
1. 你只能保存**草稿**。本系统没有任何发布能力，不要声称内容已发布/已上线。
2. 未经用户同意，不得擅自改变用户提出的硬性发布要求（尤其是发布时间）。
   时段冲突时，你的职责是**建议替代时段并标记待确认**，不是替用户做决定。
3. 素材信息缺失时，不得自己编一个值填进去，必须向用户索取。
4. 工具失败就是失败，不得在总结里把失败的步骤说成成功。

## 动作集合
你可以选择以下动作之一。除 finish 外的动作名与下方工具签名一一对应，
**参数名必须严格照写**，不得自行发明字段。
"""


def build_system_prompt(tool_specs: list[dict[str, Any]]) -> str:
    """把工具签名拼进系统提示词。

    为什么不写死在提示词里：工具签名是代码，提示词是文本，两者一旦漂移，
    模型就会用不存在的参数调用工具。这里从 `ScheduleTools.describe()` 取，
    保证只有一处真源；一致性由 `test_tool_spec_matches_signature` 守护。
    """
    lines = [_BASE_SYSTEM_PROMPT]
    for spec in tool_specs:
        lines.append(f"- `{spec['name']}`：{spec['description']}")
        for pname, meta in spec["parameters"].items():
            flag = "必填" if meta["required"] else "可选"
            lines.append(f"    - `{pname}`（{meta['type']}，{flag}）：{meta['desc']}")

    lines.append("""
- `request_clarification`：向用户提出待确认项（用户无需实时在线）。
    参数：`question`（string，必填）写清楚需要用户补充或确认什么。
- `finish`：给出最终结论并结束。参数结构如下：
    - `answer`（string，必填）：给内容团队的结论说明
    - `audit_status`（string）：passed / pending_info / risky / blocked 之一
    - `risk_reasons`（string[]）：风险原因清单，没有则给空数组
    - `suggested_slot`（string 或 null）：建议的发布时段；若用户要求的时段可用，填该时段
    - `pending_confirmations`（string[]）：待用户确认的事项
    - `draft_status`（string）：saved / not_saved。**必须与实际工具结果一致**，
      一次 save_draft 都没成功过就写 not_saved

## 决策要点
- 不要一次把所有工具都调一遍；每一步根据上一步的返回决定下一步。
- 规则 severity=block 的素材不要为其排期；risk/warn 可以继续，但必须列入待确认项。
- 用户要求的时段被占用时，先从 query_slots 的 available 里挑一个作为**建议**，
  保存草稿时标注待确认，不要假装用户已经同意改期。
- 确信还有必要信息没拿到时，先 request_clarification。
- 若任务尚未解决但预算将耗尽，用 finish 如实说明未完成的原因，不要含糊带过。
""")
    return "\n".join(lines)


# 未注入工具签名时的默认提示词：供离线测试与文档引用使用。
SYSTEM_PROMPT = _BASE_SYSTEM_PROMPT


def build_initial_messages(state: dict[str, Any]) -> list[dict[str, str]]:
    """构造首轮对话。每次调用都应产出同一份内容，保证 trace 可复现。"""
    lines = [
        f"请求编号 request_id={state['request_id']}",
        f"待处理素材 material_id={state['material_id']}",
        f"用户要求：{state['user_requirement']}",
        "",
        "请开始：给出你的第一个动作。",
    ]
    return [{"role": "user", "content": "\n".join(lines)}]


def observation_message(result: Any) -> dict[str, str]:
    """把 ToolResult 转成喂给模型的 user 消息。

    这里额外附上「接下来可以做什么」的提示，但**不指定**该做什么 ——
    否则就变成了工具替模型决策。
    """
    body = result.summary
    return {
        "role": "user",
        "content": (
            f"【观察】第 n 步工具返回如下：\n{body}\n\n"
            "请根据以上返回决定下一步动作。"
            "若判定信息已足够，使用 finish 给出结论；否则选择下一个工具。"
        ),
    }


def assistant_message(action: Any) -> dict[str, str]:
    """把选中的动作回写进对话，让模型在多步中看到自己此前的选择。"""
    return {
        "role": "assistant",
        "content": json.dumps(
            {
                "rationale": action.rationale,
                "action": action.action,
                "action_input": action.action_input,
            },
            ensure_ascii=False,
        ),
    }
