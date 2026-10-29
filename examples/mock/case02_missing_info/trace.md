# case02_missing_info — 素材信息缺失：未提供期望发布时间

- `task_status`：`done`
- `draft_status`：`not_saved`
- `draft_id`：`（未保存）`
- `duplicate`：`None` / `total_drafts`：`None`

| 步骤 | 动作 | 参数 | 返回摘要 | 后续动作 | 决策依据 |
|---|---|---|---|---|---|
| 1 | `fetch_material` | `{"material_id": "M02"}` | 工具 fetch_material 执行成功：{"material_id": "M02", "title": "用户故事访谈剪辑", "platform": "kuaishou", "content_type": "short_video" | `request_clarification` | 先读取素材元数据，确认必填字段是否齐全 |
| 2 | `request_clarification` | `{"question": "素材 M02 缺少必填字段「期望发布时段」，请确认希望投放的时段"}` | 已记录待确认项：素材 M02 缺少必填字段「期望发布时段」，请确认希望投放的时段 | `check_rules` | 素材缺少必填字段 requested_slot，不能自行编造，需向用户索取 |
| 3 | `check_rules` | `{"material_id": "M02"}` | 工具 check_rules 执行成功：{"material_id": "M02", "severity": "pass", "blocked": false, "hits": [], "note": "规则结论仅供参考，是否阻断/是否请求 | `query_slots` | 等待用户回复期间，先完成规则检查以确认素材是否可排期 |
| 4 | `query_slots` | `{}` | 工具 query_slots 执行成功：{"date": "2026-10-30", "timezone": "Asia/Shanghai", "available": ["15:00", "16:00", "19:00", "21:00" | `finish` | 规则通过，先查询可用时段，以便给用户可选项 |

## 最终答复

素材 M02 规则检查通过，但缺失必填字段「期望发布时段」，已向用户提出确认，暂不保存草稿
