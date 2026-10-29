# case02_missing_info — 素材信息缺失：未提供期望发布时间

- `task_status`：`done`
- `draft_status`：`not_saved`
- `draft_id`：`（未保存）`
- `duplicate`：`None` / `total_drafts`：`None`

| 步骤 | 动作 | 参数 | 返回摘要 | 后续动作 | 决策依据 |
|---|---|---|---|---|---|
| 1 | `fetch_material` | `{"material_id": "M02"}` | 工具 fetch_material 执行成功：{"material_id": "M02", "title": "用户故事访谈剪辑", "platform": "kuaishou", "content_type": "short_video" | `request_clarification` | 先读取素材 M02 的元数据，确认必填字段是否齐全，再决定后续审核与排期动作。 |
| 2 | `request_clarification` | `{"question": "素材 M02（用户故事访谈剪辑，快手短视频）缺少必填字段「期望发布时段」。请确认希望的具体发布时间（例如 2026-10-30 的某个时段），我再据此查询排期并给出建议。"}` | 已记录待确认项：素材 M02（用户故事访谈剪辑，快手短视频）缺少必填字段「期望发布时段」。请确认希望的具体发布时间（例如 2026-10-30 的某个时段），我再据此查询排期并给出建议。 | `check_rules` | 素材缺少必填字段 requested_slot（用户未指定发布时间），按硬约束不能自行编造，需先向用户索取。 |
| 3 | `check_rules` | `{"material_id": "M02"}` | 工具 check_rules 执行成功：{"material_id": "M02", "severity": "pass", "blocked": false, "hits": [], "note": "规则结论仅供参考，是否阻断/是否请求 | `query_slots` | 用户尚未回复期望时段，但可先查询排期表了解可用时段，为后续建议做准备；同时先做规则检查以确认素材是否可排期。 |
| 4 | `query_slots` | `{}` | 工具 query_slots 执行成功：{"date": "2026-10-30", "timezone": "Asia/Shanghai", "available": ["15:00", "16:00", "19:00", "21:00" | `finish` | 规则检查通过，素材可排期；用户仍未给出期望时段，先查询排期表获取可用时段，以便给出建议。 |

## 最终答复

素材 M02（用户故事访谈剪辑，快手短视频，60 秒）元数据基本完整，版权（音乐 licensed、肖像 authorized）无问题，审核规则检查结果为 pass，无阻断项，可以排期。唯一缺失的必填字段是「期望发布时段」，用户尚未提供，因此暂未保存草稿。2026-10-30 可用时段为 15:00、16:00、19:00、21:00（18:00 已被 M90 占用，20:00 为品牌直播受保护时段）。请确认希望的具体时段，我再据此保存草稿。
