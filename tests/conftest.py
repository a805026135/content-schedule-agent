"""共享 fixtures。

关于「临时目录」的说明：
这里**刻意不用 pytest 内置的 tmp_path**。原因有两条，都跟可复现性有关：
1. pytest 会周期性清理旧的 basetemp（默认保留最近 3 个），在受限环境里 rmtree
   可能被拦截，导致所有用例以同样的 OSError 集体报错——这与被测逻辑无关。
2. 落在系统 temp 下的路径不可控，出错时评审难以定位。

改成项目内的 `runs/_pytest_tmp/<用例名>/`，失败时的中间产物可以直接打开看。
`runs/` 已在 .gitignore 中，不会污染仓库。
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from src.config import DATA_DIR, PROJECT_ROOT, Settings
from src.tools.schedule_tools import ScheduleTools

DATA_FILES = ("materials.json", "rules.json", "schedule.json")

_PYTEST_TMP = PROJECT_ROOT / "runs" / "_pytest_tmp"


def _safe(name: str) -> str:
    return re.sub(r"[^\w.\-]", "_", name)


@pytest.fixture
def isolated_dir(request):
    """每个用例一个独立目录，位于项目内。

    刻意**不复用固定路径、也不清理旧目录**：
    - 复用固定路径会让上一次运行的残留草稿影响"草稿是否真的只有一份"这类断言；
    - 清理则要调用 rmtree，在受限环境里会被批量删除保护拦下。
    折中方案是每次运行都新建一个带随机后缀的子目录，代价是 runs/ 会逐渐变多 ——
    它在 .gitignore 里，不影响仓库。
    """
    path = _PYTEST_TMP / _safe(request.node.name) / uuid4().hex[:8]
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def tools_factory(isolated_dir):
    """返回一个可定制 (data_dir, drafts_path, fail_injection) 的工具运行时工厂。"""

    def _make(data_dir=None, drafts_path=None, fail_injection="none"):
        return ScheduleTools(
            Settings(),
            data_dir=Path(data_dir) if data_dir else DATA_DIR,
            drafts_path=Path(drafts_path) if drafts_path else isolated_dir / "drafts.json",
            fail_injection=fail_injection,
        )

    return _make


@pytest.fixture
def tools(tools_factory):
    """默认工具运行时：真实 data/ + 隔离的草稿存储。"""
    return tools_factory()


@pytest.fixture
def blocked_material_data(isolated_dir):
    """构造一个会被 severity=block 拦下的素材的数据目录。

    M05 刻意不进 data/（不属于六个必验点），但规则引擎的 block 档必须有实证，
    所以在这里用内联 fixture 补上。
    """
    data_dir = isolated_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for name in DATA_FILES:
        shutil.copy(DATA_DIR / name, data_dir / name)

    payload = json.loads((data_dir / "materials.json").read_text(encoding="utf-8"))
    payload["materials"].append({
        "material_id": "M05",
        "title": "二创混剪（背景音乐来源不明）",
        "platform": "douyin",
        "content_type": "short_video",
        "duration_sec": 25,
        "requested_slot": "21:00",
        "owner": "content-team",
        "tags": ["二创"],
        "copyright": {"music": "unlicensed", "portrait": "authorized"},
        "claims": [],
        "review_doc_ids": ["RV-2026-1015"],
    })
    (data_dir / "materials.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data_dir
