# 03 · 效果验证：测试预期、实际结果、失败与限制

> 本文件回答的是**"凭什么说它有效"**。
> 所有记录都可以在 `examples/` 下逐条核对，评估文档里的每一个结论都指向具体文件。
>
> 生成时间：2026-10-29（末次复核）· 模型：`deepseek-flash`（非思考模式，`temperature=0`）
> · LangGraph 1.2.17 · Python 3.13.12

---

## 1. 测试总览

```
$ .venv/Scripts/python.exe -m pytest
43 passed in 0.47s
```

| 文件 | 数量 | 覆盖什么 | 是否需要 API Key |
|---|---|---|---|
| `tests/test_tools.py` | 29 | 工具层的确定性行为、幂等、冲突时段、故障注入、关键缺陷防护 | 否 |
| `tests/test_decision_paths.py` | 14 | 决策路径差异、trace 结构、预算边界、虚报纠正、服务故障不被误判为决策失败 | 否 |
| **合计** | **43** | | **全部不需要 Key** |

全部 43 个用例在**没有 API Key** 的机器上也能通过，因为它们验证的是编排与工具，
不验证模型能力。模型能力的证据单独放在 `examples/real_llm/`。

---

## 2. 六个必验收点：预期 vs 实际

所有实际结果取自 `examples/real_llm/<用例>/output.json`，Trace 在同目录的 `trace.md`。

### 验收点 1 · 正常排期

| 项 | 内容 |
|---|---|
| 用例 | `examples/real_llm/case01_normal/` |
| 输入 | `--material M04 --request-id REQ-M04` |
| 预期 | 字段完整、规则 pass、15:00 空闲 → 按用户要求时段保存草稿 |
| 实际 | `audit_status=passed`，`suggested_slot=15:00`，`draft_status=saved`，`draft_id=DR-REQ-M04` |
| 步数 | 5（4 次工具调用 + finish） |
| **结论** | ✅ 通过 |

### 验收点 2 · 素材信息缺失

| 项 | 内容 |
|---|---|
| 用例 | `examples/real_llm/case02_missing_info/` |
| 输入 | `--material M02 --request-id REQ-M02`（M02 的 `requested_slot` 为 null） |
| 预期 | 不自造字段值，触发请求确认，不保存草稿 |
| 实际 | 步骤 2 选择 `request_clarification`；`draft_status=not_saved`；`suggested_slot=null` |
| **结论** | ✅ 通过。模型没有自己编一个发布时间填进去 |

### 验收点 3 · 规则风险

| 项 | 内容 |
|---|---|
| 用例 | `examples/real_llm/case03_rule_risk/` |
| 输入 | `--material M03 --request-id REQ-M03`（命中 R03 warn + R04 risk） |
| 预期 | 不阻断，但把风险列入待确认 |
| 实际 | `audit_status=risky`，按用户要求的 16:00 保存草稿，`pending_confirmations` 非空 |
| **结论** | ✅ 通过 |

补充：`severity=block` 这一档没有放进 `data/`（不属于必验点），由
`tests/test_tools.py::test_check_rules_blocks_unlicensed_music` 用内联 fixture 覆盖，
断言 `blocked=True` 且命中 `R01`。

### 验收点 4 · 时段冲突

| 项 | 内容 |
|---|---|
| 用例 | `examples/real_llm/case04_slot_conflict/` |
| 输入 | `--material M01 --request-id REQ-M01`，用户要求 18:00 |
| 预期 | 18:00 被占用 → 建议另一个可用时段 + 标记待确认，**不得声称用户已同意改期** |
| 实际 | Trace 第 3 步读到 `occupied: [{slot: 18:00, occupant: M90}]`；第 4 步改用 `slot=19:00`，决策依据原文是"18:00 已被 M90 占用，需从可用时段中挑一个作为建议并保存为待确认草稿，**不能替用户决定改期**"；`suggested_slot=19:00`，最终答复结尾是"是否接受改期至 19:00…请用户确认" |
| **结论** | ✅ 通过，且行为与题目参考实例一致 |

### 验收点 5 · 重复提交

| 项 | 内容 |
|---|---|
| 用例 | `examples/real_llm/case05_duplicate/` |
| 输入 | 同一 `--request-id REQ-DUP` 连续执行两次 |
| 预期 | 只产生一份草稿 |
| 实际 | 第一次：`draft_id=DR-REQ-DUP`，`duplicate=False`，`total_drafts=1`（`first_run.json`）<br>第二次：`draft_id=DR-REQ-DUP`，`duplicate=True`，`total_drafts=1`（`output.json`） |
| **结论** | ✅ 通过 |

> 这里曾经失败过一次。第一版把模型生成的自由文本 `note` 算进了幂等指纹，
> 两次运行的 note 措辞不同 → 被判成 `IDEMPOTENCY_CONFLICT`，模型转而去问用户
> "要不要换个 request_id"。修复方式见 `docs/02-solution.md` 的 D3。

### 验收点 6 · 保存失败

| 项 | 内容 |
|---|---|
| 用例 | `examples/real_llm/case06_save_failure/` |
| 输入 | `--material M04 --fail-injection save_draft` |
| 预期 | 明确返回失败，不虚报成功 |
| 实际 | 模型在步骤 4 保存失败后于步骤 5 **重试一次**（决策依据："存储 503，重试一次以确认是否为瞬时故障"），仍失败后在步骤 6 收尾：`draft_status=not_saved`，`failures` 里有 2 条 `TOOL_UNAVAILABLE`，最终答复写明"草稿未能保存…本系统无发布能力，内容未发布" |
| **结论** | ✅ 通过 |

### 附加用例 · 步数上限

| 项 | 内容 |
|---|---|
| 用例 | `examples/real_llm/case07_step_budget/` |
| 输入 | `--material M01 --max-steps 2` |
| 预期 | 返回明确的未完成状态 |
| 实际 | `task_status=MAX_STEPS_EXCEEDED`，`final_answer="未完成任务：已在 2 步 / 2 次工具调用后达到上限…"`，CLI 退出码 `2` |
| **结论** | ✅ 通过 |

---

## 3. 两条差异化执行路径（决策真实性的核心证据）

同一份提示词、同一个模型、同一份工具集合，仅因工具返回不同而产生不同路径：

| | 路径 A（`case04_slot_conflict`） | 路径 B（`case02_missing_info`） |
|---|---|---|
| 步骤 1 | `fetch_material` | `fetch_material` |
| **分歧点** | 返回 `missing_fields=[]` → 继续检查规则 | 返回 `missing_fields=["requested_slot"]` → **转入 `request_clarification`** |
| 步骤 2 | `check_rules` | `request_clarification` |
| 步骤 3 | `query_slots` | `check_rules` |
| 步骤 4 | `save_draft`（slot=19:00） | `query_slots` |
| 最终 | `draft_status=saved` | `draft_status=not_saved` |

分歧完全发生在看到 `fetch_material` 返回之后 —— 路径 A 看到字段齐全，路径 B 看到缺字段。
代码里的 `route_after_decision` 只按 action 名搬运，不含任何业务判断，
所以这两条路径的差异只能来自模型。

另有第三、四条路径：`case06` 在保存失败后**选择重试再收尾**；
早前一次调试中模型因压根不知道参数名，用八步预算反复猜参数直至超限
（该失败已修复，记录见 `docs/02-solution.md` D10）。

---

## 4. 失败过的用例与修复记录

诚实记录失败是本项目的取向：这些不是"最终结果"，但它们比最终结果更有信息量。

| # | 现象 | 根因 | 修复 | 守卫它的测试 |
|---|---|---|---|---|
| F1 | 模型每步都输出与提示词样例**逐字相同**的 rationale | DeepSeek JSON Output 强制要求样例，样例被照抄 | 样例改为中间动作 + 占位 rationale + 显式否定 | 人工核对 `scripts/probe_decision.py` |
| F2 | M04 用例以 `MAX_STEPS_EXCEEDED` 结束，8 步全在猜参数 | 提示词没暴露工具参数名 | 签名从 `describe()` 注入提示词 | `test_tool_spec_matches_signature`、`test_tool_spec_covers_every_registered_tool` |
| F3 | 重复提交被判 `IDEMPOTENCY_CONFLICT` | 幂等指纹包含模型生成的自由文本 `note` | 指纹只取 `material_id + slot + confirmed` | `test_repeated_same_request_produces_one_draft` |
| F4 | pytest 全量报 `PermissionError: WinError 5` | sandbox 下无法写系统 temp / 无法清理 basetemp | 改用项目内 `isolated_dir` fixture | 全部用例自带即可体现 |
| F5 | venv 里 `No module named pip` | 沙箱把 `pip` 目录改名成 `~ip` | 改回目录名；本项目不再升级 pip | - |
| F6 | 模型服务不可达时 CLI 抛 40 行 traceback 并以退出码 `1` 结束 | SDK 的传输异常未被分类，既不是 `LLMDecisionError` 也不是正常返回 | 新增 `LLMUnavailableError` 与退出码 `4`，与"决策失败（3）"分开报 | `test_model_unavailable_is_not_reported_as_decision_failure` |

---

## 5. 已知限制

主动列出，避免评审按更高标准误判。

| # | 限制 | 为什么存在 | 影响 |
|---|---|---|---|
| L1 | **规则引擎是字符串/相等判定**，同义改写（如"没有比我们更强的"）不会被 R04 命中 | 未引入文本分类模型，刻意保持工具层确定性 | 只是玩具级规则匹配，不代表生产可用的合规审查 |
| L2 | 草稿不回填 `schedule.json` | 草稿与正式排期是两套东西；回填等于赋予草稿占用权 | 跨素材冲突只在**同一份草稿存储**内检测（`SLOT_TAKEN_BY_DRAFT`） |
| L3 | 没有并发控制，草稿存储是单个 JSON 文件 | 单机演示，题目不要求生产级 Runtime | 并发写会丢数据 |
| L4 | Mock 不能证明模型决策能力 | 脚本替身是一次性动作队列，不读取上下文 | `examples/mock/` 的七条路径只用于验证流程与编排；即便与 `real_llm/` 的路径一致，也不构成模型能力的证据 |
| L5 | `check_rules` 的结果缓存不在内存中，每次重新读文件 | 简化，代价是重复 IO | 大规模素材下性能不佳 |
| L6 | `requested_slot` 缺失时完全依赖用户补充 | 题目要求不得擅自改变硬性发布要求 | 无法给出"建议时段"的自动推荐 |
| L7 | 未实现 LangGraph `interrupt()` 的真人审批 | 时间预算内优先保证六个必验点 | "待确认"目前是记录 + 措辞约束，不是阻塞式闸门 |

---

## 6. 时间投入

| 阶段 | 用时 |
|---|---|
| 需求拆解与方案取舍 | ~25 min |
| 业界调研（OWASP / 幂等 / LangGraph / HITL / DeepSeek 文档） | ~35 min |
| 数据与配置层 | ~20 min |
| 工具层 | ~30 min |
| ReAct 状态机 | ~30 min |
| 测试与示例生成 | ~40 min |
| 文档 | ~30 min |
| **合计** | **约 3.5 小时**（超出建议的 3 小时，主要超在调研与故障排查） |

未完成的部分如实写在 `docs/05-retrospective.md`。
