# case04_slot_conflict — 时段冲突：18:00 已占用，需建议替代时段并标记待确认

- `task_status`：`done`
- `draft_status`：`saved`
- `draft_id`：`DR-REQ-M01`
- `duplicate`：`False` / `total_drafts`：`1`

| 步骤 | 动作 | 参数 | 返回摘要 | 后续动作 | 决策依据 |
|---|---|---|---|---|---|
| 1 | `fetch_material` | `{"material_id": "M01"}` | 工具 fetch_material 执行成功：{"material_id": "M01", "title": "秋季新品 30 秒短片", "platform": "douyin", "content_type": "short_video | `check_rules` | 读取素材元数据 |
| 2 | `check_rules` | `{"material_id": "M01"}` | 工具 check_rules 执行成功：{"material_id": "M01", "severity": "pass", "blocked": false, "hits": [], "note": "规则结论仅供参考，是否阻断/是否请求 | `query_slots` | 元数据完整，检查规则 |
| 3 | `query_slots` | `{}` | 工具 query_slots 执行成功：{"date": "2026-10-30", "timezone": "Asia/Shanghai", "available": ["15:00", "16:00", "19:00", "21:00" | `save_draft` | 规则通过，查询时段 |
| 4 | `save_draft` | `{"request_id": "REQ-M01", "material_id": "M01", "slot": "19:00"}` | 工具 save_draft 执行成功：{"draft_id": "DR-REQ-M01", "duplicate": false, "total_drafts": 1, "status": "draft", "message": "已保存为 | `finish` | 18:00 被占用，改用 19:00 作为建议时段 |

## 最终答复

18:00 已被占用，建议 19:00，草稿已保存，未发布
