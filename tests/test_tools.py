"""业务工具层单元测试。

这些用例考察的是**没有任何模型参与**时的行为：给定输入，工具必须确定地返回
什么，且不得越权、不得虚报。工具层不可靠的话，上层 trace 再漂亮也没有意义。

跑法（项目根目录）：
    .venv/Scripts/python.exe -m pytest tests/test_tools.py -v
"""

from __future__ import annotations

import json

import pytest

from src.tools.schedule_tools import (
    ERR_IDEMPOTENCY_CONFLICT,
    ERR_MATERIAL_NOT_FOUND,
    ERR_SLOT_OCCUPIED,
    ERR_SLOT_RESTRICTED,
    ERR_SLOT_TAKEN_BY_DRAFT,
    ERR_TOOL_UNAVAILABLE,
    ERR_UNKNOWN_DATE,
    ERR_UNKNOWN_SLOT,
    ToolResult,
)


# ---------------------------------------------------------------- 结构性防线

def test_registry_contains_no_publish_capability(tools):
    """【关键缺陷防护】工具集里不得存在任何发布能力。

    这是"不得擅自发布"的**结构性**保证：不是提示词说不要发布，而是
    Agent 根本没有这个入口。任何形式的 publish / post / release 都应触发失败。
    """
    names = set(tools.registry().keys())
    assert names == {"fetch_material", "check_rules", "query_slots", "save_draft"}

    forbidden = ("publish", "post", "release", "commit", "send", "deploy", "push")
    offenders = {n for n in names for f in forbidden if f in n.lower()}
    assert offenders == set()


def test_draft_status_is_never_published(tools):
    """【关键缺陷防护】save_draft 落盘的状态只能是 draft。"""
    result = tools.save_draft(request_id="REQ-T01", material_id="M04", slot="15:00")
    assert result.ok
    store = json.loads((tools._drafts_path).read_text(encoding="utf-8"))
    assert store["REQ-T01"]["status"] == "draft"


# ---------------------------------------------------------------- fetch_material

def test_fetch_complete_material_has_no_missing_fields(tools):
    result = tools.fetch_material("M04")
    assert result.ok
    assert result.data["missing_fields"] == []
    assert result.data["field_complete"] is True
    assert result.data["requested_slot"] == "15:00"


def test_fetch_detects_missing_requested_slot(tools):
    """【必验点】素材信息缺失：M02 未提供发布时间要求。"""
    result = tools.fetch_material("M02")
    assert result.ok
    assert result.data["missing_fields"] == ["requested_slot"]
    assert result.data["field_complete"] is False
    # 工具不得替模型把缺失字段"猜"出来
    assert result.data["requested_slot"] is None


def test_fetch_unknown_material_reports_error(tools):
    result = tools.fetch_material("M99")
    assert not result.ok
    assert result.error_code == ERR_MATERIAL_NOT_FOUND
    assert "M99" in result.error_message


def test_fetch_rejects_empty_id(tools):
    result = tools.fetch_material("")
    assert not result.ok
    assert result.error_code == "INVALID_PARAMS"


# ---------------------------------------------------------------- check_rules

def test_check_rules_passes_for_clean_material(tools):
    result = tools.check_rules("M04")
    assert result.ok
    assert result.data["severity"] == "pass"
    assert result.data["hits"] == []
    assert result.data["blocked"] is False


def test_check_rules_reports_risk_and_warn(tools):
    """【必验点】规则风险：M03 命中 R04(risk) 与 R03(warn)，但不阻断。"""
    result = tools.check_rules("M03")
    assert result.ok
    assert result.data["severity"] == "risk"
    assert result.data["blocked"] is False
    rule_ids = {h["rule_id"] for h in result.data["hits"]}
    assert rule_ids == {"R03", "R04"}


def test_check_rules_blocks_unlicensed_music(tools_factory, blocked_material_data):
    """M05 不在 data/ 里，用内联 fixture 覆盖 severity=block 这一档。"""
    tools = tools_factory(data_dir=blocked_material_data)
    result = tools.check_rules("M05")
    assert result.ok
    assert result.data["severity"] == "block"
    assert result.data["blocked"] is True
    assert {h["rule_id"] for h in result.data["hits"]} == {"R01"}


def test_check_rules_propagates_missing_material(tools):
    result = tools.check_rules("M99")
    assert not result.ok
    assert result.error_code == ERR_MATERIAL_NOT_FOUND


def test_unknown_rule_operator_raises_instead_of_being_skipped(isolated_dir):
    """未知算子必须炸出来，而不是被静默跳过 —— 静默跳过会让规则形同虚设。"""
    import shutil

    from src.config import DATA_DIR, Settings
    from src.tools.schedule_tools import ScheduleTools

    data_dir = isolated_dir / "bad-rule"
    data_dir.mkdir()
    for name in ("materials.json", "schedule.json"):
        shutil.copy(DATA_DIR / name, data_dir / name)
    rules = json.loads((DATA_DIR / "rules.json").read_text(encoding="utf-8"))
    rules["rules"].append({
        "rule_id": "R99", "name": "配置错误", "severity": "block",
        "field": "title", "op": "__not_a_real_op__", "message": "配置错误",
    })
    (data_dir / "rules.json").write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")

    tools = ScheduleTools(Settings(), data_dir=data_dir, drafts_path=isolated_dir / "d.json")
    with pytest.raises(ValueError, match="未知规则算子"):
        tools.check_rules("M04")


# ---------------------------------------------------------------- query_slots

def test_query_slots_partitions_by_status(tools):
    result = tools.query_slots(date="2026-10-30")
    assert result.ok
    assert "19:00" in result.data["available"]
    assert "18:00" not in result.data["available"]
    assert any(o["slot"] == "18:00" for o in result.data["occupied"])
    assert any(r["slot"] == "20:00" for r in result.data["restricted"])


def test_query_slots_rejects_unknown_date(tools):
    """诚实边界：数据集没有的日期不能假装可用。"""
    result = tools.query_slots(date="2099-01-01")
    assert not result.ok
    assert result.error_code == ERR_UNKNOWN_DATE


def test_query_slots_can_be_injected_to_fail(tools_factory):
    """【必验点】工具失败：一条开关复现排期服务不可用。"""
    tools = tools_factory(fail_injection="query_slots")
    result = tools.query_slots(date="2026-10-30")
    assert not result.ok
    assert result.error_code == ERR_TOOL_UNAVAILABLE


# ---------------------------------------------------------------- save_draft 幂等

def test_repeated_same_request_produces_one_draft(tools):
    """【必验点】重复提交：同 request_id 跑两次只能产生一份草稿。"""
    first = tools.save_draft(request_id="REQ-DUP", material_id="M04", slot="15:00")
    assert first.ok and first.data["duplicate"] is False

    second = tools.save_draft(request_id="REQ-DUP", material_id="M04", slot="15:00")
    assert second.ok
    assert second.data["duplicate"] is True
    assert second.data["draft_id"] == first.data["draft_id"]
    assert second.data["total_drafts"] == 1


def test_same_key_different_body_is_a_conflict_not_a_replay(tools):
    """同幂等键配不同内容必须报错，而不是重放旧响应。

    只存 key 不存 body 指纹的实现，会把这个请求静默重放成上一次的结果 ——
    那是比报错严重得多的错误。
    """
    tools.save_draft(request_id="REQ-CF", material_id="M04", slot="15:00")
    other = tools.save_draft(request_id="REQ-CF", material_id="M01", slot="19:00")
    assert not other.ok
    assert other.error_code == ERR_IDEMPOTENCY_CONFLICT


def test_different_requests_are_distinct_drafts(tools):
    a = tools.save_draft(request_id="REQ-A", material_id="M04", slot="15:00")
    b = tools.save_draft(request_id="REQ-B", material_id="M01", slot="19:00")
    assert a.data["draft_id"] != b.data["draft_id"]
    assert b.data["total_drafts"] == 2


# ---------------------------------------------------------------- save_draft 冲突时段

def test_cannot_save_into_occupied_slot(tools):
    """排期表标注被占用的时段，写操作必须拒绝 —— 无冲突是硬保证。"""
    result = tools.save_draft(request_id="REQ-OCC", material_id="M01", slot="18:00")
    assert not result.ok
    assert result.error_code == ERR_SLOT_OCCUPIED


def test_cannot_save_into_restricted_slot(tools):
    result = tools.save_draft(request_id="REQ-RES", material_id="M01", slot="20:00")
    assert not result.ok
    assert result.error_code == ERR_SLOT_RESTRICTED


def test_cannot_save_into_slot_taken_by_another_draft(tools):
    tools.save_draft(request_id="REQ-EARLY", material_id="M04", slot="15:00")
    conflict = tools.save_draft(request_id="REQ-LATE", material_id="M01", slot="15:00")
    assert not conflict.ok
    assert conflict.error_code == ERR_SLOT_TAKEN_BY_DRAFT


def test_unknown_slot_is_rejected(tools):
    result = tools.save_draft(request_id="REQ-BAD", material_id="M04", slot="03:00")
    assert not result.ok
    assert result.error_code == ERR_UNKNOWN_SLOT


def test_failed_save_leaves_no_partial_state(tools, tools_factory):
    """失败时不得落盘，也不得留下半成品 —— 否则"未保存"就成了假话。"""
    result = tools.save_draft(request_id="REQ-OCC", material_id="M01", slot="18:00")
    assert not result.ok
    assert not tools._drafts_path.exists()


# ---------------------------------------------------------------- 故障注入

def test_save_draft_failure_is_reported_honestly(tools_factory):
    """【必验点】保存失败：必须明确返回失败，不得虚报成功。"""
    tools = tools_factory(fail_injection="save_draft")
    result = tools.save_draft(request_id="REQ-FAIL", material_id="M04", slot="15:00")
    assert not result.ok
    assert result.error_code == ERR_TOOL_UNAVAILABLE
    # 失败后草稿存储必须不存在或为空
    if tools._drafts_path.exists():
        assert tools._drafts_path.read_text(encoding="utf-8").strip() in ("", "{}")


def test_failure_injection_is_off_by_default(tools):
    assert tools._fail_injection == "none"


# ---------------------------------------------------------------- ToolResult 不变量

def test_tool_result_forbids_ok_with_error():
    with pytest.raises(ValueError):
        ToolResult("x", {}, True, error_code="BOOM")


def test_tool_result_requires_error_code_when_failed():
    with pytest.raises(ValueError):
        ToolResult("x", {}, False, error_message="没有给 code")


def test_tool_spec_matches_real_signature(tools):
    """工具文档的参数名必须与真实函数签名一致。

    为什么要这条测试：模型的参数名是从 `describe()` 生成的文本里抄的。
    改了函数签名却忘了改文档，模型就会用不存在的参数调用工具 ——
    这个 bug 不会报错崩掉，只会让每一步都静默失败，很难查。用测试来钉住它。
    """
    import inspect

    for spec in tools.describe():
        fn = tools.registry()[spec["name"]]
        real = set(inspect.signature(fn).parameters)
        documented = set(spec["parameters"])
        assert documented == real, (
            f"{spec['name']} 的参数文档与签名不一致："
            f"文档有 {documented - real}，签名有 {real - documented}"
        )


def test_tool_spec_covers_every_registered_tool(tools):
    assert {s["name"] for s in tools.describe()} == set(tools.registry())


def test_summary_is_compact_and_not_empty(tools):
    ok = tools.fetch_material("M04")
    bad = tools.fetch_material("M99")
    assert "M04" in ok.summary and "成功" in ok.summary
    assert "失败" in bad.summary and bad.error_code in bad.summary
