# 06 · 提交说明（答题区域文本）

> 这份文件的内容**可以直接粘贴到测评的答题区域**。它回答的是"仓库里放不下、或评审来不及从代码里看出来的东西"：
> 题号、核心取舍、两条决策路径、边界与未完成项、AI 使用声明。
> 仓库里的完整论证见 `docs/01` ~ `docs/05`，运行材料见 `examples/`。

---

## 题号与交付

- **题号**：第 1 题 · 素材审核与发布排期 Agent
- **仓库**：https://github.com/a805026135/content-schedule-agent （私有，已授权评审账号）
- **最终 commit SHA**：`be690108c66b1146daa3fd12e9d92ada9ef78b17`
- **运行方式**：CLI
- **模型**：`deepseek-flash`（DeepSeek-V4.1-Flash-1017，非思考模式，`temperature=0`）
- **编排**：LangGraph `1.2.17`，手写 `StateGraph`（不使用 `prebuilt.create_react_agent`）
- **实际用时**：约 3.5 小时（超出建议的 3 小时，超出部分主要在业界调研与故障排查）

> 注：仓库里的 `.env` 不入库（`.gitignore` 已排除），评审无需配置任何密钥即可跑测试；
> 若要复现 `examples/real_llm/` 下的真实模型记录，需要自备 DeepSeek Key。

三条命令即可复现：

```bash
.venv/Scripts/python.exe -m pytest                 # 42 个用例，不需要任何 API Key
.venv/Scripts/python.exe -m src.cli --material M01 --request-id REQ-M01
.venv/Scripts/python.exe scripts/run_examples.py     # 一键重生成全部验证材料
```

---

## 核心取舍：把"希望模型别做错"翻译成"做错了也改变不了结果"

这道题的三个高分依据（冲突调整、确认边界、防重复）**全部可以用代码结构保证**，
所以我没把它们写进提示词，而是做进架构：

| 题目要求 | 我的做法 | 效果 |
|---|---|---|
| 仅保存草稿，不得发布 | 工具注册表里**根本没有 publish 工具** | 越权在能力上不可能，不看模型心情 |
| 不得虚报保存成功 | 最终 `draft_status` 只认 trace 里有没有成功的 `save_draft`，与模型自述不一致时强制改写并记入 `failures` | 虚报在数据流上不可能 |
| 重复提交只产生一份草稿 | `request_id` + payload 指纹做幂等；指纹**不含**模型生成的自由文本 | 可断言：`total_drafts` 恒为 1 |
| 达到上限须返回未完成 | `step_count` / `tool_call_count` 双预算 + 图深度硬顶，耗尽返回 `MAX_STEPS_EXCEEDED`，CLI 退出码 `2` | 未完成状态机器可读，不依赖人读文字 |

依据来自 OWASP LLM06/LLM08（Excessive Agency 的首选缓解措施是**最小化工具集合**，其举例
"替用户发帖的 LLM 应用应在 post 操作内内置审批例程"与本题几乎逐字重合）、
AWS Builders' Library 与 WSO2 的幂等实践（幂等键必须由调用方持有，且要存 body 指纹）、
LangGraph 官方关于 `recursion_limit` 的说明。完整出处表在 `docs/02-solution.md` 末尾。

---

## 决策真实性的证据：两条路径（同一提示词、同一模型、同一工具集）

分歧发生在第 1 步 `fetch_material` 返回之后，代码里的 `route_after_decision`
只按 action 名搬运、不含任何业务判断，所以差异只能来自模型。

**路径 A — 素材 M01，用户要求 18:00（`examples/real_llm/case04_slot_conflict/`）**

| 步骤 | 动作 | 模型自述的决策依据 |
|---|---|---|
| 1 | `fetch_material` | 先读取元数据，确认必填字段是否齐全 |
| 2 | `check_rules` | 元数据完整无缺失，下一步按规则检查是否有 block/risk/warn |
| 3 | `query_slots` | 审核通过，查询排期表确认 18:00 是否可用 |
| 4 | `save_draft`(19:00) | "18:00 已被 M90 占用，需从可用时段中挑一个作为建议并保存为待确认草稿，**不能擅自替用户改期**" |

结果：`suggested_slot=19:00`，`pending_confirmations` 非空（"是否接受改为 19:00"），`draft_status=saved`，答复明确写"未发布"。

**路径 B — 素材 M02，缺 `requested_slot`（`examples/real_llm/case02_missing_info/`）**

| 步骤 | 动作 | 模型自述的决策依据 |
|---|---|---|
| 1 | `fetch_material` | 先读取元数据，确认必填字段是否齐全 |
| 2 | `request_clarification` | "缺少必填字段 `requested_slot`，按硬约束不能自行编造，需先向用户索取" |
| 3 | `check_rules` | 等待回复期间先做规则检查，确认素材是否可排期 |
| 4 | `query_slots` | 规则通过，先查可用时段以便给用户可选项 |

结果：`draft_status=not_saved`，`suggested_slot=null` —— 模型没有自己编一个发布时间填进去。

另有第三条路径：`case06` 在保存失败后**自己选择重试一次**，仍失败才据实收尾（不把失败记成成功）。

---

## 六个必验收点：结论

| 验收点 | 用例 | 实际结果 |
|---|---|---|
| 正常排期 | `case01_normal` | `passed` / 15:00 / `saved` |
| 素材信息缺失 | `case02_missing_info` | 转 `request_clarification`，`not_saved` |
| 规则风险 | `case03_rule_risk` | `risky`，按 16:00 保存，3 条待确认 |
| 时段冲突 | `case04_slot_conflict` | 18:00 被占 → 建议 19:00，`saved` + 待确认 |
| 重复提交 | `case05_duplicate` | 两次同为 `DR-REQ-DUP`，第二次 `duplicate=true`，`total_drafts=1` |
| 保存失败 | `case06_save_failure` | `not_saved`，2 条 `TOOL_UNAVAILABLE` |
| 步数上限（附加） | `case07_step_budget` | `MAX_STEPS_EXCEEDED`，退出码 `2` |

---

## 边界与未完成（如实说明）

**Mock 的边界**：`examples/mock/` 下的七条路径由离线脚本驱动，**只证明流程与编排正确，
不证明模型的决策能力**。脚本替身不读取上下文，动作序列是照着真实模型跑出来的路径抄的，
两个目录刻意分开存放以避免误读。模型决策的证据在 `examples/real_llm/`。

**已知限制**：规则引擎是字符串/相等判定级别（同义改写测不出来）；
草稿不回填排期表；无并发控制；`requested_slot` 缺失时完全依赖用户补充。
详见 `docs/03-evaluation.md` 第 5 节。

**未完成**：真人审批闸门（LangGraph `interrupt()`）未实现 —— "待确认"目前是记录 + 措辞约束，
不是阻塞式闸门；草稿与正式排期的语义分层（`reserve_slot`）未实现；conversation 未做分窗摘要。
完整清单在 `docs/05-retrospective.md` 第 3 节。

---

## AI 使用声明（摘要，完整版见 `docs/04-ai-usage.md`）

AI 参与了绝大部分代码初稿，但**设计决策、故障归因、修复方向由我做出，每一行产出我都读过并能解释**。
我对 AI 初稿做了 5 处关键修改：

1. `auto` 降级必须把 `provider` 写进输出 —— 静默降级等于制造假证据
2. 重试只修格式（空内容 / 非法 JSON / 动作越界），**业务动作绝不由代码替模型决定**
3. 幂等指纹移除模型生成的自由文本 `note`
4. 最终保存状态改为以工具结果为准，不听模型自述
5. 工具返回值必须回写进 `conversation`，否则 ReAct 会退化成串行调用

失败用例（F1~F5，含"模型照抄提示词样例""八步预算全花在猜参数名"）如实记录在
`docs/03-evaluation.md` 第 4 节 —— 它们比"42 passed"更能说明我实际做了什么。
