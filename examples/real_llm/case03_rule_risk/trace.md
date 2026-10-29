# case03_rule_risk — 规则风险：命中 risk + warn，不阻断但须待确认

- `task_status`：`done`
- `draft_status`：`saved`
- `draft_id`：`DR-REQ-M03`
- `duplicate`：`False` / `total_drafts`：`1`

| 步骤 | 动作 | 参数 | 返回摘要 | 后续动作 | 决策依据 |
|---|---|---|---|---|---|
| 1 | `fetch_material` | `{"material_id": "M03"}` | 工具 fetch_material 执行成功：{"material_id": "M03", "title": "洁面产品功效介绍", "platform": "douyin", "content_type": "short_video",  | `check_rules` | 先读取素材 M03 的元数据，确认必填字段是否齐全，再决定后续审核与排期步骤。 |
| 2 | `check_rules` | `{"material_id": "M03"}` | 工具 check_rules 执行成功：{"material_id": "M03", "severity": "risk", "blocked": false, "hits": [{"rule_id": "R03", "rule_name" | `query_slots` | 素材缺少必填字段 review_doc_ids，且含“行业最强”这类绝对化宣称，需先按规则检查以确认严重程度。 |
| 3 | `query_slots` | `{}` | 工具 query_slots 执行成功：{"date": "2026-10-30", "timezone": "Asia/Shanghai", "available": ["15:00", "16:00", "19:00", "21:00" | `save_draft` | 规则结论为 risk（非 block），可继续排期，但需查询 16:00 是否可用，以便给出建议时段。 |
| 4 | `save_draft` | `{"request_id": "REQ-M03", "material_id": "M03", "slot": "16:00", "note": "待确认：缺少审核归档编号(review_doc_ids)；文案含绝对化宣称“清洁力行业最强”需法务复核", "confirmed": false}` | 工具 save_draft 执行成功：{"draft_id": "DR-REQ-M03", "duplicate": false, "total_drafts": 1, "status": "draft", "message": "已保存为 | `finish` | 用户要求的 16:00 在 available 列表中，可直接按用户要求时段保存草稿，但因存在 risk/warn 待确认项，confirmed 不填 true。 |

## 最终答复

素材 M03《洁面产品功效介绍》（抖音短视频，45秒）已完成审核与排期草稿：用户要求的 16:00 在 2026-10-30 排期表中可用，已按该时段保存为草稿 DR-REQ-M03（仅草稿，未发布，需人工确认后执行）。审核结论为 risk（未阻断），存在两项待处理问题：1) 缺少审核归档编号 review_doc_ids（warn，需补件）；2) 文案含绝对化宣称“清洁力行业最强”（risk，需法务复核）。请补齐归档文档并完成法务复核后再确认发布。
