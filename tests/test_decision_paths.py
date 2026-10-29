"""决策路径与运行边界的测试。

用 ScriptedMockLLMClient 而不是真实模型，理由有两条：
1. 这些用例验证的是**编排层行为**（路由、预算、校正），与模型能力无关，
   用脚本替身才能把"输入脚本 → 期望输出"钉死。
2. 真实模型的输出有随机性，放在 CI 里会让用例偶发变红。

Mock 只证明流程正确；真实模型的决策能力由 examples/real_llm/ 下的 trace 证明。
两者刻意分开，互不充当对方的证据。

跑法：.venv/Scripts/python.exe -m pytest tests/test_decision_paths.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.graph import STATUS_EXCEEDED, STATUS_OK, ReActAgent
from src.agent.llm import ScriptedMockLLMClient
from src.config import Settings
from src.tools.schedule_tools import ScheduleTools


def _agent(script, *, isolated_dir, max_steps=8, max_tool_calls=20, fail_injection="none"):
    settings = Settings(max_steps=max_steps, max_tool_calls=max_tool_calls)
    tools = ScheduleTools(
        settings,
        drafts_path=Path(isolated_dir) / "drafts.json",
        fail_injection=fail_injection,
    )
    return ReActAgent(llm=ScriptedMockLLMClient(script), tools=tools, settings=settings)


def _run(agent, request_id="REQ-TEST", material="M01", **kw):
    return agent.run(
        request_id=request_id,
        material_id=material,
        user_requirement=kw.get("requirement", "请审核并给出排期建议"),
    )


CONFLICT_SCRIPT = [
    {"rationale": "先读取素材元数据", "action": "fetch_material", "action_input": {"material_id": "M01"}},
    {"rationale": "元数据完整，检查审核规则", "action": "check_rules", "action_input": {"material_id": "M01"}},
    {"rationale": "规则通过，需确认 18:00 是否可用", "action": "query_slots", "action_input": {}},
    {
        "rationale": "18:00 已被占用，改用 19:00 作为建议时段",
        "action": "save_draft",
        "action_input": {"request_id": "REQ-P1", "material_id": "M01", "slot": "19:00"},
    },
    {
        "rationale": "已给出建议时段并保存草稿，需用户确认后结束",
        "action": "finish",
        "action_input": {
            "answer": "18:00 被占用，建议改到 19:00，草稿已保存",
            "audit_status": "pending_info",
            "suggested_slot": "19:00",
            "pending_confirmations": ["是否接受改到 19:00"],
            "draft_status": "saved",
        },
    },
]

MISSING_INFO_SCRIPT = [
    {"rationale": "读取素材元数据", "action": "fetch_material", "action_input": {"material_id": "M02"}},
    {
        "rationale": "缺少 requested_slot，不得自行编一个，向用户索取",
        "action": "request_clarification",
        "action_input": {"question": "请补充期望的发布时间"},
    },
    {
        "rationale": "时段未知，无法保存草稿，如实结束",
        "action": "finish",
        "action_input": {
            "answer": "缺少发布时间要求，未保存草稿",
            "audit_status": "pending_info",
            "suggested_slot": None,
            "pending_confirmations": ["请补充期望的发布时间"],
            "draft_status": "not_saved",
        },
    },
]


# ---------------------------------------------------------------- 两条差异化路径

def test_two_scripts_produce_two_distinct_paths(isolated_dir):
    """同一套编排、同一套工具，不同输入脚本产出两条不同路径。"""
    path_a = _run(_agent(CONFLICT_SCRIPT, isolated_dir=isolated_dir), material="M01")
    path_b = _run(_agent(MISSING_INFO_SCRIPT, isolated_dir=isolated_dir), material="M02")

    actions_a = [t["action"] for t in path_a["trace"]]
    actions_b = [t["action"] for t in path_b["trace"]]

    assert actions_a == ["fetch_material", "check_rules", "query_slots", "save_draft"]
    assert actions_b == ["fetch_material", "request_clarification"]
    assert len(actions_a) != len(actions_b)

    assert path_a["suggested_slot"] == "19:00"
    assert path_a["draft_status"] == "saved"
    assert path_b["suggested_slot"] is None
    assert path_b["draft_status"] == "not_saved"


def test_trace_records_next_action(isolated_dir):
    """考核要求 trace 里体现"后续动作"，由 finalize 阶段回填。"""
    path = _run(_agent(CONFLICT_SCRIPT, isolated_dir=isolated_dir))
    pairs = [(t["action"], t.get("next_action")) for t in path["trace"]]
    assert pairs[0] == ("fetch_material", "check_rules")
    assert pairs[-1] == ("save_draft", "finish")


def test_trace_has_decision_rationale(isolated_dir):
    """每条记录都要有决策依据，但不得包含完整思维链。"""
    path = _run(_agent(CONFLICT_SCRIPT, isolated_dir=isolated_dir))
    for record in path["trace"]:
        assert record["rationale"].strip()
        assert record["observation_summary"].strip()
        assert len(record["rationale"]) <= 260


def test_observations_flow_back_into_conversation(isolated_dir):
    """工具结果必须进入 conversation，否则"观察影响下一步"无从谈起。"""
    agent = _agent(CONFLICT_SCRIPT, isolated_dir=isolated_dir)
    path = _run(agent)
    joined = json.dumps(path["conversation"], ensure_ascii=False)
    # query_slots 的返回必须出现在对话里，它是模型决定换 19:00 的依据来源
    assert "19:00" in joined
    assert "18:00" in joined


# ---------------------------------------------------------------- 运行边界

def test_step_budget_returns_explicit_unfinished_state(isolated_dir):
    """【要求】达到上限必须返回明确的未完成状态。"""
    looping = [
        {"rationale": "反复重试", "action": "query_slots", "action_input": {}}
        for _ in range(50)
    ]
    path = _run(_agent(looping, isolated_dir=isolated_dir, max_steps=3))
    assert path["task_status"] == STATUS_EXCEEDED
    assert path["draft_status"] == "not_saved"
    assert "未完成" in path["final_answer"]
    assert path["step_count"] <= 4


def test_tool_call_budget_is_enforced(isolated_dir):
    looping = [
        {"rationale": "反复重试", "action": "query_slots", "action_input": {}}
        for _ in range(50)
    ]
    path = _run(_agent(looping, isolated_dir=isolated_dir, max_steps=99, max_tool_calls=2))
    assert path["task_status"] == STATUS_EXCEEDED
    assert path["tool_call_count"] <= 3


def test_recursion_limit_never_triggers_before_own_counter(isolated_dir):
    """自维护计数器应当先于 LangGraph 的 recursion_limit 生效。

    否则会抛 GraphRecursionError，而不是返回我们定义的未完成状态。
    """
    looping = [
        {"rationale": "反复重试", "action": "query_slots", "action_input": {}}
        for _ in range(200)
    ]
    agent = _agent(looping, isolated_dir=isolated_dir, max_steps=6)
    assert agent.recursion_limit >= 6 * 2
    path = _run(agent)
    assert path["task_status"] == STATUS_EXCEEDED


# ---------------------------------------------------------------- 关键缺陷防护

def test_no_publish_tool_and_no_publish_wording(isolated_dir):
    """【关键缺陷】没有任何发布入口。"""
    tools = ScheduleTools(Settings(), drafts_path=Path(isolated_dir) / "d.json")
    assert "publish" not in tools.registry()


def test_falsely_claimed_save_is_corrected(isolated_dir):
    """【关键缺陷】模型声称已保存但没有成功的 save 调用时，必须被纠正。"""
    script = [
        {"rationale": "读素材", "action": "fetch_material", "action_input": {"material_id": "M04"}},
        {
            "rationale": "直接收尾",
            "action": "finish",
            "action_input": {"answer": "已保存草稿", "draft_status": "saved"},
        },
    ]
    path = _run(_agent(script, isolated_dir=isolated_dir))
    assert path["draft_status"] == "not_saved"
    assert any("强制记为未保存" in f["reason"] for f in path["failures"])


def test_publish_wording_in_final_answer_is_flagged(isolated_dir):
    """【关键缺陷】最终答复里出现"已发布"类措辞必须被记下来。"""
    script = [
        {"rationale": "读素材", "action": "fetch_material", "action_input": {"material_id": "M04"}},
        {
            "rationale": "收尾",
            "action": "finish",
            "action_input": {"answer": "内容已发布，无需再确认", "draft_status": "not_saved"},
        },
    ]
    path = _run(_agent(script, isolated_dir=isolated_dir))
    assert any("发布类措辞" in f["reason"] for f in path["failures"])


def test_save_failure_is_reported_not_concealed(isolated_dir):
    script = [
        {"rationale": "读素材", "action": "fetch_material", "action_input": {"material_id": "M04"}},
        {
            "rationale": "保存草稿",
            "action": "save_draft",
            "action_input": {"request_id": "REQ-F", "material_id": "M04", "slot": "15:00"},
        },
        {
            "rationale": "收尾",
            "action": "finish",
            "action_input": {"answer": "保存失败", "draft_status": "not_saved"},
        },
    ]
    path = _run(
        _agent(script, isolated_dir=isolated_dir, fail_injection="save_draft")
    )
    assert path["draft_status"] == "not_saved"
    assert any(f["stage"] == "save_draft" for f in path["failures"])
    assert path["failures"][0]["reason"].startswith("TOOL_UNAVAILABLE")


def test_unknown_tool_does_not_crash_the_loop(isolated_dir):
    """模型可能幻觉出不存在的工具名，编排层要如实报错并让它有机会改。"""
    script = [
        {"rationale": "幻觉工具", "action": "publish_content", "action_input": {}},
        {
            "rationale": "改用真实工具",
            "action": "fetch_material",
            "action_input": {"material_id": "M04"},
        },
        {
            "rationale": "收尾",
            "action": "finish",
            "action_input": {"answer": "未知工具已拒绝", "draft_status": "not_saved"},
        },
    ]
    path = _run(_agent(script, isolated_dir=isolated_dir))
    assert path["trace"][0]["error_code"] == "UNKNOWN_TOOL"
    assert path["trace"][1]["action"] == "fetch_material"
    assert path["task_status"] == STATUS_OK


def test_duplicate_submission_creates_one_draft(isolated_dir):
    """【必验点】同一 request_id 跑两次，草稿总数保持 1。

    这里刻意**复用同一份草稿存储**但重建 LLM：脚本替身是一次性队列，
    跑完就没了，真实模型则不存在这个问题 —— 这个差异本身也说明了
    Mock 只能验证流程。
    """
    drafts = Path(isolated_dir) / "shared-drafts.json"
    settings = Settings()
    tools = ScheduleTools(settings, drafts_path=drafts)

    first = ReActAgent(llm=ScriptedMockLLMClient(list(CONFLICT_SCRIPT)), tools=tools, settings=settings).run(
        request_id="REQ-P1", material_id="M01", user_requirement="排期建议"
    )
    second = ReActAgent(llm=ScriptedMockLLMClient(list(CONFLICT_SCRIPT)), tools=tools, settings=settings).run(
        request_id="REQ-P1", material_id="M01", user_requirement="排期建议"
    )

    assert first["draft_id"] == second["draft_id"] == "DR-REQ-P1"
    assert second["total_drafts"] == 1
    assert second["duplicate"] is True


def test_model_unavailable_is_not_reported_as_decision_failure():
    """网络/鉴权故障必须报成「服务不可用」，不能被算到模型的决策能力头上。

    如果这里退化成"空返回 → 走格式修复重试"，一次网络抖动就会被伪装成
    "模型连续 N 次给不出合规动作"，把环境问题记到模型账上。
    """
    from src.agent.llm import DeepSeekLLMClient, LLMUnavailableError

    settings = Settings(deepseek_api_key="sk-test-not-a-real-key")
    client = DeepSeekLLMClient(settings)

    def _boom(_messages):
        raise ConnectionError("Connection error.")

    client._raw_completion = _boom  # type: ignore[method-assign]

    with pytest.raises(LLMUnavailableError, match="服务不可达或不可用"):
        client.decide(
            system_prompt="sys",
            conversation=[{"role": "user", "content": "hi"}],
            allowed_actions=["finish"],
        )
