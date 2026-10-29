# case04_slot_conflict — 时段冲突：18:00 已占用，需建议替代时段并标记待确认

- `task_status`：`done`
- `draft_status`：`saved`
- `draft_id`：`DR-REQ-M01`
- `duplicate`：`False` / `total_drafts`：`1`

| 步骤 | 动作 | 参数 | 返回摘要 | 后续动作 | 决策依据 |
|---|---|---|---|---|---|
| 1 | `fetch_material` | `{"material_id": "M01"}` | 工具 fetch_material 执行成功：{"material_id": "M01", "title": "秋季新品 30 秒短片", "platform": "douyin", "content_type": "short_video | `check_rules` | 先读取素材 M01 的元数据，确认必填字段是否齐全，再决定后续审核与排期动作。 |
| 2 | `check_rules` | `{"material_id": "M01"}` | 工具 check_rules 执行成功：{"material_id": "M01", "severity": "pass", "blocked": false, "hits": [], "note": "规则结论仅供参考，是否阻断/是否请求 | `query_slots` | 素材元数据完整无缺失，下一步按审核规则检查 M01 是否存在 block/risk/warn 问题。 |
| 3 | `query_slots` | `{}` | 工具 query_slots 执行成功：{"date": "2026-10-30", "timezone": "Asia/Shanghai", "available": ["15:00", "16:00", "19:00", "21:00" | `save_draft` | 审核通过无风险，接下来查询排期表确认用户要求的 18:00 是否可用。 |
| 4 | `save_draft` | `{"request_id": "REQ-M01", "material_id": "M01", "slot": "19:00", "note": "用户原要求 18:00，但该时段已被 M90（主推大片）占用；建议改为 19:00，待用户确认", "confirmed": false}` | 工具 save_draft 执行成功：{"draft_id": "DR-REQ-M01", "duplicate": false, "total_drafts": 1, "status": "draft", "message": "已保存为 | `finish` | 用户要求的 18:00 已被 M90 占用，需从可用时段中挑一个作为建议并保存为待确认草稿，不能擅自替用户改期。 |

## 最终答复

素材 M01（秋季新品 30 秒短片，抖音短视频，30 秒）元数据完整、审核规则检查通过（severity=pass，无命中规则）。用户要求的 18:00 在 2026-10-30 已被 M90（主推大片）占用，无法按原时段排期；已从可用时段中选取 19:00 作为建议，并保存为草稿 DR-REQ-M01（状态：draft，未发布，需用户确认后由人工执行发布）。若用户坚持 18:00，需另行协调 M90 的排期。
