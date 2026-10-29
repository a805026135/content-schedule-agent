# case07_step_budget — 步数上限：必须返回明确的未完成状态

- `task_status`：`MAX_STEPS_EXCEEDED`
- `draft_status`：`not_saved`
- `draft_id`：`（未保存）`
- `duplicate`：`None` / `total_drafts`：`None`

| 步骤 | 动作 | 参数 | 返回摘要 | 后续动作 | 决策依据 |
|---|---|---|---|---|---|
| 1 | `fetch_material` | `{"material_id": "M01"}` | 工具 fetch_material 执行成功：{"material_id": "M01", "title": "秋季新品 30 秒短片", "platform": "douyin", "content_type": "short_video | `check_rules` | 读取素材元数据 |
| 2 | `check_rules` | `{"material_id": "M01"}` | 工具 check_rules 执行成功：{"material_id": "M01", "severity": "pass", "blocked": false, "hits": [], "note": "规则结论仅供参考，是否阻断/是否请求 | `finish` | 元数据完整，检查规则 |

## 最终答复

未完成任务：已在 2 步 / 2 次工具调用后达到上限。排期尚未可取，需要人工介入。
