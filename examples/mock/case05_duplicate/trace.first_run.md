# case05_duplicate — 重复提交：同一 request_id 连续两次，只能产生一份草稿（重复提交的第一次）

- `task_status`：`done`
- `draft_status`：`saved`
- `draft_id`：`DR-REQ-DUP`
- `duplicate`：`False` / `total_drafts`：`1`

| 步骤 | 动作 | 参数 | 返回摘要 | 后续动作 | 决策依据 |
|---|---|---|---|---|---|
| 1 | `fetch_material` | `{"material_id": "M04"}` | 工具 fetch_material 执行成功：{"material_id": "M04", "title": "门店探店花絮", "platform": "xiaohongshu", "content_type": "short_video | `check_rules` | 读取素材元数据 |
| 2 | `check_rules` | `{"material_id": "M04"}` | 工具 check_rules 执行成功：{"material_id": "M04", "severity": "pass", "blocked": false, "hits": [], "note": "规则结论仅供参考，是否阻断/是否请求 | `query_slots` | 元数据完整，检查规则 |
| 3 | `query_slots` | `{}` | 工具 query_slots 执行成功：{"date": "2026-10-30", "timezone": "Asia/Shanghai", "available": ["15:00", "16:00", "19:00", "21:00" | `save_draft` | 规则通过，查询时段可用性 |
| 4 | `save_draft` | `{"request_id": "REQ-DUP", "material_id": "M04", "slot": "15:00"}` | 工具 save_draft 执行成功：{"draft_id": "DR-REQ-DUP", "duplicate": false, "total_drafts": 1, "status": "draft", "message": "已保存为 | `finish` | 15:00 可用，保存草稿 |

## 最终答复

已保存草稿 DR-REQ-DUP（未发布）
