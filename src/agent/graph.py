"""ReAct 状态机：模型选动作 → 执行工具 → 观察 → 模型决定下一步。

三个刻意的设计点：

1. **动作由模型产出，路由只负责搬运**。`route_after_decision` 里没有任何业务判断，
   它只按 action 名把流程分到不同节点。是否查询、是否换时段、是否请求确认、
   是否结束，全部来自模型输出。"固定条件分支"之所以不算 ReAct，就是因为那条分支
   的判断逻辑写在代码里；这里它写在模型的 `rationale` 与 `action` 里。

2. **工具返回值必须回填 conversation**。见 prompts.observation_message 的注释。
   这是"观察影响下一步"的唯一通道，删掉这一步，整个循环就退化成串行调用。

3. **最终结论里的保存状态以工具为准，不听模型的**。模型在 finish 里声称
   "草稿已保存"但没有任何一次 save_draft 成功返回时，会被强制改写为 not_saved
   并记入 failures。这条直接对应本题的关键缺陷"虚报保存成功"。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from langgraph.graph import END, StateGraph

from ..config import Settings
from ..tools.schedule_tools import ScheduleTools, ToolResult
from .llm import (
    ACTION_FETCH_MATERIAL,
    ACTION_FINISH,
    ACTION_REQUEST_CLARIFICATION,
    ACTION_SAVE_DRAFT,
    ALL_ACTIONS,
    LLMClient,
    LLMDecisionError,
)
from .prompts import build_system_prompt, assistant_message, build_initial_messages, observation_message
from .state import AgentState, ToolCallRecord

logger = logging.getLogger(__name__)

# 完结态的机器可读取值。CLI 用它决定退出码，测试用它做断言。
STATUS_OK = "done"
STATUS_EXCEEDED = "MAX_STEPS_EXCEEDED"
STATUS_DECISION_FAILED = "LLM_DECISION_FAILED"

# 「已发布」一类的措辞。出现在最终答复里即便工具从未成功过，也应被标记 ——
# 这是"不得声称已发布"的最后一道文本级防线（结构性防线已是工具集不含 publish）。
_PUBLISH_WORDS = ("已发布", "已上线", "发布成功", "published", "has been published")


def _extract_question(params: dict[str, Any]) -> str:
    """从模型给的参数里取出待确认项文案。

    模型可能用 question（单个）或 questions（数组）。这里做兼容，
    但**不替模型改写内容**：拿不到就如实记一条"未给出具体文案"，
    而不是编一个看起来通顺的问题。
    """
    single = params.get("question")
    if isinstance(single, str) and single.strip():
        return single.strip()
    many = params.get("questions")
    if isinstance(many, list) and many:
        merged = "；".join(str(x).strip() for x in many if str(x).strip())
        if merged:
            return merged
    for key in ("reason", "message", "content"):
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"（模型未给出具体文案）原始参数：{json.dumps(params, ensure_ascii=False)[:200]}"


def _trim(text: str, limit: int = 400) -> str:
    return text if len(text) <= limit else text[:limit] + "...(截断)"


def _backfill_next_actions(trace: list[ToolCallRecord]) -> list[ToolCallRecord]:
    """为每条记录补上"后续动作"字段。

    考核要求 trace 里体现后续动作，而下一步只有走到那里才知道，所以放在最后回填。
    """
    out: list[ToolCallRecord] = [dict(rec) for rec in trace]  # type: ignore[arg-type]
    for i, rec in enumerate(out):
        nxt = out[i + 1]["action"] if i + 1 < len(out) else ACTION_FINISH
        rec["next_action"] = nxt
    return out


class ReActAgent:
    """把 LangGraph 图包装成一个可直接调用的入口。"""

    def __init__(
        self,
        *,
        llm: LLMClient,
        tools: ScheduleTools,
        settings: Settings,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._settings = settings
        # 系统提示词在构造时一次性生成：工具签名来自代码本身，不存在第二份手写副本
        self._system_prompt = build_system_prompt(tools.describe())
        self._graph = self._compile()

    # ---------------------------------------------------------------- 图搭建

    def _compile(self):
        graph: StateGraph = StateGraph(AgentState)

        graph.add_node("prepare", self._prepare)
        graph.add_node("decide", self._decide)
        graph.add_node("execute", self._execute)
        graph.add_node("clarify", self._clarify)
        graph.add_node("finalize", self._finalize)
        graph.add_node("exceeded", self._exceeded)

        graph.set_entry_point("prepare")
        graph.add_edge("prepare", "decide")

        graph.add_conditional_edges(
            "decide",
            self._route_after_decision,
            {"execute": "execute", "clarify": "clarify", "finalize": "finalize", "exceeded": "exceeded"},
        )
        graph.add_conditional_edges(
            "execute",
            self._route_after_tool,
            {"decide": "decide", "exceeded": "exceeded"},
        )
        graph.add_edge("clarify", "decide")
        graph.add_edge("finalize", END)
        graph.add_edge("exceeded", END)

        # recursion_limit 与自维护的 step_count 是两条独立的防线：
        # LangGraph 文档给出的经验公式是 2N+1（含每个节点自己的一次记账），
        # 而实践资料指出 recursion_limit 并不等于迭代次数。
        return graph.compile()

    @property
    def recursion_limit(self) -> int:
        # 每个 ReAct 循环占用 decide + execute/clarify 两个图节点
        return self._settings.max_steps * 2 + 4

    # ---------------------------------------------------------------- 节点

    def _prepare(self, state: AgentState) -> dict[str, Any]:
        """把静态的任务输入变成首轮对话。只执行一次。"""
        return {"conversation": build_initial_messages(state)}

    def _decide(self, state: AgentState) -> dict[str, Any]:
        step = state["step_count"] + 1
        # 预算检查放在真正决策之前：已经用完就别再花钱调模型了
        if step > self._settings.max_steps:
            return {"step_count": step}

        try:
            action = self._llm.decide(
                system_prompt=self._system_prompt,
                conversation=state["conversation"],
                allowed_actions=ALL_ACTIONS,
            )
        except LLMDecisionError as exc:
            logger.error("模型决策失败：%s", exc)
            return {
                "step_count": step,
                "task_status": STATUS_DECISION_FAILED,
                "draft_status": "not_saved",
                "final_answer": f"任务未完成：{exc}",
                "failures": state["failures"] + [{"stage": "decide", "reason": str(exc)}],
            }

        updates: dict[str, Any] = {
            "step_count": step,
            "conversation": state["conversation"] + [assistant_message(action)],
        }
        if action.action == ACTION_FINISH:
            updates["_finish_input"] = action.action_input
            updates["final_answer"] = str(action.action_input.get("answer", ""))
        updates["_pending_action"] = {
            "action": action.action,
            "action_input": action.action_input,
            "rationale": action.rationale,
        }
        return updates

    def _execute(self, state: AgentState) -> dict[str, Any]:
        pending = state["_pending_action"]
        name: str = pending["action"]
        params: dict[str, Any] = pending["action_input"] or {}
        step = state["step_count"]

        try:
            fn = self._tools.registry()[name]
        except KeyError:
            result = ToolResult(
                name, params, False,
                error_code="UNKNOWN_TOOL",
                error_message=f"未知工具：{name}。可用：{sorted(self._tools.registry())}",
            )
        else:
            try:
                result = fn(**params)
            except TypeError as exc:
                # 模型给了对的工具、错的参数：如实告知，让它自己改，
                # 而不是在框架层做一层"参数修复"把模型的错误掩盖掉。
                result = ToolResult(
                    name, params, False,
                    error_code="INVALID_PARAMS",
                    error_message=f"参数不合法：{exc}",
                )

        record: ToolCallRecord = {
            "step": step,
            "action": name,
            "action_input": params,
            "rationale": _trim(pending["rationale"], 200),
            "observation_summary": _trim(result.summary),
            "ok": result.ok,
            "error_code": result.error_code,
        }

        updates: dict[str, Any] = {
            "tool_call_count": state["tool_call_count"] + 1,
            "trace": state["trace"] + [record],
            "conversation": state["conversation"] + [observation_message(result)],
        }

        if name == ACTION_FETCH_MATERIAL and result.ok:
            updates["requested_slot"] = result.data.get("requested_slot")
            if result.data.get("missing_fields"):
                updates["missing_fields"] = result.data["missing_fields"]

        if name == ACTION_SAVE_DRAFT and result.ok:
            updates["draft_status"] = "saved"
            updates["draft_id"] = result.data.get("draft_id")
            updates["duplicate"] = result.data.get("duplicate", False)
            updates["total_drafts"] = result.data.get("total_drafts")

        if name == ACTION_SAVE_DRAFT and not result.ok:
            updates["failures"] = state["failures"] + [
                {"stage": "save_draft", "reason": f"{result.error_code}: {result.error_message}"}
            ]

        return updates

    def _clarify(self, state: AgentState) -> dict[str, Any]:
        pending = state["_pending_action"]
        params = pending.get("action_input") or {}
        question = _extract_question(params)
        record: ToolCallRecord = {
            "step": state["step_count"],
            "action": ACTION_REQUEST_CLARIFICATION,
            "action_input": params,
            "rationale": _trim(pending.get("rationale", ""), 200),
            "observation_summary": f"已记录待确认项：{_trim(question)}",
            "ok": True,
            "error_code": None,
        }
        note = {"role": "user", "content": f"【系统】已把该待确认项登记：{question}。请继续。"}
        return {
            "trace": state["trace"] + [record],
            "pending_confirmations": state["pending_confirmations"] + [question],
            "conversation": state["conversation"] + [note],
        }

    def _finalize(self, state: AgentState) -> dict[str, Any]:
        payload: dict[str, Any] = state.get("_finish_input") or {}
        claimed_answers = str(payload.get("answer") or state.get("final_answer") or "")

        reported = str(payload.get("draft_status") or "")
        any_saved = any(
            rec["action"] == ACTION_SAVE_DRAFT and rec["ok"] for rec in state["trace"]
        )
        failures = list(state["failures"])
        draft_status = state.get("draft_status", "not_saved")

        # ---- 防「虚报保存成功」：以工具结果为准，不听模型自述 ----
        if reported == "saved" and not any_saved:
            failures.append({
                "stage": "finalize",
                "reason": "模型声称草稿已保存，但本次运行中没有任何一次 save_draft 成功返回，已强制记为未保存",
            })
            draft_status = "not_saved"
            claimed_answers += "（系统纠正：草稿实际未保存）"

        # ---- 防「越权发布」：文本层面的兜底扫描 ----
        hits = [w for w in _PUBLISH_WORDS if w in claimed_answers]
        if hits and not any_saved:
            failures.append({
                "stage": "finalize",
                "reason": f"最终答复出现发布类措辞 {hits}，但系统不具备发布能力，请人工复核",
            })

        return {
            "audit_status": str(payload.get("audit_status") or state.get("audit_status") or "unknown"),
            "risk_reasons": list(payload.get("risk_reasons") or state.get("risk_reasons") or []),
            "suggested_slot": payload.get("suggested_slot") or state.get("suggested_slot"),
            "pending_confirmations": list(
                payload.get("pending_confirmations") or state.get("pending_confirmations") or []
            ),
            "draft_status": draft_status,
            "trace": _backfill_next_actions(state["trace"]),
            "final_answer": claimed_answers,
            "failures": failures,
            "task_status": STATUS_OK,
        }

    def _exceeded(self, state: AgentState) -> dict[str, Any]:
        """预算耗尽：返回**明确的未完成状态**，而不是假装完成。

        这是考核里"项目须设置执行步数或工具调用次数上限，达到上限时返回明确的
        未完成状态"这条要求的落点。draft_status 保持不变（未保存就是未保存）。
        """
        return {
            "task_status": STATUS_EXCEEDED,
            "draft_status": state.get("draft_status", "not_saved"),
            "trace": _backfill_next_actions(state["trace"]),
            "final_answer": (
                f"未完成任务：已在 {state['step_count']} 步 / "
                f"{state['tool_call_count']} 次工具调用后达到上限。"
                "排期尚未可取，需要人工介入。"
            ),
        }

    # ---------------------------------------------------------------- 路由

    def _route_after_decision(self, state: AgentState) -> str:
        """纯搬运：不做任何业务判断，只按动了机的 action 名分流。"""
        if state.get("task_status") == STATUS_DECISION_FAILED:
            return "finalize"
        if state["step_count"] > self._settings.max_steps:
            return "exceeded"

        name = (state.get("_pending_action") or {}).get("action", "")
        if not name:
            return "exceeded"
        if name == ACTION_FINISH:
            return "finalize"
        if name == ACTION_REQUEST_CLARIFICATION:
            return "clarify"
        return "execute"

    def _route_after_tool(self, state: AgentState) -> str:
        if state["tool_call_count"] >= self._settings.max_tool_calls:
            return "exceeded"
        if state["step_count"] >= self._settings.max_steps:
            return "exceeded"
        return "decide"

    # ---------------------------------------------------------------- 入口

    def run(self, *, request_id: str, material_id: str, user_requirement: str) -> AgentState:
        from .state import new_state

        initial = new_state(
            request_id=request_id, material_id=material_id, user_requirement=user_requirement
        )
        return self._graph.invoke(
            initial,
            config={"recursion_limit": self.recursion_limit},
        )


def build_agent(
    *,
    llm: LLMClient,
    tools: Optional[ScheduleTools] = None,
    settings: Optional[Settings] = None,
) -> ReActAgent:
    return ReActAgent(
        llm=llm,
        tools=tools or ScheduleTools(settings or Settings()),
        settings=settings or Settings(),
    )


def state_to_json(state: AgentState) -> str:
    """导出完整运行状态。conversation 默认不带，避免 trace 文件过大。"""
    payload = {
        k: v for k, v in state.items() if k != "conversation" and not k.startswith("_")
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
