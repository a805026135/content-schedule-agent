"""模型接入层：真实 DeepSeek 与离线 Mock 双入口。

边界说明（务必与文档中保持一致）：
- DeepSeekLLMClient：真实模型产出动作，可用于证明「模型根据工具结果调整下一步」。
- ScriptedMockLLMClient：按脚本返回动作的测试替身。它**不是**模型，
  只用来在 CI 和无 Key 环境下验证流程正确性与状态机行为，
  不作为「模型决策能力」的证据。

关于「重试」的说明，防止被误读：
本文件中的重试只发生在模型输出无法满足**结构契约**时（返回空内容 / 非法 JSON /
动作名不在允许集合）。这类重试修复的是格式，不替模型选择任何业务动作。
业务决策（查哪个工具、是否换时段、是否请求确认）始终由模型给出，
重试过程中我们不会也没有地方替它改写 action 的语义内容。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, Field, ValidationError

from ..config import Settings

logger = logging.getLogger(__name__)

# 动作名常量：同时被工具注册表与提示词引用，避免字符串写错
ACTION_FETCH_MATERIAL = "fetch_material"
ACTION_CHECK_RULES = "check_rules"
ACTION_QUERY_SLOTS = "query_slots"
ACTION_SAVE_DRAFT = "save_draft"
ACTION_REQUEST_CLARIFICATION = "request_clarification"
ACTION_FINISH = "finish"

ALL_ACTIONS: tuple[str, ...] = (
    ACTION_FETCH_MATERIAL,
    ACTION_CHECK_RULES,
    ACTION_QUERY_SLOTS,
    ACTION_SAVE_DRAFT,
    ACTION_REQUEST_CLARIFICATION,
    ACTION_FINISH,
)

# 给模型的输出样例。DeepSeek 的 JSON Output 要求提示词里出现「json」字样
# 并给出格式样例，否则不会启用该能力。
#
# 这里刻意让样例本身"不可照抄"：样例用的是 query_slots 这个中间动作，且
# rationale 写成占位描述。若样例正好是「冷启动时读素材」，模型倾向于直接
# 复述样例文本，导致 trace 里的决策依据看起来像模板填充而非模型产出。
_ACTION_EXAMPLE: dict[str, Any] = {
    "rationale": "（用一句话说明你为什么在当前这一步选这个动作）",
    "action": "query_slots",
    "action_input": {"date": "2026-10-29", "platform": "douyin"},
}


class AgentAction(BaseModel):
    """模型每一步的输出契约。

    rationale 是刻意设计的字段：考核要求 trace 里给出「简要决策依据」，
    但不需要提交模型的完整思维链。因此这里让模型输出一句可序列化的理由，
    与 action / action_input 一起落盘，而不是把 CoT 原文 dump 进日志。
    """

    rationale: str = Field(min_length=1, description="选择该动作的简要依据")
    action: str = Field(description="要执行的动作名")
    action_input: dict[str, Any] = Field(default_factory=dict)


class LLMDecisionError(RuntimeError):
    """模型在重试上限后仍无法给出合规动作。

    这里如实向上抛出异常，绝不替模型补一个「看起来合理」的动作 ——
    否则系统就会把自己编的动作记到模型账上。
    """


class LLMUnavailableError(RuntimeError):
    """模型服务不可用：网络不可达、鉴权失败、限流、超时等。

    与 LLMDecisionError 刻意分开：后者是「模型说话了但说的不合规」，
    前者是「根本没说上话」。两者的处置方式不同 ——
    后者是决策失败，前者应当被如实报成"环境/服务问题"，
    绝不能被当成一次空返回去走格式修复重试，那样只会把网络故障伪装成模型故障。
    """


@runtime_checkable
class LLMClient(Protocol):
    def decide(
        self,
        *,
        system_prompt: str,
        conversation: Sequence[dict[str, str]],
        allowed_actions: Sequence[str],
    ) -> AgentAction: ...


def _contract_block(allowed_actions: Sequence[str]) -> str:
    """拼装输出契约说明。allowed_actions 参与提示，让模型知道当前还能做什么。"""
    example = dict(_ACTION_EXAMPLE)
    return (
        "\n\n【输出契约】\n"
        "请严格输出一个 JSON 对象（不要代码块、不要额外文字），结构如下：\n"
        + json.dumps(example, ensure_ascii=False)
        + "\n字段说明：\n"
        "- rationale：一句话说明你为什么选这个动作（会写入执行记录）\n"
        "- action：本次要执行的动作，必须取自允许集合\n"
        "- action_input：该动作的参数对象，无参数时为 {}\n"
        "注意：上面的样例仅用于说明 JSON 结构与字段位置，**不是**建议你执行它，\n"
        "你必须依据当前对话中已有的工具返回自行判断。\n"
        f"当前允许的动作集合：{list(allowed_actions)}"
    )


class DeepSeekLLMClient:
    """通过 OpenAI 兼容接口调用 DeepSeek 会话模型。

    默认模型 deepseek-flash 即 DeepSeek-V4.1-Flash-1017（2026-10-17 发布）。
    历史标识 deepseek-chat 已于 2026-07-24 退役，请勿回退。
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY 为空，无法创建真实模型客户端")
        self._settings = settings
        self._client: Any = None

    @property
    def model_name(self) -> str:
        return self._settings.deepseek_model

    @property
    def _sdk(self) -> Any:
        # 延迟导入：mock 模式下不必依赖 openai 包是否已安装
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self._settings.deepseek_api_key,
                base_url=self._settings.deepseek_base_url,
                timeout=self._settings.llm_timeout_sec,
            )
        return self._client

    def _thinking_body(self) -> dict[str, Any]:
        """按 DeepSeek 现行 API 组装思考模式参数。

        DeepSeek-V4.1-Flash 默认开启思考（effort=high）。不显式关闭的话，
        思维链会占用 max_tokens 预算，把真正的 JSON 挤到被截断的位置，
        表现之一就是官方承认的 "JSON Output 偶发返回空内容"。
        本项目每次只需要几十个 token 的结构化动作，用不到思维链，
        因此默认以 {"thinking": {"type": "disabled"}} 关闭。
        """
        mode = self._settings.deepseek_thinking
        return {"thinking": {"type": "disabled" if mode == "disabled" else "enabled"}}

    def _raw_completion(self, messages: list[dict[str, str]]) -> Optional[str]:
        mode = self._settings.deepseek_thinking
        request_kwargs: dict[str, Any] = {
            "model": self._settings.deepseek_model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            # 不设置的话非思考模式默认 8K：够用，但显式声明可避免不同版本间的默认值漂移
            "max_tokens": self._settings.llm_max_tokens,
            "extra_body": self._thinking_body(),
        }
        if mode == "disabled":
            # 思考模式不支持 temperature / top_p，只在非思考模式下生效。
            # DeepSeek 官方对代码与数学类任务推荐 0.0；本项目是结构化决策，
            # 取 0 以换取可复现的执行记录。
            request_kwargs["temperature"] = 0
        else:
            request_kwargs["reasoning_effort"] = mode

        response = self._sdk.chat.completions.create(**request_kwargs)
        message = response.choices[0].message

        reasoning = getattr(message, "reasoning_content", None)
        if reasoning:
            logger.warning(
                "检测到 reasoning_content：思考模式未关闭，其思维链会挤占"
                " JSON 输出的 token 预算。请确认 DEEPSEEK_THINKING=disabled。"
            )

        content = message.content
        return content.strip() if content and content.strip() else None

    def decide(
        self,
        *,
        system_prompt: str,
        conversation: Sequence[dict[str, str]],
        allowed_actions: Sequence[str],
    ) -> AgentAction:
        contract = _contract_block(allowed_actions)
        repair_note = ""
        attempts = max(0, self._settings.llm_max_retries) + 1
        last_problem = "未知原因"

        for attempt in range(1, attempts + 1):
            messages = [
                {"role": "system", "content": system_prompt + contract + repair_note}
            ] + list(conversation)

            try:
                raw = self._raw_completion(messages)
            except LLMUnavailableError:
                raise
            except Exception as exc:  # SDK 侧的传输/鉴权/限流异常
                raise LLMUnavailableError(
                    f"调用模型服务失败（{type(exc).__name__}）：{exc}。"
                    "这不是模型决策失败，而是服务不可达或不可用的环境问题。"
                ) from exc
            if raw is None:
                last_problem = "模型返回空内容"
                logger.warning("第 %s 次决策：模型返回空内容，准备重试", attempt)
                repair_note = "\n\n上一次你返回了空内容，请重新输出完整的 JSON 对象。"
                continue

            try:
                action = AgentAction.model_validate(json.loads(raw))
            except (json.JSONDecodeError, ValidationError) as exc:
                last_problem = f"输出不满足结构契约（{type(exc).__name__}）"
                logger.warning("第 %s 次决策：%s", attempt, last_problem)
                repair_note = (
                    "\n\n上一次输出无法解析为约定结构，"
                    "请只输出一个 JSON 对象，不要包含 Markdown 代码块或说明文字。"
                )
                continue

            if action.action not in allowed_actions:
                last_problem = f"动作 {action.action!r} 不在允许集合内"
                logger.warning("第 %s 次决策：%s", attempt, last_problem)
                repair_note = (
                    "\n\n上一次选择的动作不在允许集合内。"
                    f"请从 {list(allowed_actions)} 中选择。"
                )
                continue

            return action

        raise LLMDecisionError(
            f"模型连续 {attempts} 次未能给出合规动作，最后一次问题：{last_problem}"
        )


class ScriptedMockLLMClient:
    """离线测试替身：按脚本顺序返回动作。

    只用于 tests/ 与 examples/mock/。脚本耗尽时抛异常而不是静默补刀，
    这样一旦 fixtures 与实现不同步会立刻暴露。
    """

    def __init__(self, script: Sequence[dict[str, Any]]) -> None:
        self._queue: list[dict[str, Any]] = [dict(item) for item in script]

    @property
    def model_name(self) -> str:
        return "mock:scripted"

    def decide(
        self,
        *,
        system_prompt: str,
        conversation: Sequence[dict[str, str]],
        allowed_actions: Sequence[str],
    ) -> AgentAction:
        del system_prompt, conversation  # 脚本替身不读取上下文，这是它的边界所在
        if not self._queue:
            raise LLMDecisionError(
                "Mock 脚本已耗尽：真实模型在此处会继续决策，"
                "但脚本替身不会。请检查 fixtures 或改用 DeepSeekLLMClient。"
            )
        item = self._queue.pop(0)
        return AgentAction.model_validate(item)


def build_llm_client(
    settings: Settings,
    *,
    provider: Optional[str] = None,
    script: Optional[Sequence[dict[str, Any]]] = None,
) -> LLMClient:
    if script is not None:
        return ScriptedMockLLMClient(script)
    actual = provider or "auto"
    if actual == "auto":
        # auto 是"按环境自动选择"的约定值，需要先落到具体 provider 再走分派
        actual = resolve_provider_safely(settings)
    if actual == "deepseek":
        return DeepSeekLLMClient(settings)
    if actual == "mock":
        raise ValueError(
            "mock 模式需要提供显式脚本，否则无从决定动作 —— "
            "离线替身不能替模型做决定，所以不存在「什么都不传也能跑」的模式。\n"
            "请在 CLI 中用 --mock-script 指定；仓库已附带可直接使用的脚本，例如：\n"
            "  python -m src.cli --material M01 --request-id REQ-MOCK \\\n"
            "      --mock-script examples/mock/mock_script_example.json\n"
            "或一次性重放全部七条路径：python scripts/run_examples.py --provider mock"
        )
    raise ValueError(f"未知的 LLM_PROVIDER：{actual}")


def resolve_provider_safely(settings: Settings) -> str:
    from ..config import resolve_provider

    return resolve_provider(settings)
