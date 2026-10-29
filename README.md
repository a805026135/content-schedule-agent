# 素材审核与发布排期 Agent

> **仓库**：https://github.com/a805026135/content-schedule-agent
> **题号**：第 1 题 · 素材审核与发布排期 Agent
> **运行方式**：CLI
> **模型**：`deepseek-flash`（DeepSeek-V4.1-Flash-1017），非思考模式，`temperature=0`
> **编排**：LangGraph `1.2.17`（手写 `StateGraph`，不用 `prebuilt.create_react_agent`）

帮助内容团队找出待处理素材，产出**无冲突、可确认**的发布排期建议。
系统**只能保存草稿，不具备任何发布能力**。

---

## 30 秒跑起来

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # macOS/Linux: .venv/bin/python
cp .env.example .env          # 可选：填入 DEEPSEEK_API_KEY 以使用真实模型
.venv/Scripts/python.exe -m pytest          # 42 个用例，无需任何 Key
.venv/Scripts/python.exe -m src.cli --material M01 --request-id REQ-M01
```

### 没有 Key 时怎么用

`pytest` 的 43 个用例**完全不需要 Key**。CLI 若要离线运行，必须显式给出动作脚本
（离线替身本身不能替模型做决定，所以不存在"什么都不传也能跑"的模式）：

```bash
.venv/Scripts/python.exe -m src.cli --material M01 --request-id REQ-MOCK \
    --mock-script examples/mock/mock_script_example.json

# 单独重放某一条路径（每个用例目录都自带 mock_script.json，自包含）
.venv/Scripts/python.exe -m src.cli --material M01 --request-id REQ-M01 \
    --mock-script examples/mock/case04_slot_conflict/mock_script.json
```

输出的 `_meta.provider` 会写明是 `ScriptedMockLLMClient` / `model=mock:scripted`，
**不会静默假装成真实模型**。更多说明见 `examples/README.md`。

---

## 评审入口（建议按这个顺序看）

| 顺序 | 看什么 | 为什么先看它 |
|---|---|---|
| 1 | `docs/01-requirements.md` | 成功标准，后面所有断言都对照它 |
| 2 | `docs/02-solution.md` | 调用流程、**为什么这么设计**（含业界依据与踩坑实录） |
| 3 | `examples/real_llm/README.md` | 六个必验点 + 超限用例的真实运行结论索引 |
| 4 | `examples/real_llm/case04_slot_conflict/trace.md` | 最有代表性的一条：时段冲突后建议替代时段 |
| 5 | `docs/03-evaluation.md` | 测试预期 vs 实际结果、失败用例与已知限制 |
| 6 | `docs/04-ai-usage.md` / `docs/05-retrospective.md` | AI 使用边界与回顾 |
| 7 | `docs/06-submission-note.md` | 可直接粘贴到答题区域的提交说明 |

想跳过文档直接看行为，两条命令：

```bash
.venv/Scripts/python.exe -m pytest                                   # 43 passed
.venv/Scripts/python.exe scripts/run_examples.py                      # 重新生成全部验证材料
```

---

## 退出码是机器可读的接口

| 退出码 | 含义 |
|---|---|
| `0` | 任务完成 |
| `2` | 达到步数或工具调用上限，属于**明确的未完成状态** |
| `3` | 模型连续多次无法给出合规动作（**决策失败**） |
| `4` | 模型服务不可用：网络不可达 / 鉴权失败 / 限流（**环境问题，不是决策问题**） |

```bash
# 不依赖网络即可复现「步数超限 → 退出码 2」
.venv/Scripts/python.exe -m src.cli --material M01 --request-id REQ-M01 --max-steps 2 \
    --mock-script examples/mock/case07_step_budget/mock_script.json ; echo $?   # => 2
```

`3` 与 `4` 分开是有意的：一次网络抖动若被当成"模型给不出合规动作"，
等于把环境问题记到了模型的决策能力账上。

"未完成 ≠ 假装成功"因此是可自动验证的，不依赖人工读文字判断。

---

## 六个必验收点的复现命令

| 验收点 | 命令 | 预期关键点 |
|---|---|---|
| 正常排期 | `--material M04 --request-id REQ-A` | `audit_status=passed`，`suggested_slot=15:00`，`draft_status=saved` |
| 素材信息缺失 | `--material M02 --request-id REQ-B` | 触发 `request_clarification`，`draft_status=not_saved` |
| 规则风险 | `--material M03 --request-id REQ-C` | `audit_status=risky`，`pending_confirmations` 非空 |
| 时段冲突 | `--material M01 --request-id REQ-D` | 18:00 被占 → `suggested_slot=19:00`，且待确认项非空 |
| 重复提交 | 同 `--request-id REQ-E` 连跑两次 | 两次 `draft_id` 相同，`total_drafts=1`，第二次 `duplicate=true` |
| 保存失败 | `--fail-injection save_draft` | `draft_status=not_saved`，`failures` 里 `TOOL_UNAVAILABLE` |

生成好的原始材料在 `examples/real_llm/<用例>/`（`input.json` / `output.json` / `trace.md` / `drafts.json`），
无需重跑即可查阅。离线版本在 `examples/mock/<用例>/`，覆盖同样七条路径。

---

## 项目结构

```
content-schedule-agent/
├── src/
│   ├── cli.py                    CLI 入口，含退出码语义
│   ├── config.py                 环境变量 -> Settings，含故障注入开关
│   ├── agent/
│   │   ├── graph.py              ReAct 状态机（本项目的核心）
│   │   ├── llm.py                真实模型 / 离线 Mock 双入口 + 输出契约
│   │   ├── prompts.py            系统提示词（工具签名从代码生成）
│   │   └── state.py              State 与 trace 记录的 schema
│   └── tools/
│       └── schedule_tools.py     四个确定性工具 + 幂等写 + 故障注入
├── tests/                        43 个用例
├── examples/
│   ├── real_llm/                 真实模型跑出来的六组 + 超限用例（决策能力的证据）
│   └── mock/                     离线脚本跑出来的七组，零配置复现（流程的证据）
├── data/                         合成素材、规则、排期表
├── docs/                         01 ~ 06
└── scripts/                      接入自检、决策探针、示例生成
```

---

## 一句话总结设计取向

> 这道题的高分依据（冲突调整 / 确认边界 / 防重复）**全部可以用结构性手段保证**，
> 所以本项目把它们做进代码而不是写进提示词：工具集里没有 `publish`、
> `request_id` + 指纹做幂等、保存状态以工具结果为准。
> 模型只负责它不可替代的那一部分 —— 根据观察选择下一步动作。

---

## 已知限制

- 规则引擎是字符串/相等判定级别的玩具实现，"最强"换个同义表述就测不出来。
- 草稿不会自动落到任何真实发布系统；本仓库**刻意不提供发布能力**。
- 已有草稿不回填 `schedule.json`，跨素材冲突只在同一份草稿存储内检测。
- 详见 `docs/03-evaluation.md` 的「已知限制」一节。
