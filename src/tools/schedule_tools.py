"""业务工具层：确定性函数，不含任何决策逻辑。

设计原则（与 docs/02-solution.md 的 D1/D2/D3 对应）：

1. **工具集里没有 publish**。"不得擅自发布"不靠提示词约束，而是靠工具的能力集合
   在结构上不存在发布入口。这是 OWASP LLM06/LLM08 Excessive Agency 推荐的
    mitigation 手段 —— 最小权限应当体现在 Actor Layer 的能力上，而不是写在话术里。
2. **工具只做业务判定，不做决策**。规则引擎输出"命中了什么、严重程度如何"，
   时段查询输出"哪些可用、哪些受限"，至于换哪个时段、要不要请用户确认，全部交给模型。
   一旦工具开始替模型拍板，"模型根据观察调整动作"这条考核要求就无法兑现。
3. **写操作幂等**。`save_draft` 以 `request_id` 为幂等键，同时存 payload 指纹：
   同 key 同 body → 返回同一份草稿（duplicate=true）；同 key 不同 body →
   报 IDEMPOTENCY_CONFLICT，而不是静默重放旧响应（AWS Builders' Library 明确指出
   只存 key 不存 body 会让不同请求被重放成错误的旧结果）。
4. **失败如实上报**。任何失败都表现为 ok=False + 明确的 error code，
   绝不会为了"链路跑通"而在失败时写入成功状态。

每个工具的返回都是统一的 ToolResult，可直接序列化成喂给模型的 observation 字符串。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ..config import Settings

# ---- 错误码。刻意集中定义，便于测试与文档引用 ----
ERR_MATERIAL_NOT_FOUND = "MATERIAL_NOT_FOUND"
ERR_INVALID_PARAMS = "INVALID_PARAMS"
ERR_TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
ERR_UNKNOWN_DATE = "UNKNOWN_DATE"
ERR_UNKNOWN_SLOT = "UNKNOWN_SLOT"
ERR_SLOT_OCCUPIED = "SLOT_OCCUPIED"
ERR_SLOT_RESTRICTED = "SLOT_RESTRICTED"
ERR_SLOT_TAKEN_BY_DRAFT = "SLOT_TAKEN_BY_DRAFT"
ERR_IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
ERR_STORE_IO = "DRAFT_STORE_IO_ERROR"


@dataclass(frozen=True)
class ToolResult:
    """工具返回值的统一外壳。

    ok 与 data/error 互斥：ok=True 时 error 必为 None，反之亦然。
    这个不变量由构造时的校验保证，避免下游出现"既成功又有错"的模糊状态。
    """

    tool: str
    params: dict[str, Any]
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    def __post_init__(self) -> None:
        if self.ok and (self.error_code or self.error_message):
            raise ValueError("ok=True 的工具结果不得携带错误信息")
        if not self.ok and not self.error_code:
            raise ValueError("ok=False 的工具结果必须给出 error_code")

    @property
    def summary(self) -> str:
        """给模型看的紧凑摘要。刻意截断超长字段，避免把整个数据集灌进上下文。"""
        payload = self.data if self.ok else {
            "error": self.error_code,
            "message": self.error_message,
        }
        text = json.dumps(payload, ensure_ascii=False)
        if len(text) > 1200:
            text = text[:1200] + "...(已截断)"
        prefix = "成功" if self.ok else "失败"
        return f"工具 {self.tool} 执行{prefix}：{text}"


def _dig(material: dict[str, Any], dotted: str) -> tuple[bool, Any]:
    """按点号路径取值。返回 (是否存在有效值, 值)。

    "是否存在"与"值是否为 None"是两件事：JSON 里显式写 null 表示
    "该字段已知未提供"，键不存在表示"根本没有这个字段"。对排期业务而言两者都是缺失，
    但对诊断而言区分它们有价值，所以分别返回。
    """
    cur: Any = material
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, tuple, dict, str)) and len(value) == 0:
        return True
    return False


def _fingerprint(payload: dict[str, Any]) -> str:
    """请求体指纹。用于识别"同一个幂等键配了不同的 body"。

    sort_keys 保证同一份语义的内容得到同一个指纹，不受字段顺序影响。
    """
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ScheduleTools:
    """工具运行时。

    以实例而非模块级函数存在，是为了让 tests/ 能用 tmp_path 构造独立的草稿存储，
    不必共享真实 runs/ 目录。副作用仅限于 drafts_path 这一个文件。
    """

    def __init__(
        self,
        settings: Settings,
        *,
        data_dir: Optional[Path] = None,
        drafts_path: Optional[Path] = None,
        fail_injection: Optional[str] = None,
    ) -> None:
        self._settings = settings
        self._data_dir = Path(data_dir) if data_dir else settings.data_dir
        self._drafts_path = Path(drafts_path) if drafts_path else settings.runs_dir / "drafts.json"
        self._fail_injection = fail_injection or settings.fail_injection

    # ---------- 数据读取 ----------

    def _load_json(self, name: str) -> dict[str, Any]:
        path = self._data_dir / name
        if not path.exists():
            raise FileNotFoundError(f"合成数据缺失：{path}")
        return json.loads(path.read_text(encoding="utf-8"))

    # ---------- 工具 1：读取素材 ----------

    def fetch_material(self, material_id: str) -> ToolResult:
        params = {"material_id": material_id}
        if not isinstance(material_id, str) or not material_id.strip():
            return ToolResult(
                "fetch_material", params, False,
                error_code=ERR_INVALID_PARAMS,
                error_message="material_id 必须是非空字符串",
            )

        dataset = self._load_json("materials.json")
        found = next(
            (m for m in dataset["materials"] if m.get("material_id") == material_id), None
        )
        if found is None:
            available = [m.get("material_id") for m in dataset["materials"]]
            return ToolResult(
                "fetch_material", params, False,
                error_code=ERR_MATERIAL_NOT_FOUND,
                error_message=f"素材 {material_id} 不存在，当前数据集包含：{available}",
            )

        ruleset = self._load_json("rules.json")
        missing = [
            f
            for f in ruleset.get("required_fields", [])
            if _is_missing(_dig(found, f)[1])
        ]

        return ToolResult(
            "fetch_material", params, True,
            data={
                "material_id": found.get("material_id"),
                "title": found.get("title"),
                "platform": found.get("platform"),
                "content_type": found.get("content_type"),
                "duration_sec": found.get("duration_sec"),
                "requested_slot": found.get("requested_slot"),
                "owner": found.get("owner"),
                "tags": found.get("tags", []),
                "copyright": found.get("copyright", {}),
                "claims": found.get("claims", []),
                "review_doc_ids": found.get("review_doc_ids", []),
                "missing_fields": missing,
                "field_complete": not missing,
            },
        )

    # ---------- 工具 2：规则检查 ----------

    def check_rules(self, material_id: str) -> ToolResult:
        params = {"material_id": material_id}
        fetched = self.fetch_material(material_id)
        if not fetched.ok:
            return ToolResult(
                "check_rules", params, False,
                error_code=fetched.error_code,
                error_message=fetched.error_message,
            )
        material = fetched.data

        ruleset = self._load_json("rules.json")
        hits: list[dict[str, Any]] = []
        for rule in ruleset.get("rules", []):
            op = rule.get("op")
            dotted = rule.get("field")
            exists, value = _dig(material, dotted)

            if op == "eq":
                violated = not exists or value != rule.get("value")
            elif op == "non_empty":
                violated = _is_missing(value)
            elif op == "contains_none_of":
                banned = rule.get("value", [])
                items = value if isinstance(value, list) else []
                violated = any(b in str(item) for item in items for b in banned)
            else:
                # 未知算子视为配置错误，宁可暴露也不要被静默跳过
                raise ValueError(f"未知规则算子：{op}（规则 {rule.get('rule_id')}）")

            if violated:
                hits.append({
                    "rule_id": rule.get("rule_id"),
                    "rule_name": rule.get("name"),
                    "severity": rule.get("severity"),
                    "field": dotted,
                    "observed": value,
                    "message": rule.get("message"),
                })

        order = {"block": 0, "risk": 1, "warn": 2}
        worst = min((h["severity"] for h in hits), key=lambda s: order.get(s, 9)) if hits else "pass"
        return ToolResult(
            "check_rules", params, True,
            data={
                "material_id": material_id,
                "severity": worst,
                "blocked": worst == "block",
                "hits": hits,
                # 重申：这是规则层面的结论，不是最终动作，最终动作由模型决定
                "note": "规则结论仅供参考，是否阻断/是否请求确认由你（模型）决定",
            },
        )

    # ---------- 工具 3：查询可用时段 ----------

    def query_slots(self, date: Optional[str] = None, platform: Optional[str] = None) -> ToolResult:
        params = {"date": date, "platform": platform}
        if self._fail_injection == "query_slots":
            return ToolResult(
                "query_slots", params, False,
                error_code=ERR_TOOL_UNAVAILABLE,
                error_message="排期服务暂时不可用（故障注入），请稍后重试或向用户说明无法查询",
            )

        schedule = self._load_json("schedule.json")
        dataset_date = schedule.get("date")
        if date is not None and date != dataset_date:
            return ToolResult(
                "query_slots", params, False,
                error_code=ERR_UNKNOWN_DATE,
                error_message=f"合成数据集只覆盖 {dataset_date}，无法查询 {date} 的排期",
            )

        slots = schedule.get("slots", [])
        return ToolResult(
            "query_slots", params, True,
            data={
                "date": dataset_date,
                "timezone": schedule.get("timezone"),
                "available": [s["slot"] for s in slots if s.get("status") == "free"],
                "occupied": [
                    {"slot": s["slot"], "occupant": s.get("occupant"), "note": s.get("note")}
                    for s in slots if s.get("status") == "occupied"
                ],
                "restricted": [
                    {"slot": s["slot"], "note": s.get("note")}
                    for s in slots if s.get("status") == "restricted"
                ],
            },
        )

    # ---------- 工具 4：保存草稿（幂等写操作） ----------

    def save_draft(
        self,
        *,
        request_id: str,
        material_id: str,
        slot: str,
        note: str = "",
        confirmed: bool = False,
    ) -> ToolResult:
        params = {
            "request_id": request_id,
            "material_id": material_id,
            "slot": slot,
            "confirmed": confirmed,
        }
        if not request_id or not material_id or not slot:
            return ToolResult(
                "save_draft", params, False,
                error_code=ERR_INVALID_PARAMS,
                error_message="request_id / material_id / slot 均不得为空",
            )

        if self._fail_injection == "save_draft":
            # 故障注入点：必须在写之前返回，且不得留下任何半成品状态
            return ToolResult(
                "save_draft", params, False,
                error_code=ERR_TOOL_UNAVAILABLE,
                error_message="草稿存储写入失败（故障注入）：storage 返回 503，草稿未保存",
            )

        # 先校验时段，再写盘 —— 顺序不能反，否则会在冲突时段上留下脏草稿
        schedule = self._load_json("schedule.json")
        row = next((s for s in schedule.get("slots", []) if s.get("slot") == slot), None)
        if row is None:
            know = [s.get("slot") for s in schedule.get("slots", [])]
            return ToolResult(
                "save_draft", params, False,
                error_code=ERR_UNKNOWN_SLOT,
                error_message=f"时段 {slot} 不在排期表内，可选：{know}",
            )
        if row.get("status") == "occupied":
            return ToolResult(
                "save_draft", params, False,
                error_code=ERR_SLOT_OCCUPIED,
                error_message=f"时段 {slot} 已被 {row.get('occupant')} 占用，不能排入",
            )
        if row.get("status") == "restricted":
            return ToolResult(
                "save_draft", params, False,
                error_code=ERR_SLOT_RESTRICTED,
                error_message=f"时段 {slot} 为受保护时段（{row.get('note')}），需人工审批",
            )

        try:
            store = self._read_store()
        except OSError as exc:
            return ToolResult(
                "save_draft", params, False,
                error_code=ERR_STORE_IO,
                error_message=f"草稿存储读取失败：{exc}",
            )

        # 指纹只包含"语义身份"，刻意排除自由文本 note。
        #
        # 这是实测踩出来的：note 由模型生成，措辞每次都不一样。把它算进指纹后，
        # 同一 request_id 的重复提交会被判定成 IDEMPOTENCY_CONFLICT，
        # 直接违反题目"重复保存只产生一份草稿"的验收点。
        # 幂等键管的是"同一件事"，不是"同一串字符" —— 生成式自由属的文本
        # 天然不适合作为身份的一部分。
        body = {k: v for k, v in params.items() if k not in ("request_id", "note")}
        fingerprint = _fingerprint(body)

        existing = store.get(request_id)
        if existing is not None:
            if existing.get("fingerprint") != fingerprint:
                return ToolResult(
                    "save_draft", params, False,
                    error_code=ERR_IDEMPOTENCY_CONFLICT,
                    error_message=(
                        f"request_id {request_id} 已被用于另一份不同的草稿内容，"
                        "幂等键不允许复用。请更换 request_id 后重试"
                    ),
                )
            return ToolResult(
                "save_draft", params, True,
                data={
                    "draft_id": existing["draft_id"],
                    "duplicate": True,
                    "total_drafts": len(store),
                    "message": "相同 request_id 的重复提交，未产生新草稿",
                },
            )

        taken = next(
            (d for d in store.values() if d.get("slot") == slot and d.get("material_id") != material_id),
            None,
        )
        if taken is not None:
            return ToolResult(
                "save_draft", params, False,
                error_code=ERR_SLOT_TAKEN_BY_DRAFT,
                error_message=f"时段 {slot} 已被草稿 {taken.get('draft_id')} 占用",
            )

        draft_id = f"DR-{request_id}"
        store[request_id] = {
            "draft_id": draft_id,
            "request_id": request_id,
            "fingerprint": fingerprint,
            "slot": slot,
            "material_id": material_id,
            "confirmed": confirmed,
            "note": note,
            "status": "draft",  # 刻意只有 draft 一种状态：本项目不具备发布能力
        }

        try:
            self._write_store(store)
        except OSError as exc:
            return ToolResult(
                "save_draft", params, False,
                error_code=ERR_STORE_IO,
                error_message=f"草稿存储写入失败：{exc}，草稿未保存",
            )

        return ToolResult(
            "save_draft", params, True,
            data={
                "draft_id": draft_id,
                "duplicate": False,
                "total_drafts": len(store),
                "status": "draft",
                "message": "已保存为草稿，尚未发布，需用户确认后才可由人工执行发布",
            },
        )

    # ---------- 草稿存储 ----------

    def _read_store(self) -> dict[str, Any]:
        if not self._drafts_path.exists():
            return {}
        raw = self._drafts_path.read_text(encoding="utf-8").strip()
        return json.loads(raw) if raw else {}

    def _write_store(self, store: dict[str, Any]) -> None:
        self._drafts_path.parent.mkdir(parents=True, exist_ok=True)
        # 直接覆盖写：不用 os.replace 做原子替换，沙箱/受限环境下 rename 可能被拦截，
        # 反而把一次正常写入变成失败。本项目是单机演示，覆盖写的风险可接受。
        self._drafts_path.write_text(
            json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---------- 注册表 ----------

    def registry(self) -> dict[str, Callable[..., ToolResult]]:
        """对外暴露的工具注册表。

        这张表就是 Agent 能力的**完整上界**。网关层面不会再有任何旁路，
        所以"能不能发布"这个问题，看这张表就够了 —— 这是 D1 的落点。
        """
        return {
            "fetch_material": self.fetch_material,
            "check_rules": self.check_rules,
            "query_slots": self.query_slots,
            "save_draft": self.save_draft,
        }

    # ---------- 工具签名描述 ----------

    def describe(self) -> list[dict[str, Any]]:
        """把工具签名导出成可以写进提示词的结构。

        这不是可选的"锦上添花"。早期版本没把参数名告诉模型，结果 deepseek-flash
        连续三轮用 `platform` / `date` / `status` 这些不存在的参数调用 save_draft，
        八步预算全部消耗在猜参数上，任务以 MAX_STEPS_EXCEEDED 收场 ——
        **工具可用性必须像 API 文档一样显式暴露**，指望模型从错误信息里反推参数名
        是在拿预算赌博。

        返回值的键名与函数签名由 `tests/test_tools.py::test_tool_spec_matches_signature`
        强制保持一致：改了签名忘了改文档，测试会红。
        """
        specs: list[dict[str, Any]] = []
        for name, doc in TOOL_DOCS.items():
            specs.append({
                "name": name,
                "description": doc["description"],
                "parameters": doc["parameters"],
            })
        return specs


# 工具签名文档。键必须与 ScheduleTools 上真实方法的参数保持一致。
TOOL_DOCS: dict[str, dict[str, Any]] = {
    "fetch_material": {
        "description": "读取素材的元数据，并列出必填字段中缺失的部分",
        "parameters": {
            "material_id": {"type": "string", "required": True, "desc": "素材 ID，如 M01"},
        },
    },
    "check_rules": {
        "description": "按审核规则检查素材，返回命中的规则清单与综合严重程度（block/risk/warn/pass）",
        "parameters": {
            "material_id": {"type": "string", "required": True, "desc": "素材 ID，如 M01"},
        },
    },
    "query_slots": {
        "description": "查询排期表，返回空闲/已占用/受保护时段。调用时不要传多余参数",
        "parameters": {
            "date": {
                "type": "string",
                "required": False,
                "desc": "YYYY-MM-DD。合成数据集只有 2026-10-30，不确定时请省略",
            },
            "platform": {"type": "string", "required": False, "desc": "投放平台，可选"},
        },
    },
    "save_draft": {
        "description": "把排期保存为草稿。同一 request_id 重复调用只会返回同一份草稿；系统没有发布能力",
        "parameters": {
            "request_id": {"type": "string", "required": True, "desc": "本次请求的幂等键"},
            "material_id": {"type": "string", "required": True, "desc": "素材 ID"},
            "slot": {
                "type": "string",
                "required": True,
                "desc": "时段，必须取自 query_slots 返回的 available 列表，如 15:00",
            },
            "note": {"type": "string", "required": False, "desc": "备注，可选"},
            "confirmed": {
                "type": "boolean",
                "required": False,
                "desc": "用户是否已确认；未经确认不得填 true",
            },
        },
    },
}
