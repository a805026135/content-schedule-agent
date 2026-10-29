# examples 索引

生成时间：2026-10-29T15:10:23
模式：真实模型 deepseek-flash

> Mock 目录里的材料只证明**流程与编排正确**，不证明模型的决策能力。
> 模型决策能力的证据在 `real_llm/` 目录。两者刻意分开存放。

| 用例 | task_status | draft_status | 关注点 |
|---|---|---|---|
| [`case01_normal/`](./case01_normal/trace.md) | `done` | `saved` | 正常排期：素材完整、规则通过、时段空闲 |
| [`case02_missing_info/`](./case02_missing_info/trace.md) | `done` | `not_saved` | 素材信息缺失：未提供期望发布时间 |
| [`case03_rule_risk/`](./case03_rule_risk/trace.md) | `done` | `saved` | 规则风险：命中 risk + warn，不阻断但须待确认 |
| [`case04_slot_conflict/`](./case04_slot_conflict/trace.md) | `done` | `saved` | 时段冲突：18:00 已占用，需建议替代时段并标记待确认 |
| [`case06_save_failure/`](./case06_save_failure/trace.md) | `done` | `not_saved` | 保存失败：注入 storage 503，须如实返回未保存 |
| [`case07_step_budget/`](./case07_step_budget/trace.md) | `MAX_STEPS_EXCEEDED` | `not_saved` | 步数上限：必须返回明确的未完成状态 |
| [`case05_duplicate/`](./case05_duplicate/trace.md) | `done` | `saved` | 重复提交：同一 request_id 连续两次，只能产生一份草稿 |
