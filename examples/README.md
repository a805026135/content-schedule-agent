# examples/

存放全部验证材料的**原始记录**。评估文档 `docs/03-evaluation.md` 里的每一个结论都引用这里的文件。

```
examples/
├── real_llm/   真实模型（deepseek-flash）跑出来的 7 组 —— 这是「模型决策能力」的证据
└── mock/       离线脚本跑出来的 7 组 —— 这是「流程与编排正确」的证据，零配置即可重现
```

## 两个目录的分工

| 目录 | 谁能跑 | 证明什么 | **不**证明什么 |
|---|---|---|---|
| `real_llm/` | 需要 `DEEPSEEK_API_KEY` | 模型会根据不同的工具返回选择不同的下一步 | 与模型无关的工程正确性（那是 mock/ 与 tests/ 的事） |
| `mock/` | 任何人，零配置 | 链路编排、幂等、预算、校正逻辑 | **不证明模型具备任何决策能力** |

> 明确写出来：**Mock 只验证流程，不验证真实模型的决策效果。**
> 刻意不把两者混在同一个目录里，避免 Mock 的输出被误当成模型能力的证据。

## 每个用例目录里有什么

| 文件 | 内容 |
|---|---|
| `input.json` | 本次运行的输入（素材、request_id、是否注入故障、步数上限） |
| `output.json` | 完整产出：task_status / audit_status / suggested_slot / draft_status / trace / final_answer |
| `trace.md` | 人可读表格，含步骤、工具参数、返回摘要、**后续动作**、决策依据 |
| `drafts.json` | 该用例的草稿存储落盘，可直接查看"是不是只产生了一份草稿" |

`case05_duplicate/` 额外有 `first_run.json` 与 `trace.first_run.md`，
用来核对两次执行是否真的落在同一个 `draft_id` 上。

## 重新生成

```bash
.venv/Scripts/python.exe scripts/run_examples.py                  # 真实模型
.venv/Scripts/python.exe scripts/run_examples.py --provider mock  # 离线
```

脚本会先重置每个用例的草稿存储，因此重复运行能得到一致的结果 ——
这是"可复现"的最低要求，而不是靠文档里写一句"应该可以复现"。

## 单个用例手动复现

```bash
# 时段冲突（最有代表性的一条）
.venv/Scripts/python.exe -m src.cli --material M01 --request-id REQ-M01

# 保存失败（退出码仍为 0，但 draft_status=not_saved）
.venv/Scripts/python.exe -m src.cli --material M04 --fail-injection save_draft

# 步数上限（退出码为 2）
.venv/Scripts/python.exe -m src.cli --material M01 --max-steps 2 ; echo $?
```
