# case03_rule_risk — 规则风险：命中 risk + warn，不阻断但须待确认

- `task_status`：`done`
- `draft_status`：`saved`
- `draft_id`：`DR-REQ-M03`
- `duplicate`：`False` / `total_drafts`：`1`

| 步骤 | 动作 | 参数 | 返回摘要 | 后续动作 | 决策依据 |
|---|---|---|---|---|---|
| 1 | `fetch_material` | `{"material_id": "M03"}` | 工具 fetch_material 执行成功：{"material_id": "M03", "title": "洁面产品功效介绍", "platform": "douyin", "content_type": "short_video",  | `check_rules` | 先读取素材元数据，确认必填字段是否齐全 |
| 2 | `check_rules` | `{"material_id": "M03"}` | 工具 check_rules 执行成功：{"material_id": "M03", "severity": "risk", "blocked": false, "hits": [{"rule_id": "R03", "rule_name" | `query_slots` | 素材缺少 review_doc_ids 且含绝对化宣称，需先按规则检查严重程度 |
| 3 | `query_slots` | `{}` | 工具 query_slots 执行成功：{"date": "2026-10-30", "timezone": "Asia/Shanghai", "available": ["15:00", "16:00", "19:00", "21:00" | `save_draft` | 规则结论为 risk（非 block），可继续排期，但需确认用户要求的 16:00 是否可用 |
| 4 | `save_draft` | `{"request_id": "REQ-M03", "material_id": "M03", "slot": "16:00", "note": "命中 risk/warn，需法务复核与补件"}` | 工具 save_draft 执行成功：{"draft_id": "DR-REQ-M03", "duplicate": false, "total_drafts": 1, "status": "draft", "message": "已保存为 | `finish` | 16:00 可用，按用户要求时段保存草稿并标注待确认 |

## 最终答复

审核结论为 risk（未阻断）：缺归档编号（warn）、含绝对化宣称（risk）。已按 16:00 保存草稿 DR-REQ-M03，未发布
