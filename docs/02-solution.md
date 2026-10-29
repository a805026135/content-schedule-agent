# 02 · 方案设计：调用流程、动态决策与技术取舍

> **文档状态**：本文描述**目标设计**。部分模块尚在实现中，落地进度见文末
> 「实现状态」一节。已实现部分与本文一致；未实现部分在实现后如有偏差，会在同一节回填。
> 我们刻意保留这张表，而不是把设计文档写成已经达成的样子。

---

## 0. 一句话概览

一个**只有草稿权限**的审核排期 Agent：给它一个素材，它自己去查素材、查规则、查时段，
过程中**由模型**决定每一步做什么、要不要换时段、要不要回头问人，最后把结论存成**草稿**。
它无法发布任何东西——不是被提示词约束，而是发布这个动词在它的世界里不存在。

---

## 1. 业界调研：从哪里抄的，抄了什么

搜索范围包括：OWASP 官方 LLM 风险清单及其安全实践解读、WSO2 与 AWS Builders' Library
的幂等 API 设计、LangGraph 官方手册与其 Human-in-the-loop 文档、以及 DeepSeek 官方 API 文档。
下表是**直接改变了本项目设计**的结论，其余一般性常识不列。

| 来源 | 它的结论 | 对本项目的影响 |
|---|---|---|
| OWASP LLM Top 10 — Excessive Agency | 权限超出任务所需时事故半径失控；** authorize 要在下游系统里做，不能交给模型判断** | 决定"不提供 publish 工具"，规则判定下沉到工具层 |
| OWASP LLM08 举例 | "替用户创作并**发帖到社交媒体**的 LLM 应用，应在实现 post 操作的工具内内置用户审批例程" | 与本题场景几乎逐字重合，成为"草稿 + 待确认"设计的直接依据 |
| Safeguard《guardrails vs sandboxes》 | 护栏（提示/过滤）可被绕过，**沙箱（能力边界）不依赖模型是否听话**；二者冲突时以沙箱为准 | 明确"结构性防线优先于提示词防线"的分层顺序 |
| WSO2《Idempotency Keys for APIs》 | Agent 重试写操作是常态；**每次工具调用都新生成 UUID 会让去重完全失效，这是编排层的 bug** | 幂等 key 必须由 Agent 状态持有并贯穿重试，不能由工具临时生成 |
| AWS Builders' Library 转述《Making retries safe》 | 不能只存 key 不存 body 指纹——**同 key 不同 body 若被静默重放，返回的是错误的响应** | `request_id` 之外增加 payload 指纹校验，冲突时报错而非重放 |
| LangGraph 官方《运行 Agent》 | `recursion_limit = 2 × max_iterations + 1`；超限抛 `GraphRecursionError` | 步数上限的设置公式与异常捕获方式 |
| LangGraph 实践资料 | **`recursion_limit` 控制的是图执行深度，不等于 ReAct 迭代数**，一次循环可能消耗多个深度单位 | 在 state 里自维护 `step_count` 作为真正的决策计数器 |
| LangChain HITL 文档 | 人类可给出 approve / edit / reject / respond 四种决策；**不得用 `respond` 拒绝有副作用的工具**，因为其返回值会被当成成功结果 | 直接对应"不得把失败伪装成成功"这条关键缺陷，成为 `request_clarification` 的设计边界 |
| 事务型 Agent 实践（2PC 式） | staging → validation → commit 三层分离，**任何不可逆动作都不应在没有显式许可时发生** | 本项目是有意只实现 staging 层的特例，并把这当作设计决策而非缺失 |
| DeepSeek 官方 JSON Output 文档 | 启用需 `response_format` + 提示词含 "json"；**已知会偶发返回空 content** | 客户端必须具备空内容与非法 JSON 的兜底重试 |
| DeepSeek API 变更说明 | **`deepseek-chat`/`reasoner` 已退役**，现行 `deepseek-flash` = V4.1-Flash-1017；**思考模式默认开启**且思维链占用 `max_tokens` | 纠正模型名默认值，并显式关闭思考模式 |

---

## 2. 系统分层

```
CLI            src/cli.py                入参、退出码、把 trace 落成文件
 └─ Graph      src/agent/graph.py        LangGraph 状态机：谁在什么时候被调用
     ├─ Node   src/agent/graph.py        每一步请模型决定动作；不含任何业务判断
     ├─ Tools  src/tools/*.py            确定性纯函数；失败就返回失败，不修饰
     └─ LLM    src/agent/llm.py          DeepSeek / Mock 双入口；只输出结构化动作
```

分层规则只有一条，其余都由它派生：**决策在模型层，判定在工具层，编排在图**。
规则引擎不会替模型下结论，图也不会替模型选工具。

---

## 3. 关键设计决策

### D1 · 工具集里没有 publish —— 结构性防线优先于提示词

- **常见做法**：给足全套工具，在 system prompt 里写"未经确认不要发布"。
- **我们的选择**：只提供 `fetch_material` / `check_rules` / `query_slots` / `save_draft` 四个工具。
  `save_draft` 唯一的产物是 `runs/drafts.json` 里的一条草稿记录，没有任何对外可见副作用。
- **为什么**：OWASP 把「智能体被赋予的权限超过任务所需」单列为一项风险 Excessive Agency，
  其缓解措施第一条就是**最小化工具集合**。护栏（提示词）会被绕过，**能力边界不会依赖模型是否听话**。
  评测里"越权发布"是本题的关键缺陷——我们让它在架构上无法发生，而不是祈祷它不发生。
- **可验证**：`tests/test_tools.py::test_registry_contains_no_publish_capability` 断言工具注册表的键集合
  等于白名单；`tests/test_decision_paths.py::test_no_publish_tool_and_no_publish_wording`
  再对最终结论做一次发布措辞扫描。任何人新增写工具都会让测试变红。

### D2 · 规则判定下沉到工具层，模型只读结果

- **常见做法**：把规则全文塞进 system prompt，让模型自己判。
- **我们的选择**：`rules.json` 是数据，工具层实现三个算子（`eq`/`non_empty`/`contains_none_of`），
  输出「命中了哪条、严重程度如何」。**是否阻断由模型决定，但"命中了什么"不由模型决定。**
- **为什么**：OWASP 明确要求 **authorization 在下游系统执行，而不是让模型判断某事是否允许**（complete mediation）。
- **为什么不让模型算**：同一句话今天判过、明天判不过，trace 就不可复现了。

### D3 · 幂等：`request_id` + payload 指纹

- **常见做法**：每次调 `save_draft` 就写一条新记录，然后贴一张"我只存了一份"的截图。
- **我们的选择**：`save_draft` 以 `(request_id, payload_fingerprint)` 为主键。
  - 同 key 同 body → 返回**同一个 `draft_id`**，标记 `duplicate=true`，草稿总数不变；
  - 同 key 不同 body → **报错**，不静默重放（这是最容易被忽略的坑）；
  - 不同 key → 正常新建。
- **为什么**：一是 OWASP 之外更硬的工程依据（WSO2 / AWS Builders' Library），二是本题明确把
  "相同请求重复保存只产生一份草稿"写进了参考实例。
- **为什么值得做成亮点**：幂等是**可以断言**的——跑两遍数文件里的记录数。这比任何主观描述都硬。
- **指纹里刻意排除自由文本 `note`（实测教训）**：`note` 由模型生成，每次措辞都不同。
  第一版把它算进指纹后，"同一 request_id 重复保存"被判定成 `IDEMPOTENCY_CONFLICT`，
  模型转而去追问用户要不要换个 request_id —— 直接违反题目"重复保存只产生一份草稿"的验收点。
  根因是混淆了两件事：**幂等键管的是"同一件事"，不是"同一串字符"**。
  模型生成的自由文本天然不适合作为身份的一部分，身份应该落在 `material_id + slot + confirmed` 上。

### D4 · 步数上限：自己计数，两条防线

- **做法**：state 里自维护 `step_count`（真正的 ReAct 迭代数）+ LangGraph 的
  `recursion_limit = 2 × max_steps + 1`（图深度硬顶）。
- **为什么两条**：公开实践反复强调 **`recursion_limit` 不等于迭代次数**，一次 ReAct 循环可能消耗多个深度单位，
  只靠它会算不准；而只靠自己的计数器则挡不住图内部的意外深调用。
- **超限行为**：捕获 `GraphRecursionError` 或计数器触顶时，返回 `status = MAX_STEPS_EXCEEDED`
  并带上已完成的步骤摘要，**绝不补全一个"看起来完成了"的结论**。CLI 退出码非 0。

### D5 · 确认边界：`request_clarification` 与工具失败语义隔离

- **做法**：模型可选动作之一是 `request_clarification`（向用户提问），与 `save_draft` 严格区分。
  凡是需要人工拍板的项，进 `pending_confirmations` 列表，`draft_status` 保持 `pending_confirm`。
- **依据**：LangChain HITL 文档把 `respond` 定义为"人替代工具作答"，并**明确警告不可用它来拒绝有副作用的工具**，
  因为其返回值会被模型当成成功结果。我们对工具失败原样透传，不外敷一层"成功"。
- **待办（可选加分）**：用 LangGraph 的 `interrupt()` + `MemorySaver` 实现真正的审批闸门，
  让 pending 状态可跨进程恢复。这属于"多了更好"，不在核心链路上。

### D6 · 双 trace：真实模型与 Mock 分开存放

- **做法**：`examples/real_llm/` 放真实模型跑的，`examples/mock/` 放脚本 Mock 跑的（CI 用）。
- **边界写死**：Mock 是**测试替身**，不是模型。它按脚本吐动作，能证明流程与状态机正确，
  **不能证明模型的决策能力**。README 与本文都把这句话写进正文，而不是等评审来问。
- **为什么分开**：混在一个目录里，评审无法判断哪条观察来自真实模型，诚实度会被打折扣。

### D7 · 故障注入开关

- **做法**：`FAIL_INJECTION=save_draft|query_slots`，一条环境变量让对应工具返回失败。
- **为什么**：公开实践强调要 **测失败路径而不是只测 happy path**。用开关复现比临时改代码可信，
  评审敲一条命令就能重现"保存失败"这条必验点。

### D8 · 模型层踩坑：V4.1-Flash-1017 的思考模式

- **模型**：`deepseek-flash`（即 DeepSeek-V4.1-Flash-1017，2026-10-17 发布，MIT，1M 上下文，支持 JSON 输出与工具调用）。
- **坑**：该版本**默认开启思考模式**，思维链要占用 `max_tokens` 预算，表现之一就是官方文档承认的
  "JSON Output 偶发返回空内容"。我们传 `extra_body={"thinking": {"type": "disabled"}}` 显式关闭，
  并在收到 `reasoning_content` 时打告警——因为一旦投产环境改回思考模式，这个 bug 会复现得很隐蔽。
- **温度**：思考模式下 `temperature` 无效；非思考模式下 DeepSeek 官方对代码/数学类推荐 `0.0`。
  我们取 `0`，换取可复现的执行记录。
- **另一坑**：早期资料里的 `deepseek-chat` / `deepseek-reasoner` 已退役，填了会拿到错的东西或直接失败。
  `.env.example` 里写明了这一点。

### D9 · 输出样例必须"不可照抄"（实测发现的坑）

DeepSeek 的 JSON Output **要求**提示词里给出输出样例，否则不启用该能力。最初我们把样例写成
「冷启动 → `fetch_material`」，结果实测中模型在多组用例的第一步都**逐字复述了样例的 rationale**：

```
rationale: "素材元数据尚未读取，先确认基础信息与缺失字段"   # 与样例完全一致
action: "fetch_material"
```

这不影响功能（动作本身是合理的），但会让 trace 里的「决策依据」看起来像模板填充，
直接削弱 ReAct 真实性这条得分点。

**改法**：把样例换成一个中间动作 `query_slots`，`rationale` 写成占位描述，
并显式声明"样例仅用于说明 JSON 结构与字段位置，不是建议你执行它"。改后同一份探针的结果：

| 对话状态 | 模型选择的动作 | 模型自述依据（节选） |
|---|---|---|
| 冷启动 M04 | `fetch_material` | "需要先获取素材 M04 的详细信息，才能判断其平台、内容与发布要求是否匹配" |
| 已 fetch，M02 缺 `copyright.portrait` 与 `review_doc_ids` | `request_clarification` | "缺少肖像授权与审核文档，属于硬性合规缺口，需先向用户澄清补齐" |
| M01 规则通过但 18:00 被占用 | `request_clarification` | "硬性要求 18:00 不可用，需用户确认" |

三条路径由**同一份提示词、同一个模型**在不同工具返回下产生，理由各不相同且针对各自的观察结果。
复现命令：`.venv/Scripts/python.exe scripts/probe_decision.py`。

这条经验的一般性结论是：**在需要模型产出"决策依据"并把它作为评测材料的场景里，
few-shot 样例本身就是污染源**；样例应该放在远离首步决策的位置，并配一句显式否定。

### D10 · 工具签名必须像 API 文档一样显式暴露

最初的系统提示词里工具只写了名字和一句话说明，没写参数名。真实模型的表现是：

```
step 4  save_draft  {request_id, material_id, slot, platform}  → INVALID_PARAMS
step 5  save_draft  {request_id, material_id, slot, date}      → INVALID_PARAMS
step 8  save_draft  {request_id, material_id, slot, status}    → INVALID_PARAMS
最终  MAX_STEPS_EXCEEDED，任务未完成
```

模型把八步预算全部消耗在**猜参数名**上。指望它从 `TypeError` 的错误文本里反推正确签名，
是拿预算去赌博。

**改法**：工具签名从 `ScheduleTools.describe()` 生成后拼进系统提示词，
并且加一条测试断言文档中的参数名集合等于 `inspect.signature` 的参数集合。
改完之后同一个 M04 用例走 4 步收敛，每一步都是有效推进。

这一步的意义不止于"修了一个 bug"：**工具即 API，可用性文档是不可省略的交付物**，
Agent 场景也不例外。

---

## 4. 亮点总结：我们的做法 vs 常见做法

| 维度 | 常见做法 | 我们 | 依据 |
|---|---|---|---|
| 防止越权 | 提示词里写"不要发布" | 工具集里根本没有发布能力 + 测试守护白名单 | OWASP LLM06/LLM08、guardrails vs sandboxes |
| 重复提交 | 靠展示截图说明"只存了一份" | `request_id` + body 指纹，重复请求返回同一 `draft_id` 且总数不变，可断言 | Stripe/AWS/WSO2 幂等设计 |
| 步数上限 | 只设 `recursion_limit` | 自维护迭代计数 + 图深度上限双防线，超限返回明确未完成状态 | LangGraph 官方公式与实践教训 |
| 失败处理 | 重试到成功，或记录"已完成" | 原样透传失败，`draft_status` 如实标记为未保存 | LangChain HITL 对 `respond` 的警告 |
| Mock | 和真实输出混在一起 | 分目录存放，并主动声明 Mock 不证明模型决策 | 评测对 Mock 边界的明文要求 |
| 异常复现 | 让评审自己改代码 | `FAIL_INJECTION` 一条命令复现 | 失败路径测试实践 |
| LLM 输出样例 | 直接把首步应做的事写进样例 | 样例选用中间动作 + 占位 rationale + 显式否定 | 本项目实测（见 D9） |
| 工具可用性文档 | 只写工具名，让模型从报错里学 | 签名从代码生成后注入提示词，并用测试守护同步 | 本项目实测（见 D10） |

---

## 5. 明确不具备的能力

不藏拙，以下能力本项目**没有**，且是有意为之：

1. **没有真实发布能力**，也不打算有。
2. **没有向量检索/语义匹配**——这道题不需要。
3. **没有多 Agent 协作**——评测明确写了非必需。
4. **没有持久化数据库**——草稿落在 JSON 文件，重启后仍在，但没有并发控制。多个进程同时写会打架。
5. **Mock 推理能力为零**——它能复现流程，不能复现"模型是怎么想的"。
6. **规则引擎是玩具级**——三个算子、字符串包含匹配，不做语义判断。"最强"换个说法就检测不出来。

---

## 6. 参考来源

| # | 来源 | 链接 |
|---|---|---|
| 1 | OWASP LLM Top 10 — Excessive Agency | https://genai.owasp.org/llmrisk2023-24/llm08-excessive-agency/ |
| 2 | OWASP LLM06:2025 Excessive Agency 解读与实践清单（2026-10 复审） | https://safeguard.sh/resources/blog/ai-agents-break-security-why-and-how-to-contain-them |
| 3 | Agent guardrails versus sandboxes: a hands-on checklist | https://safeguard.sh/resources/blog/agent-guardrails-vs-sandboxes-a-hands-on-checklist |
| 4 | WSO2 — Idempotency Keys for APIs（含 AI agent 章节） | https://wso2.com/api-platform/learn/idempotency-keys-for-apis |
| 5 | Idempotency Keys Make Retried Mutations Safe（指纹/作用域/TTL） | https://nalar.dev/idempotency-keys-make-retried-mutations-safe |
| 6 | How to Design Idempotent APIs for Safe Retries（含 AWS Builders' Library 实践） | https://codinizer.com/?p=2822 |
| 7 | LangGraph 官方 — 运行 Agent / recursion_limit | https://github.langchain.ac.cn/langgraph/agents/run_agents/ |
| 8 | LangGraph 实践 — recursion_limit 不等于迭代次数 | http://theneuralbase.com/react-prompting/learn/advanced/iteration-limit-for-cost-control |
| 9 | LangChain — Human-in-the-loop（approve/edit/reject/respond 与 respond 陷阱） | https://docs.langchain.com/oss/python/langchain/human-in-the-loop |
| 10 | 事务型 Agent：staging / validation / commit 三层模式 | https://ubos.tech/?p=52277/ |
| 11 | DeepSeek 官方 — JSON Output 指南（空 content 已知问题） | https://api-docs.deepseek.com/guides/json_mode/ |
| 12 | DeepSeek OpenAI 兼容性与思考模式参数变更 | https://deepseekai.guide/?p=95/ |
| 13 | DeepSeek-V4.1-Flash 模型规格（阿里云百炼文档） | https://www.alibabacloud.com/help/tc/model-studio/deepseek-v4-1-flash |
| 14 | DeepSeek V4.1 Flash 1017 规格与发布信息（2026-10-17） | https://www.gradually.ai/en/ai-models/deepseek-v4.1-flash-1017 |

### LangGraph 在本项目中的定位

- **版本**：`langgraph==1.2.17`（PyPI 当前稳定版，2026-10-24 发布）。
- **我们使用它的**：`StateGraph` 编排、条件边路由、`recursion_limit` 硬顶、（可选）`checkpointer` + `interrupt` 表达人工审批。
- **我们不使用它的**：`prebuilt.create_react_agent`。原因是它的每一步决策藏在 `messages` 列表里，
  无法干净地取出我们要的 `rationale` 字段直接落盘成 trace——而"清晰的决策依据"正是这道考核的核心。
- **不承担**：业务判定（交给工具层）以及任何编排之外的智能。

---

## 7. 实现状态

| 模块 | 状态 | 说明 |
|---|---|---|
| `src/config.py` 配置与故障注入开关 | 已实现 | |
| `src/agent/llm.py` 模型双入口 | 已实现 | 含 thinking 关闭与空返回兜底 |
| `data/` 合成数据与规则 | 已实现 | 四素材 + 四规则 |
| `src/tools/schedule_tools.py` 业务工具 | 已实现 | 含幂等、指纹、冲突时段拒绝、故障注入、工具签名导出 |
| `src/agent/graph.py` ReAct 状态机 | 已实现 | 双预算防线、`next_action` 回填、保存状态以工具为准 |
| `src/agent/prompts.py` 提示词 | 已实现 | 工具签名由代码生成注入 |
| `src/cli.py` 命令行入口 | 已实现 | 退出码 0/2/3 |
| `tests/` 测试 | 已实现 | 42 个用例，全部无需 Key |
| `examples/` 执行记录 | 已实现 | `real_llm/` 7 组、`mock/` 2 组 + 样例脚本 |
| `scripts/` | 已实现 | 接入自检、决策探针、示例一键生成 |
| LangGraph `interrupt()` 真人审批闸门 | **未实现** | 「待确认」目前是记录 + 措辞约束，不是阻塞式闸门，见 `05-retrospective.md` |

### 已验证的环境

```
Python 3.13.12（content-schedule-agent/.venv）
langgraph 1.2.17 / langchain-core 1.7.0 / pydantic 2.14.1 / openai 1.109.1 / pytest 9.2.0
DEEPSEEK_MODEL=deepseek-flash  DEEPSEEK_THINKING=disabled
```

`.venv/Scripts/python.exe scripts/check_llm.py` 通过：真实模型在 `allowed_actions` 约束下
返回可解析的 JSON 动作；`scripts/probe_decision.py` 通过：三种不同工具观察产出三条不同路径（见 D9）。
