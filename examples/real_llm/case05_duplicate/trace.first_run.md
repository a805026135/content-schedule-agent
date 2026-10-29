# case05_duplicate — 重复提交：同一 request_id 连续两次，只能产生一份草稿（重复提交的第一次）

- `task_status`：`done`
- `draft_status`：`saved`
- `draft_id`：`DR-REQ-DUP`
- `duplicate`：`False` / `total_drafts`：`1`

| 步骤 | 动作 | 参数 | 返回摘要 | 后续动作 | 决策依据 |
|---|---|---|---|---|---|
| 1 | `fetch_material` | `{"material_id": "M04"}` | 工具 fetch_material 执行成功：{"material_id": "M04", "title": "门店探店花絮", "platform": "xiaohongshu", "content_type": "short_video | `check_rules` | 用户要求为 M04 排期，但未说明具体时段，且素材元数据尚未读取，先获取素材信息以确认必填字段是否齐全。 |
| 2 | `check_rules` | `{"material_id": "M04"}` | 工具 check_rules 执行成功：{"material_id": "M04", "severity": "pass", "blocked": false, "hits": [], "note": "规则结论仅供参考，是否阻断/是否请求 | `query_slots` | 素材元数据完整，用户要求的时段为 15:00，下一步需按审核规则检查素材是否存在 block/risk 问题。 |
| 3 | `query_slots` | `{}` | 工具 query_slots 执行成功：{"date": "2026-10-30", "timezone": "Asia/Shanghai", "available": ["15:00", "16:00", "19:00", "21:00" | `save_draft` | 规则检查通过，无阻断项，接下来查询排期表确认用户要求的 15:00 时段是否可用。 |
| 4 | `save_draft` | `{"request_id": "REQ-DUP", "material_id": "M04", "slot": "15:00", "note": "按用户要求时段 15:00 排期，规则检查通过（pass），待用户确认"}` | 工具 save_draft 执行成功：{"draft_id": "DR-REQ-DUP", "duplicate": false, "total_drafts": 1, "status": "draft", "message": "已保存为 | `finish` | 用户要求的 15:00 在可用列表中，规则检查通过，可直接按用户要求时段保存草稿（未确认，confirmed 不填 true）。 |

## 最终答复

素材 M04《门店探店花絮》（小红书，短视频 40 秒）必填字段完整，审核规则检查结果为 pass，无命中项。用户要求的 15:00 时段在 2026-10-30 排期表中可用，已按该时段保存为草稿（draft_id=DR-REQ-DUP）。注意：系统仅能保存草稿，内容尚未发布，需用户确认后由人工执行发布。
