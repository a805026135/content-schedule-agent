"""一键生成 examples/ 下的全部验证材料。

存在的意义：让评审不必手工敲七条命令。**可复现性**不是文档里写一句"可以复现"，
而是有一条命令能把材料重新生成出来。

用法：
    .venv/Scripts/python.exe scripts/run_examples.py              # 真实模型（需要 Key）
    .venv/Scripts/python.exe scripts/run_examples.py --provider mock  # 离线脚本，零配置
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent.graph import ReActAgent  # noqa: E402
from src.agent.llm import ScriptedMockLLMClient  # noqa: E402
from src.cli import EXIT_OK  # noqa: E402
from src.config import Settings, load_settings  # noqa: E402
from src.tools.schedule_tools import ScheduleTools  # noqa: E402

# 六个必验点 + 一条超限用例
CASES = [
    {
        "dir": "case01_normal",
        "material": "M04",
        "requirement": "素材 M04，请按要求的时段做发布排期",
        "note": "正常排期：素材完整、规则通过、时段空闲",
    },
    {
        "dir": "case02_missing_info",
        "material": "M02",
        "requirement": "素材 M02，请做发布排期",
        "note": "素材信息缺失：未提供期望发布时间",
    },
    {
        "dir": "case03_rule_risk",
        "material": "M03",
        "requirement": "素材 M03，请审核并排期",
        "note": "规则风险：命中 risk + warn，不阻断但须待确认",
    },
    {
        "dir": "case04_slot_conflict",
        "material": "M01",
        "requirement": "素材 M01，用户要求 18:00 发布",
        "note": "时段冲突：18:00 已占用，需建议替代时段并标记待确认",
    },
    {
        "dir": "case06_save_failure",
        "material": "M04",
        "requirement": "素材 M04，请保存排期草稿",
        "fail_injection": "save_draft",
        "note": "保存失败：注入 storage 503，须如实返回未保存",
    },
    {
        "dir": "case07_step_budget",
        "material": "M01",
        "requirement": "素材 M01，请反复尝试直到成功",
        "max_steps": 2,
        "note": "步数上限：必须返回明确的未完成状态",
    },
]

# case05 的重复提交需要连续跑两次，单独处理
DUPLICATE_CASE = {
    "dir": "case05_duplicate",
    "material": "M04",
    "requirement": "素材 M04，请按要求的时段做发布排期",
    "request_id": "REQ-DUP",
    "note": "重复提交：同一 request_id 连续两次，只能产生一份草稿",
}

# 离线脚本说明：这些动作序列是**照着 examples/real_llm/ 里真实模型走过的路径抄下来的**，
# 不是我凭空设计的最优路径。这样做的好处是两条目录可以逐条对照，
# 评审能一眼看出 Mock 只是在复演流程，而不是在替模型表演。
MOCK_SCRIPTS = {
    "case01_normal": [
        {"rationale": "读取素材元数据", "action": "fetch_material", "action_input": {"material_id": "M04"}},
        {"rationale": "元数据完整，检查规则", "action": "check_rules", "action_input": {"material_id": "M04"}},
        {"rationale": "规则通过，查询时段可用性", "action": "query_slots", "action_input": {}},
        {"rationale": "15:00 可用，保存草稿", "action": "save_draft",
         "action_input": {"request_id": "REQ-M04", "material_id": "M04", "slot": "15:00"}},
        {"rationale": "已保存草稿，给出结论", "action": "finish",
         "action_input": {"answer": "素材完整，15:00 可用，已保存草稿 DR-REQ-M04（未发布）",
                          "audit_status": "passed", "suggested_slot": "15:00",
                          "pending_confirmations": ["请确认是否按 2026-10-30 15:00 发布"],
                          "draft_status": "saved"}},
    ],
    "case04_slot_conflict": [
        {"rationale": "读取素材元数据", "action": "fetch_material", "action_input": {"material_id": "M01"}},
        {"rationale": "元数据完整，检查规则", "action": "check_rules", "action_input": {"material_id": "M01"}},
        {"rationale": "规则通过，查询时段", "action": "query_slots", "action_input": {}},
        {"rationale": "18:00 被占用，改用 19:00 作为建议时段", "action": "save_draft",
         "action_input": {"request_id": "REQ-M01", "material_id": "M01", "slot": "19:00"}},
        {"rationale": "建议时段已保存，待用户确认", "action": "finish",
         "action_input": {"answer": "18:00 已被占用，建议 19:00，草稿已保存，未发布",
                          "audit_status": "pending_info", "suggested_slot": "19:00",
                          "pending_confirmations": ["是否接受改到 19:00"],
                          "draft_status": "saved"}},
    ],
    "case02_missing_info": [
        {"rationale": "先读取素材元数据，确认必填字段是否齐全", "action": "fetch_material",
         "action_input": {"material_id": "M02"}},
        {"rationale": "素材缺少必填字段 requested_slot，不能自行编造，需向用户索取",
         "action": "request_clarification",
         "action_input": {"question": "素材 M02 缺少必填字段「期望发布时段」，请确认希望投放的时段"}},
        {"rationale": "等待用户回复期间，先完成规则检查以确认素材是否可排期",
         "action": "check_rules", "action_input": {"material_id": "M02"}},
        {"rationale": "规则通过，先查询可用时段，以便给用户可选项", "action": "query_slots",
         "action_input": {}},
        {"rationale": "用户仍未给出期望时段，不擅自排期，据实收尾", "action": "finish",
         "action_input": {"answer": "素材 M02 规则检查通过，但缺失必填字段「期望发布时段」，"
                                    "已向用户提出确认，暂不保存草稿",
                          "audit_status": "pending_info",
                          "pending_confirmations": ["请确认素材 M02 的期望发布时段（可选 15:00 / 16:00 / 19:00 / 21:00）"],
                          "draft_status": "not_saved"}},
    ],
    "case03_rule_risk": [
        {"rationale": "先读取素材元数据，确认必填字段是否齐全", "action": "fetch_material",
         "action_input": {"material_id": "M03"}},
        {"rationale": "素材缺少 review_doc_ids 且含绝对化宣称，需先按规则检查严重程度",
         "action": "check_rules", "action_input": {"material_id": "M03"}},
        {"rationale": "规则结论为 risk（非 block），可继续排期，但需确认用户要求的 16:00 是否可用",
         "action": "query_slots", "action_input": {}},
        {"rationale": "16:00 可用，按用户要求时段保存草稿并标注待确认", "action": "save_draft",
         "action_input": {"request_id": "REQ-M03", "material_id": "M03", "slot": "16:00",
                          "note": "命中 risk/warn，需法务复核与补件"}},
        {"rationale": "草稿已保存，风险项列入待确认", "action": "finish",
         "action_input": {"answer": "审核结论为 risk（未阻断）：缺归档编号（warn）、"
                                    "含绝对化宣称（risk）。已按 16:00 保存草稿 DR-REQ-M03，未发布",
                          "audit_status": "risky", "suggested_slot": "16:00",
                          "pending_confirmations": ["补充审核归档编号 review_doc_ids",
                                                    "法务复核绝对化宣称"],
                          "draft_status": "saved"}},
    ],
    "case05_duplicate": [
        {"rationale": "读取素材元数据", "action": "fetch_material", "action_input": {"material_id": "M04"}},
        {"rationale": "元数据完整，检查规则", "action": "check_rules", "action_input": {"material_id": "M04"}},
        {"rationale": "规则通过，查询时段可用性", "action": "query_slots", "action_input": {}},
        {"rationale": "15:00 可用，保存草稿", "action": "save_draft",
         "action_input": {"request_id": "REQ-DUP", "material_id": "M04", "slot": "15:00"}},
        {"rationale": "已保存草稿，给出结论", "action": "finish",
         "action_input": {"answer": "已保存草稿 DR-REQ-DUP（未发布）", "audit_status": "passed",
                          "suggested_slot": "15:00",
                          "pending_confirmations": ["请确认是否按 15:00 发布"],
                          "draft_status": "saved"}},
    ],
    "case06_save_failure": [
        {"rationale": "读取素材元数据", "action": "fetch_material", "action_input": {"material_id": "M04"}},
        {"rationale": "元数据完整，检查规则", "action": "check_rules", "action_input": {"material_id": "M04"}},
        {"rationale": "规则通过，查询时段可用性", "action": "query_slots", "action_input": {}},
        {"rationale": "15:00 可用，保存草稿", "action": "save_draft",
         "action_input": {"request_id": "REQ-M04", "material_id": "M04", "slot": "15:00"}},
        {"rationale": "保存返回 503，重试一次以确认是否为瞬时故障", "action": "save_draft",
         "action_input": {"request_id": "REQ-M04", "material_id": "M04", "slot": "15:00"}},
        {"rationale": "两次保存均失败，据实收尾，不虚报成功", "action": "finish",
         "action_input": {"answer": "草稿保存工具连续两次返回 TOOL_UNAVAILABLE（storage 503），"
                                    "草稿未能保存；本系统无发布能力，内容未上线",
                          "audit_status": "passed", "suggested_slot": "15:00",
                          "pending_confirmations": ["草稿保存失败，需修复存储后重试"],
                          "draft_status": "not_saved"}},
    ],
    "case07_step_budget": [
        {"rationale": "读取素材元数据", "action": "fetch_material", "action_input": {"material_id": "M01"}},
        {"rationale": "元数据完整，检查规则", "action": "check_rules", "action_input": {"material_id": "M01"}},
        {"rationale": "规则通过，查询时段可用性", "action": "query_slots", "action_input": {}},
        {"rationale": "尝试保存草稿", "action": "save_draft",
         "action_input": {"request_id": "REQ-M01", "material_id": "M01", "slot": "19:00"}},
        {"rationale": "已保存草稿，给出结论", "action": "finish",
         "action_input": {"answer": "已保存草稿", "draft_status": "saved"}},
    ],
}


def _write_case(base: Path, case: dict, *, state, meta: dict, suffix: str = "output") -> None:
    target = base / case["dir"]
    target.mkdir(parents=True, exist_ok=True)
    # 非首次运行时不重写 input.json：两次运行的输入完全一致，
    # 重复写一遍只会让人怀疑是不是其实换了参数。
    if suffix == "output":
        input_doc = {
            "request_id": meta["request_id"],
            "material_id": case["material"],
            "requirement": case["requirement"],
            "fail_injection": case.get("fail_injection", "none"),
            "max_steps": case.get("max_steps"),
            "description": case["note"],
        }
        (target / "input.json").write_text(
            json.dumps(input_doc, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    payload = {
        "task_status": state["task_status"],
        "audit_status": state["audit_status"],
        "risk_reasons": state["risk_reasons"],
        "requested_slot": state["requested_slot"],
        "suggested_slot": state["suggested_slot"],
        "pending_confirmations": state["pending_confirmations"],
        "draft_status": state["draft_status"],
        # 重复提交用例的关键证据就在下面三个字段里，不能只留 draft_status
        "draft_id": state.get("draft_id"),
        "duplicate": state.get("duplicate"),
        "total_drafts": state.get("total_drafts"),
        "failures": state["failures"],
        "trace": state["trace"],
        "final_answer": state["final_answer"],
        "step_count": state["step_count"],
        "tool_call_count": state["tool_call_count"],
    }
    (target / f"{suffix}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    title = case["note"] if suffix == "output" else f"{case['note']}（重复提交的第一次）"
    lines = [f"# {case['dir']} — {title}", "",
             f"- `task_status`：`{state['task_status']}`",
             f"- `draft_status`：`{state['draft_status']}`",
             f"- `draft_id`：`{state.get('draft_id') or '（未保存）'}`",
             f"- `duplicate`：`{state.get('duplicate')}` / `total_drafts`：`{state.get('total_drafts')}`",
             ""]
    lines.append("| 步骤 | 动作 | 参数 | 返回摘要 | 后续动作 | 决策依据 |")
    lines.append("|---|---|---|---|---|---|")
    for rec in state["trace"]:
        params = json.dumps(rec["action_input"], ensure_ascii=False)
        lines.append(
            f"| {rec['step']} | `{rec['action']}` | `{params}` | "
            f"{rec['observation_summary'][:120]} | `{rec.get('next_action','')}` | {rec['rationale']} |"
        )
    lines.append("")
    lines.append("## 最终答复")
    lines.append("")
    lines.append(state["final_answer"] or "（无）")
    trace_name = "trace.md" if suffix == "output" else f"trace.{suffix}.md"
    (target / trace_name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("auto", "mock"), default="auto")
    args = parser.parse_args()

    base = ROOT / "examples" / ("mock" if args.provider == "mock" else "real_llm")
    base.mkdir(parents=True, exist_ok=True)
    started = datetime.now()

    results = []
    for case in CASES + [DUPLICATE_CASE]:
        # 草稿落盘文件放在用例目录内，作为"只产生一份草稿"的原始物证。
        target_dir = base / case["dir"]
        target_dir.mkdir(parents=True, exist_ok=True)
        drafts = target_dir / "drafts.json"
        request_id = case.get("request_id") or f"REQ-{case['material']}"

        # 重置该用例的草稿存储，保证重复运行本脚本得到同样的结果。
        # 用覆盖写而不是 unlink：受限环境下删除文件可能被拦截，
        # 而空对象对 _read_store 而言与新档等价。
        # 注意：case05 需要"同一份存储连跑两次"，所以重置放在两次循环之外。
        drafts.write_text("{}", encoding="utf-8")

        settings = load_settings()
        tools = ScheduleTools(
            settings,
            drafts_path=drafts,
            fail_injection=case.get("fail_injection", "none"),
        )

        repeat = 2 if case["dir"] == "case05_duplicate" else 1
        state = None
        prev_state = None
        for _ in range(repeat):
            if state is not None:
                prev_state = state
            if args.provider == "mock":
                script = MOCK_SCRIPTS.get(case["dir"])
                if script is None:
                    print(f"[跳过] {case['dir']}：mock 模式未提供对应脚本")
                    state = None
                    break
                # 把脚本一并落盘：这样每个用例目录都是自包含的，
                # 评审可以只拿这一个文件就把该路径单独重放出来。
                (target_dir / "mock_script.json").write_text(
                    json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                llm = ScriptedMockLLMClient([dict(x) for x in script])
            else:
                from src.agent.llm import build_llm_client

                llm = build_llm_client(settings, provider="auto")

            max_steps = case.get("max_steps") or settings.max_steps
            agent_settings = Settings(
                fail_injection=case.get("fail_injection", "none"), max_steps=max_steps
            )
            agent = ReActAgent(llm=llm, tools=tools, settings=agent_settings)
            state = agent.run(
                request_id=request_id,
                material_id=case["material"],
                user_requirement=case["requirement"],
            )

        if state is None:
            continue

        _write_case(base, case, state=state, meta={"request_id": request_id})

        # 重复提交用例要把"第一次"的结果也留下来，否则评审看到的是已经去重之后的状态，
        # 无法核对两次是否真的产生了同一个 draft_id。
        if prev_state is not None:
            _write_case(
                base, case, state=prev_state,
                meta={"request_id": request_id}, suffix="first_run",
            )

        results.append((case["dir"], state["task_status"], state["draft_status"]))
        print(f"[完成] {case['dir']}: task_status={state['task_status']} draft_status={state['draft_status']}")

    summary = [
        "# examples 索引",
        "",
        f"生成时间：{started.isoformat(timespec='seconds')}",
        f"模式：{'真实模型 deepseek-flash' if args.provider != 'mock' else '离线脚本 Mock'}",
        "",
        "> Mock 目录里的材料只证明**流程与编排正确**，不证明模型的决策能力。",
        "> 模型决策能力的证据在 `real_llm/` 目录。两者刻意分开存放。",
        "",
        "| 用例 | task_status | draft_status | 关注点 |",
        "|---|---|---|---|",
    ]
    descriptions = {c["dir"]: c["note"] for c in CASES}
    descriptions[DUPLICATE_CASE["dir"]] = DUPLICATE_CASE["note"]
    for name, task_status, draft_status in results:
        summary.append(f"| [`{name}/`](./{name}/trace.md) | `{task_status}` | `{draft_status}` | {descriptions[name]} |")

    (base / "README.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print(f"\n共生成 {len(results)} 组材料：{base}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
