"""生成 README 里用的运行截图。

这些图不是手画的示意图：图里的每一行业都来自**真实执行** CLI / pytest 的 stdout / stderr，
连退出码都是 `subprocess` 返回的真实值。脚本本身入库，任何人跑一遍都能得到同样的图：

    python scripts/render_cli_shots.py

之所以做成"脚本生成"而不是"截图工具截屏"，是因为截屏无法被复现和核对；
而这里每张图都可以用 `python scripts/render_cli_shots.py` 重新生成并逐行比对。

图片里若出现 `...（中间省略 N 行）`，是渲染时为控制高度做的截断，
被省略的是 trace 数组里的中间步骤，完整输出照常打印在真实终端里。

依赖：Pillow（仅用于生成本文档素材，不在 requirements.txt 里，不参与 Agent 运行）。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "docs" / "assets"
RUNS = ROOT / "runs"

BG = "#0C0C0C"
BAR_BG = "#1F1F1F"
FG = "#D4D4D4"
PROMPT = "#13A10E"
CMD_FG = "#FFFFFF"
ERR_FG = "#F14C4C"
OMIT_FG = "#6E7681"
C_KEY = "#9CDCFE"
C_STR = "#CE9178"
C_NUM = "#B5CEA8"
C_BOOL = "#569CD6"

SCALE = 2
FONT_SIZE = 13
LINE_H = 21
PAD_X = 18
PAD_TOP = 16
BAR_H = 34
WIDTH = 1180

_TOKEN_RE = re.compile(
    r'"(?:[^"\\]|\\.)*"\s*:|"(?:[^"\\]|\\.)*"|\b-?\d+(?:\.\d+)?\b|\btrue\b|\bfalse\b|\bnull\b'
)


def _is_cjk(ch: str) -> bool:
    return ord(ch) > 0x2E80


def _find_font(names: list[str]) -> str | None:
    fonts_dir = Path("C:/Windows/Fonts")
    for name in names:
        p = fonts_dir / name
        if p.exists():
            return str(p)
    for name in names:
        try:
            from PIL import ImageFont

            ImageFont.truetype(name, FONT_SIZE * SCALE)
            return name
        except Exception:
            continue
    return None


MONO_FONT = _find_font(
    ["consola.ttf", "CascadiaMono.ttf", "DejaVuSansMono.ttf", "cour.ttf"]
)
CJK_FONT = _find_font(["msyh.ttc", "simhei.ttf", "NotoSansCJK-Regular.ttc"])

if MONO_FONT is None:
    sys.exit("找不到等宽字体，无法生成截图")


def _font(size: int, cjk: bool):
    from PIL import ImageFont

    return ImageFont.truetype(CJK_FONT if (cjk and CJK_FONT) else MONO_FONT, size)


def text_width(text: str, size: int) -> int:
    """按字符类型分别测量宽度（CJK 与 ASCII 字体不同）。"""
    total = 0.0
    for ch in text:
        total += _font(size, _is_cjk(ch)).getlength(ch)
    return int(total)


def _colorize(line: str) -> list[tuple[str, str]]:
    """把一行输出切成 (文本, 颜色) 片段，做轻量 JSON 着色。"""
    out: list[tuple[str, str]] = []
    pos = 0
    for m in _TOKEN_RE.finditer(line):
        if m.start() > pos:
            out.append((line[pos : m.start()], FG))
        tok = m.group(0)
        if tok.endswith(":"):
            out.append((tok, C_KEY))
        elif tok.startswith('"'):
            out.append((tok, C_STR))
        elif tok in ("true", "false", "null"):
            out.append((tok, C_BOOL))
        else:
            out.append((tok, C_NUM))
        pos = m.end()
    if pos < len(line):
        out.append((line[pos:], FG))
    return out or [(line, FG)]


def _wrap(runs: list[tuple[str, str]], size: int, max_px: int) -> list[list[tuple[str, str]]]:
    """按像素宽度折行。CJK 逐字断行，ASCII 尽量不在单词中间断。"""
    lines: list[list[tuple[str, str]]] = []
    cur: list[tuple[str, str]] = []
    cur_w = 0
    for text, color in runs:
        buf = ""
        for ch in text:
            w = _font(size, _is_cjk(ch)).getlength(ch)
            if cur_w + w > max_px and cur:
                if buf:
                    cur.append((buf, color))
                lines.append(cur)
                cur, cur_w = [], 0
                buf = ""
            if ch == " " and not cur and not buf:
                continue
            buf += ch
            cur_w += w
        if buf:
            cur.append((buf, color))
    if cur:
        lines.append(cur)
    return lines or [[("", FG)]]


def run(argv: list[str], env_extra: dict[str, str] | None = None,
        clean: list[str] | None = None) -> tuple[int, str, str]:
    import os

    for f in clean or []:
        (ROOT / f).unlink(missing_ok=True)  # 保证截图演示的是"干净首跑"，不残留上次草稿

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(
        argv, cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8"
    )
    return p.returncode, p.stdout or "", p.stderr or ""


def _clip(text: str, head: int, tail: int) -> list[str]:
    """真实输出太长时保留头尾，中间显式标注省略。"""
    lines = [ln.rstrip() for ln in text.splitlines()]
    if tail == 0:
        return lines[:head]
    if len(lines) <= head + tail + 1:
        return lines
    return (
        lines[:head]
        + [f"...（中间省略 {len(lines) - head - tail} 行，完整输出照常打印在终端里）"]
        + lines[len(lines) - tail :]
    )


def render(file: str, title: str, steps: list[dict]) -> None:
    from PIL import Image, ImageDraw

    rows: list[tuple[str, list[tuple[str, str]]]] = []  # (kind, runs)

    for st in steps:
        rc, out, err = run(st["argv"], st.get("env"), st.get("clean"))
        rows.append(("cmd", [("$ ", PROMPT), (st["show"], CMD_FG)]))
        for line in _clip(out, st.get("head", 8), st.get("tail", 8)):
            rows.append(("out", _colorize(line)))
        for line in _clip(err, 4, 0):
            rows.append(("err", [(line, ERR_FG)]))
        if st.get("show_rc"):
            rows.append(("cmd", [("$ ", PROMPT), ("echo %ERRORLEVEL%", CMD_FG)]))
            color = C_NUM if rc == 0 else "#DCDCAA"
            rows.append(("out", [(str(rc), color)]))
        st["_rc"] = rc

    line_imgs = []
    for kind, runs in rows:
        line_imgs.append(_wrap(runs, FONT_SIZE * SCALE, (WIDTH - PAD_X * 2) * SCALE))

    n_lines = sum(len(x) for x in line_imgs)
    height = BAR_H + PAD_TOP * 2 + n_lines * LINE_H

    img = Image.new("RGB", (WIDTH * SCALE, height * SCALE), BG)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(
        [0, 0, WIDTH * SCALE - 1, height * SCALE - 1], radius=10 * SCALE, fill=BG
    )
    d.rounded_rectangle(
        [0, 0, WIDTH * SCALE - 1, BAR_H * SCALE], radius=10 * SCALE, fill=BAR_BG
    )
    d.rectangle([0, BAR_H * SCALE - 12 * SCALE, WIDTH * SCALE, BAR_H * SCALE], fill=BAR_BG)
    for i, c in enumerate(["#E81123", "#F5A623", "#13A10E"]):
        cx = (PAD_X + i * 16 + 6) * SCALE
        cy = BAR_H * SCALE // 2
        r = 5 * SCALE
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c)
    tf = _font(12 * SCALE, True)
    tw = d.textlength(title, font=tf)
    d.text(((WIDTH * SCALE - tw) / 2, BAR_H * SCALE / 2), title, font=tf, fill="#9A9A9A", anchor="mm")

    y = (BAR_H + PAD_TOP) * SCALE
    for wrapped in line_imgs:
        for runs in wrapped:
            x = PAD_X * SCALE
            for text, color in runs:
                for ch in text:
                    f = _font(FONT_SIZE * SCALE, _is_cjk(ch))
                    d.text((x, y), ch, font=f, fill=color, anchor="la")
                    x += f.getlength(ch)
            y += LINE_H * SCALE

    ASSETS.mkdir(parents=True, exist_ok=True)
    out_path = ASSETS / file
    img.resize((WIDTH, height), Image.LANCZOS).save(out_path, "PNG")
    print(f"[已生成] {out_path}  ({n_lines} 行真实输出)")


PY = "python"
CLI = f"{PY} -m src.cli"


def main() -> None:
    RUNS.mkdir(exist_ok=True)

    render(
        "run-01-first.png",
        "第一次运行 · 时段冲突后改期",
        [
            {
                "show": f"{CLI} --material M01 --request-id REQ-TRY-01 "
                "--mock-script examples/mock/case04_slot_conflict/mock_script.json "
                "--drafts-path runs/shot01.json",
                "argv": [sys.executable, "-m", "src.cli", "--material", "M01",
                         "--request-id", "REQ-TRY-01",
                         "--mock-script", "examples/mock/case04_slot_conflict/mock_script.json",
                         "--drafts-path", "runs/shot01.json"],
                "clean": ["runs/shot01.json"],
                "head": 9,
                "tail": 22,
                "show_rc": True,
            }
        ],
    )

    render(
        "run-02-exit-codes.png",
        "退出码 0 / 2 / 4",
        [
            {
                "show": f"{CLI} --material M04 --request-id REQ-EC-0 "
                "--mock-script examples/mock/case01_normal/mock_script.json "
                "--out runs/shot_ec0_out.json --drafts-path runs/shot_ec0.json",
                "argv": [sys.executable, "-m", "src.cli", "--material", "M04",
                         "--request-id", "REQ-EC-0",
                         "--mock-script", "examples/mock/case01_normal/mock_script.json",
                         "--out", "runs/shot_ec0_out.json",
                         "--drafts-path", "runs/shot_ec0.json"],
                "clean": ["runs/shot_ec0.json"],
                "head": 2,
                "tail": 6,
                "show_rc": True,
            },
            {
                "show": f"{CLI} --material M01 --request-id REQ-EC-2 --max-steps 2 "
                "--mock-script examples/mock/case07_step_budget/mock_script.json "
                "--out runs/shot_ec2_out.json --drafts-path runs/shot_ec2.json",
                "argv": [sys.executable, "-m", "src.cli", "--material", "M01",
                         "--request-id", "REQ-EC-2", "--max-steps", "2",
                         "--mock-script", "examples/mock/case07_step_budget/mock_script.json",
                         "--out", "runs/shot_ec2_out.json",
                         "--drafts-path", "runs/shot_ec2.json"],
                "clean": ["runs/shot_ec2.json"],
                "head": 2,
                "tail": 7,
                "show_rc": True,
            },
            {
                "show": f"set DEEPSEEK_API_KEY=sk-invalid-for-demo && {CLI} "
                "--material M04 --request-id REQ-EC-4 --provider deepseek",
                "argv": [sys.executable, "-m", "src.cli", "--material", "M04",
                         "--request-id", "REQ-EC-4", "--provider", "deepseek",
                         "--drafts-path", "runs/shot_ec4.json"],
                "clean": ["runs/shot_ec4.json"],
                "env": {"DEEPSEEK_API_KEY": "sk-invalid-for-demo"},
                "head": 0,
                "tail": 0,
                "show_rc": True,
            },
        ],
    )

    _probe = (
        "import json;d=json.load(open('%s',encoding='utf-8'));"
        "print('draft_id =',d['draft_id'],'| duplicate =',d['duplicate'],"
        "'| total_drafts =',d['total_drafts'])"
    )
    dup_argv = [sys.executable, "-m", "src.cli", "--material", "M01",
                "--request-id", "REQ-SHOT-DUP",
                "--mock-script", "examples/mock/case05_duplicate/mock_script.json",
                "--drafts-path", "runs/shot_dup.json"]

    render(
        "run-03-tests-and-idempotency.png",
        "测试 与 重复提交",
        [
            {
                "show": f"{PY} -m pytest",
                "argv": [sys.executable, "-m", "pytest"],
                "head": 0,
                "tail": 3,
                "show_rc": True,
            },
            {
                "show": f"{CLI} --material M01 --request-id REQ-SHOT-DUP "
                "--mock-script examples/mock/case05_duplicate/mock_script.json "
                "--out runs/shot_dup1.json --drafts-path runs/shot_dup.json",
                "argv": dup_argv + ["--out", "runs/shot_dup1.json"],
                "clean": ["runs/shot_dup.json"],
                "head": 0,
                "tail": 0,
            },
            {
                "show": f"{PY} -c \"{_probe % 'runs/shot_dup1.json'}\"",
                "argv": [sys.executable, "-c", _probe % "runs/shot_dup1.json"],
                "head": 0,
                "tail": 1,
            },
            {
                "show": f"{CLI} --material M01 --request-id REQ-SHOT-DUP "
                "--mock-script examples/mock/case05_duplicate/mock_script.json "
                "--out runs/shot_dup2.json --drafts-path runs/shot_dup.json",
                "argv": dup_argv + ["--out", "runs/shot_dup2.json"],
                "head": 0,
                "tail": 0,
            },
            {
                "show": f"{PY} -c \"{_probe % 'runs/shot_dup2.json'}\"",
                "argv": [sys.executable, "-c", _probe % "runs/shot_dup2.json"],
                "head": 0,
                "tail": 1,
            },
        ],
    )


if __name__ == "__main__":
    main()
