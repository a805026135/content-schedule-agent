# 数据契约说明（data/）

本目录为合成数据，全部字段由本项目定义，不含任何真实业务内容。
设计目标：**让每条执行路径都可被一个具体素材稳定复现**，而不是靠随机输入碰运气。

---

## 1. materials.json

| 字段 | 类型 | 含义 | 缺失时的后果 |
|---|---|---|---|
| `material_id` | string | 素材唯一标识 | 无法定位，直接返回输入错误 |
| `title` | string | 标题 | 触发"信息缺失"分支 |
| `platform` | string | 投放平台 | 触发"信息缺失"分支 |
| `duration_sec` | int | 时长 | 触发"信息缺失"分支 |
| `requested_slot` | string \| null | 用户要求的发布时段 | `null/缺失` 触发"信息缺失"分支，须向用户索取，**不得由 Agent 自行编一个** |
| `copyright.music` | string | 背景音乐授权状态 | 命中 R01，`severity=block` |
| `copyright.portrait` | string | 肖像授权状态 | 命中 R02，`severity=block` |
| `claims` | string[] | 文案中的宣称 | 命中 R04，`severity=risk` |
| `review_doc_ids` | string[] | 审核归档编号 | 空数组命中 R03，`severity=warn` |

必填字段清单（`required_fields`）定义在 `rules.json`，修改那里即可，不需要改代码。

### 素材与分支的对应关系

| 素材 | 设计意图 | 预期触发的分支 |
|---|---|---|
| **M01** | 数据完整、无风险，但要求的 18:00 已被占用（19:00 空闲） | 正常排期 → 时段冲突 → 建议替代时段 → 标记待确认 |
| **M02** | `requested_slot` 为 null（未提供发布时间要求），其余字段齐全且无风险 | 信息缺失 → 请求补件，不进入排期建议 |
| **M03** | 含绝对化宣称「最强」、无审核归档 | 规则命中 risk + warn → 不阻断，但列入待确认项 |
| **M04** | 数据完整、无风险，要求的 15:00 空闲 | 正常排期，无需调整时段 |

> `severity=block` 分支刻意不放素材：它不属于六个必验点，放进 data/ 会让数据集平白多一条。
> 该分支改由 `tests/test_tools.py` 用内联 fixture 构造素材覆盖，既保住规则引擎三档
> severity 的证据，又不让 data/ 承担无关的演示负担。

---

## 2. rules.json

规则是**数据**而非硬编码分支。工具层只实现三个算子：

| op | 语义 |
|---|---|
| `eq` | 字段值必须等于 `value` |
| `non_empty` | 字段必须是非空数组 / 非空字符串 |
| `contains_none_of` | 数组元素不得包含 `value` 列表中的任一子串 |

`severity` 三档决定 Agent 的决策空间：

- `block`：素材不得进入排期，Agent 应给出阻断结论
- `risk`：可继续，但必须列入待确认项，不得自行放行
- `warn`：可继续，提示补件

> 说明：severity 只是规则层面的结论，**不是最终动作**。是否阻断、是否换时段、
> 是否请求确认，都由模型读取工具返回后决定。规则引擎只负责输出
> "命中了什么、严重程度如何"，不替模型拍板。

---

## 3. schedule.json

| status | 含义 | Agent 可用的动作 |
|---|---|---|
| `free` | 空闲，可排 | 可作为建议时段 |
| `occupied` | 已被占用 | 不得直接使用，需寻找替代时段 |
| `restricted` | 受保护时段（如品牌直播） | 需人工确认，不得自动占用 |

---

## 4. 六个必验收点的复现方式

| 验收点 | 输入 | 复现命令（待实现） |
|---|---|---|
| 正常排期 | M04，`requested_slot=15:00` | `run --material M04` |
| 素材信息缺失 | M02 | `run --material M02` |
| 规则风险 | M03（risk + warn） | `run --material M03` |
| 规则阻断 | 由 `tests/test_tools.py` 内联 fixture 构造 | `pytest tests/test_tools.py -k block` |
| 时段冲突 | M01，18:00 被占 | `run --material M01` |
| 重复提交 | M04，同 `request_id` 跑两次 | `run --material M04 --request-id REQ-M04-A`（执行两遍） |
| 保存失败 | 任意素材 + 注入开关 | `FAIL_INJECTION=save_draft run --material M04` |

详细预期与实际结果记录在 `docs/03-evaluation.md`，对应原始记录存放于 `examples/`。
