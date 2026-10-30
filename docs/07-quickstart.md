# 试用手册：从零到跑通六种路径

面向第一次拿到本仓库的人。**照抄命令即可**，不需要理解代码。
所有命令在仓库根目录执行，Windows 用 `.venv/Scripts/python.exe`，
macOS/Linux 把 `.venv/Scripts/python.exe` 换成 `.venv/bin/python`。

---

## 0. 两分钟准备

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt

cp .env.example .env          # 想用真实模型就把 DEEPSEEK_API_KEY 填进去

.venv/Scripts/python.exe -m pytest        # 43 passed，不需要任何 Key（pytest.ini 已含 -q，命令行别再加）
```

`.env` 已在 `.gitignore` 里，不会被提交。

---

## 1. 三种运行模式，先选一个

| 模式 | 什么时候用 | 怎么触发 | 输出里怎么识别 |
|---|---|---|---|
| 真实模型 | 有 Key、想看模型真决策 | 什么都不加（有 Key 时自动用） | `_meta.provider = DeepSeekLLMClient` |
| 离线脚本 | 没 Key / 没网 / 想复现固定路径 | `--mock-script <文件>` | `_meta.model = mock:scripted` |
| 强制 mock | 有 Key 但就想跑流程 | `--provider mock` | 同上 |

离线模式**必须显式给脚本**。这是刻意的：离线替身不能代替模型做决定，
所以不存在"什么都不传也能跑"的模式，免得把脚本动作误当成模型决策。

---

## 2. 第一次运行（离线，最稳）

```bash
.venv/Scripts/python.exe -m src.cli --material M01 --request-id REQ-TRY-01 ^
    --mock-script examples/mock/case04_slot_conflict/mock_script.json ^
    --drafts-path runs/try01.json
```

单行版（PowerShell 用反引号，bash 用 `\`）：

```bash
.venv/Scripts/python.exe -m src.cli --material M01 --request-id REQ-TRY-01 --mock-script examples/mock/case04_slot_conflict/mock_script.json --drafts-path runs/try01.json
```

实测输出的关键部分：

```json
"requested_slot": "18:00",
"suggested_slot": "19:00",
"pending_confirmations": ["是否接受改到 19:00"],
"draft_status": "saved",
"task_status": "done",
"_meta": { "provider": "ScriptedMockLLMClient", "model": "mock:scripted" }
```

M01 想发 18:00，但 18:00 已被 M90 占用，模型查完时段后自己改到了 19:00，
并留下一条待人工确认项。`trace` 数组里能看到它每一步的理由（rationale）。

---

## 3. 有 Key 时跑真实模型

```bash
.venv/Scripts/python.exe -m src.cli --material M04 --request-id REQ-A --drafts-path runs/try_real.json
```

真实模型的决策会被完整记录在 `trace` 里，包括它为什么选某个动作。
和离线模式唯一的区别是 `_meta.provider` —— **不会静默假装**。

---

## 4. 六个必验点的复现命令

离线可复现（不需要 Key）：

```bash
set S=examples/mock                       # bash: export S=examples/mock
set PY=.venv/Scripts/python.exe            # bash: export PY=.venv/bin/python

# 1 正常排期
%PY% -m src.cli --material M04 --request-id REQ-A --mock-script %S%/case01_normal/mock_script.json --drafts-path runs/d1.json
# 2 信息缺失 → 不排期，转人工确认
%PY% -m src.cli --material M02 --request-id REQ-B --mock-script %S%/case02_missing_info/mock_script.json --drafts-path runs/d2.json
# 3 规则风险
%PY% -m src.cli --material M03 --request-id REQ-C --mock-script %S%/case03_rule_risk/mock_script.json --drafts-path runs/d3.json
# 4 时段冲突
%PY% -m src.cli --material M01 --request-id REQ-D --mock-script %S%/case04_slot_conflict/mock_script.json --drafts-path runs/d4.json
# 6 保存失败（工具故障注入）
%PY% -m src.cli --material M01 --request-id REQ-F --fail-injection save_draft --mock-script %S%/case06_save_failure/mock_script.json --drafts-path runs/d6.json
```

第 5 项（重复提交）要连跑两次，**两次用同一个 `--request-id`**：

```bash
%PY% -m src.cli --material M01 --request-id REQ-DUP --mock-script %S%/case05_duplicate/mock_script.json --drafts-path runs/dup.json
%PY% -m src.cli --material M01 --request-id REQ-DUP --mock-script %S%/case05_duplicate/mock_script.json --drafts-path runs/dup.json
```

实测结果：第一次 `duplicate=false`，第二次 `duplicate=true`，
两次 `draft_id` 相同，`total_drafts` 始终是 1。

---

## 5. 退出码：机器可读的"是否完成"

| 退出码 | 含义 | 怎么复现 |
|---|---|---|
| `0` | 任务完成 | 上面任何一条正常路径 |
| `2` | 达到步数/工具调用上限（**明确未完成**） | 见下方命令 |
| `3` | 模型连续多次给不出合规动作（决策失败） | 需真实模型 |
| `4` | 模型服务不可用：网络不可达 / 鉴权失败 / 限流 | 拔网线或填错 Key |

```bash
.venv/Scripts/python.exe -m src.cli --material M01 --request-id REQ-BUDGET --max-steps 2 ^
    --mock-script examples/mock/case07_step_budget/mock_script.json --drafts-path runs/b.json
echo %ERRORLEVEL%          # bash: echo $?    →  2
```

实测输出：`task_status = MAX_STEPS_EXCEEDED`、`draft_status = not_saved`、
`final_answer = "未完成任务：已在 2 步 / 2 次工具调用后达到上限。排期尚未可取，需要人工介入。"`

**`3` 和 `4` 是分开的**：一次网络抖动如果被记成"模型给不出合规动作"，
等于把环境问题算到了模型的决策能力账上。

---

## 6. 输出怎么读

一次运行会打印一个 JSON，只看这几个字段就够：

| 字段 | 说明 |
|---|---|
| `task_status` | `done` / `MAX_STEPS_EXCEEDED` / `DECISION_FAILED` |
| `draft_status` | `saved` / `not_saved` —— **只认 trace 里是否真有成功的 save_draft** |
| `suggested_slot` | 建议时段；与 `requested_slot` 不同说明发生了冲突调整 |
| `pending_confirmations` | 需要人拍板的事项，非空即表示"Agent 不自己决定" |
| `duplicate` | 本次是否命中幂等 |
| `failures` | 工具失败明细（`stage` + `reason`） |
| `trace[]` | 每一步的 `action` / `action_input` / `rationale` / `observation_summary` |
| `_meta.provider` | 真实模型还是离线脚本 |

加 `--out 路径.json` 可以把完整状态落盘，方便对比两次运行。

---

## 7. 参数速查

| 参数 | 作用 |
|---|---|
| `--material` | 必填。素材 ID：`M01` 冲突 / `M02` 缺信息 / `M03` 风险 / `M04` 正常 / `M05` 阻断 |
| `--request-id` | 幂等键。相同 ID + 相同内容 = 同一份草稿；内容变了报 `IDEMPOTENCY_CONFLICT` |
| `--requirement` | 用户的排期要求原文，默认"请审核该素材并给出发布排期建议" |
| `--provider` | `auto`（默认，有 Key 用真模型）/ `deepseek` / `mock` |
| `--mock-script` | JSON 动作脚本，指定即离线运行 |
| `--fail-injection` | `save_draft` 或 `query_slots`，注入工具故障 |
| `--max-steps` | 覆盖默认步数上限（默认 8） |
| `--drafts-path` | 草稿存哪。**多场景对比时每个场景指定一个，避免互相干扰** |
| `--out` | 把完整状态写入文件 |

---

## 7.5 用查看器看结果（可选）

不想在终端里读 JSON 的话，用 `viewer/index.html`：

```bash
.venv/Scripts/python.exe -m src.cli --material M01 --request-id REQ-M01 \
    --mock-script examples/mock/case04_slot_conflict/mock_script.json \
    --out runs/demo.json --drafts-path runs/demo-drafts.json
# 浏览器打开 viewer/index.html，把 runs/demo.json 拖进去
# 或者直接打开 viewer/index.html?sample=1 看内置样例
```

零依赖单文件，不在运行链路里。多场景对比记得换 `--out`，否则会覆盖上一次的结果。

## 8. 出问题先看这里

| 现象 | 原因与处理 |
|---|---|
| 退出码 4，`APIConnectionError` | 网络到 `api.deepseek.com` 不通。这是环境问题不是代码问题，改用 `--mock-script` 离线复现 |
| 退出码 3 | 模型连续输出不合规 JSON，已重试到上限。属于决策失败，看 stderr |
| `IDEMPOTENCY_CONFLICT` | 同一个 `request_id` 但内容变了。换一个 `request_id` |
| `INVALID_PARAMS` | 工具参数不对。提示词里的工具签名是从代码自动生成的，一般不会出现 |
| 两次运行结果互相干扰 | 没给 `--drafts-path`，都写到了 `runs/drafts.json` |

---

## 9. 一句话记住这个项目

> 工具集里**只有** `fetch_material` / `check_rules` / `query_slots` / `save_draft` 四个，
> 没有 `publish`、没有 `post`、没有 `release` —— 越权发布不是靠提示词劝阻，
> 而是压根没有这个工具可调。
