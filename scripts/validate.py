#!/usr/bin/env python3
"""校验：锚点完整性、有效性、引用可解析、格式契约。

确定性校验（零 LLM），不做教学循环。

用法：
    python scripts/validate.py <anchors.json> --media <video> -o <validation.json>
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

COLUMNS = ["概念", "公式", "推导", "例题", "易混与坑", "考点"]


class Issue:
    def __init__(self, code: str, owner: str, message: str, level: str = "error"):
        self.code = code
        self.owner = owner
        self.message = message
        self.level = level

    def to_dict(self) -> dict:
        return {"code": self.code, "owner": self.owner,
                "message": self.message, "level": self.level}


def ts_to_sec(ts: str) -> float:
    """mm:ss 或 hh:mm:ss → 秒"""
    parts = [int(x) for x in ts.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"时间格式非法：{ts}")


from media_util import find_ffmpeg, media_duration  # noqa: E402


def check_anchors(anchors: dict, duration: float) -> list[Issue]:
    """C1-C3：锚点完整性、有效性、可解析。"""
    issues: list[Issue] = []
    sections = anchors.get("sections") or []

    if not sections:
        issues.append(Issue("C0", "probe", "anchors.json 没有任何 section"))
        return issues

    prev_end = -1.0
    for s in sections:
        sid = s.get("id", "?")
        # C1 完整性
        if not s.get("t_start") or not s.get("t_end"):
            issues.append(Issue("C1", "frames", f"{sid} 缺时间戳"))
            continue
        try:
            t0 = ts_to_sec(s["t_start"])
            t1 = ts_to_sec(s["t_end"])
        except ValueError as exc:
            issues.append(Issue("C1", "frames", f"{sid} 时间格式非法：{exc}"))
            continue

        # C2 有效性
        if t1 <= t0:
            issues.append(Issue("C2", "frames", f"{sid} 时间区间倒置 {t0}→{t1}"))
        if t0 < prev_end - 0.5:
            issues.append(Issue("C2", "frames",
                                f"{sid} 与上一段重叠（{t0} < {prev_end}）"))
        if duration > 0 and t1 > duration + 1:
            issues.append(Issue("C2", "frames",
                                f"{sid} 越界（{t1} > 视频时长 {duration:.0f}s）"))
        prev_end = max(prev_end, t1)

        # C3 引用可解析
        slide = s.get("slide") or {}
        if not slide.get("frame"):
            issues.append(Issue("C3", "frames", f"{sid} 缺幻灯片帧"))
        if not s.get("subtitle", {}).get("text") and not s.get("frame_text", {}).get("text"):
            issues.append(Issue("C4", "ocr",
                                f"{sid} 既无字幕也无画面文字", "warn"))

        # 来源标记
        conf = s.get("confidence")
        if conf not in ("direct_source", "source_grounded_synthesis",
                        "mentor_inference", "external_general_knowledge", "unsupported"):
            issues.append(Issue("C5", "align", f"{sid} 来源标记非法：{conf}", "warn"))

    return issues


def check_lecture(md_path: Path, anchors: dict) -> list[Issue]:
    """C6：六栏格式契约 + 锚点可验证。"""
    issues: list[Issue] = []
    text = md_path.read_text(encoding="utf-8")
    anchors_text = md_path.with_suffix(".anchors.json")
    a = anchors
    if anchors_text.is_file():
        a = json.loads(anchors_text.read_text(encoding="utf-8"))
    valid_ts = {s["t_start"] for s in (a.get("sections") or [])}
    valid_ts |= {s["t_end"] for s in (a.get("sections") or [])}

    # 六栏必须齐全且顺序固定
    found = re.findall(r"^##\s+(\S.*)$", text, re.M)
    for col in COLUMNS:
        if not any(f.startswith(col) for f in found):
            issues.append(Issue("C6", "writer", f"缺栏：{col}"))
    # 顺序
    idx = []
    for f in found:
        for i, col in enumerate(COLUMNS):
            if f.startswith(col):
                idx.append(i)
                break
    if idx != sorted(idx):
        issues.append(Issue("C6", "writer", "六栏顺序不对"))

    # 时间戳必须存在且可验证
    ts_in_doc = re.findall(r"`(\d{1,2}:\d{2}(?::\d{2})?)`", text)
    if not ts_in_doc:
        issues.append(Issue("C6", "writer", "文档里没有任何时间戳锚点"))
    for ts in set(ts_in_doc):
        if ts not in valid_ts:
            issues.append(Issue("C6", "writer", f"锚点 {ts} 不在 anchors.json 里", "warn"))

    # 全角括号
    if re.search(r"〔|〕", text):
        issues.append(Issue("C6", "writer", "含全角括号〔〕"))

    # 防摘要化：正文长度不得少于字幕总量的一定比例
    sub_chars = sum(len(s.get("subtitle", {}).get("text", ""))
                    for s in (a.get("sections") or []))
    body_chars = len(re.sub(r"[#*`\s]", "", text))
    ratio = body_chars / sub_chars if sub_chars else 0
    if sub_chars > 0 and ratio < 0.85:
        issues.append(Issue("C7", "writer",
                            f"正文过短：{body_chars} 字 / 字幕 {sub_chars} 字 = {ratio:.0%} < 85%"))
    return issues


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="确定性校验")
    ap.add_argument("anchors", type=Path)
    ap.add_argument("--media", type=Path, default=None)
    ap.add_argument("--lecture", type=Path, default=None, help="讲义 md，校验六栏")
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--ffmpeg", default="")
    args = ap.parse_args(argv)

    anchors = json.loads(args.anchors.read_text(encoding="utf-8"))
    duration = 0.0
    if args.media and args.media.is_file():
        duration = media_duration(find_ffmpeg(args.ffmpeg), args.media)

    issues = check_anchors(anchors, duration)
    if args.lecture and args.lecture.is_file():
        issues += check_lecture(args.lecture, anchors)

    errors = [i for i in issues if i.level == "error"]
    warns = [i for i in issues if i.level == "warn"]

    payload = {
        "anchors": str(args.anchors),
        "lecture": str(args.lecture) if args.lecture else None,
        "media_duration": round(duration, 1),
        "summary": {"errors": len(errors), "warnings": len(warns),
                    "passed": len(errors) == 0},
        "issues": [i.to_dict() for i in issues],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"校验：{len(errors)} 错 / {len(warns)} 警")
    for i in issues:
        mark = "✗" if i.level == "error" else "!"
        print(f"  {mark} [{i.code}] {i.owner}: {i.message}")
    print(f"\n结果：{'通过' if not errors else '不通过'}")
    print(f"→ {args.out}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
