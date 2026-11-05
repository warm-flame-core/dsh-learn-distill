#!/usr/bin/env python3
"""装配：把单讲次讲义合成课程级文档 + PLAYBOOK。

用法：
    python scripts/build_doc.py <lectures_dir> -o <dist_dir> [--course <id>]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

COLUMNS = ["概念", "公式", "推导", "例题", "易混与坑", "考点"]
COPYRIGHT_FOOTER = (
    "> 本资料由 `learn-distill` 从网课视频蒸馏生成，仅用于个人学习，"
    "**不得二次分发**。视频内容版权归原作者所有。"
)


def parse_lecture(path: Path) -> dict:
    """解析单讲次讲义，按六栏切分。"""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^#\s+(.+)$", text, re.M)
    title = m.group(1).strip() if m else ""

    sections: dict[str, list[str]] = {}
    current = None
    buf: list[str] = []
    for line in text.splitlines():
        h = re.match(r"^##\s+(\S.*)$", line)
        if h:
            if current is not None:
                sections[current] = buf
            current = h.group(1).strip()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = buf

    # 归一化栏名（容忍「## 概念」与「## 1. 概念」）
    norm: dict[str, list[str]] = {}
    for k, v in sections.items():
        for col in COLUMNS:
            if k.startswith(col) or k.endswith(col) or col in k:
                norm[col] = v
                break

    # 讲义没有 H1 时，从「概念」栏第一条的粗体标题推断
    if not title:
        first = next((ln for ln in norm.get("概念", []) if ln.strip()), "")
        mm = re.search(r"\*\*(.+?)\*\*", first)
        title = mm.group(1).strip() if mm else path.stem
        # 去掉「本讲主题是」这类前缀
        title = re.sub(r"^.*?本讲主题是「?([^」]+)」?.*$", r"\1", title).strip()

    return {"title": title or path.stem, "path": path, "columns": norm}


def count_entries(lines: list[str]) -> int:
    return sum(1 for ln in lines if re.match(r"^\s*[-*]\s+", ln))


def build_index(lectures: list[dict], course: str) -> str:
    out = [f"# {course} 课程索引", ""]
    out.append("## 讲次")
    out.append("")
    out.append("| 讲次 | 标题 | 概念 | 公式 | 推导 | 例题 | 易混 | 考点 |")
    out.append("|---|---|---|---|---|---|---|---|")
    for lc in lectures:
        c = lc["columns"]
        nums = [count_entries(c.get(col, [])) for col in COLUMNS]
        out.append(f"| {lc['path'].stem} | {lc['title']} | "
                   + " | ".join(str(n) for n in nums) + " |")
    out.append("")
    out.append(COPYRIGHT_FOOTER)
    return "\n".join(out) + "\n"


def build_playbook(lectures: list[dict], course: str) -> str:
    """PLAYBOOK：可复现的操作步骤。

    每个步骤必须能独立执行，且带锚点可回溯到原视频。
    只取「推导」「例题」两栏的编号步骤，跳过标题行与元数据行（锚点/来源/校对）。
    """
    SKIP_PREFIX = ("锚点", "来源", "校对", "讲师原话", "定义", "直觉", "适用", "符号", "单位")

    out = [f"# {course} · PLAYBOOK", ""]
    out.append("本手册给出可复现的操作步骤。每一步都标注了原视频时间点，便于回溯核对。")
    out.append("")

    for lc in lectures:
        out.append(f"## {lc['title']}")
        out.append("")
        steps: list[tuple[str, str, str]] = []   # (栏, 步骤文本, 锚点)

        for col in ("推导", "例题"):
            # 按「- **标题**：」分组；组内共享该组末尾的「锚点：」行
            groups: list[list[str]] = []
            cur: list[str] = []
            for ln in lc["columns"].get(col, []):
                s = ln.strip()
                if not s:
                    continue
                if s.startswith("- **"):
                    if cur:
                        groups.append(cur)
                    cur = [s]
                elif cur:
                    cur.append(s)
            if cur:
                groups.append(cur)

            for g in groups:
                anchor = ""
                for s in g:
                    body_wo_md = re.sub(r"^[-*]\s*", "", s).strip()
                    if body_wo_md.startswith("锚点"):
                        ts = re.findall(r"`(\d{1,2}:\d{2}(?::\d{2})?)`", body_wo_md)
                        if ts:
                            anchor = ts[0]
                for s in g:
                    body_wo_md = re.sub(r"^[-*]\s*", "", s).strip()
                    if any(body_wo_md.startswith(p) for p in SKIP_PREFIX):
                        continue
                    if not re.match(r"^(\d+\.\s|决策点)", body_wo_md):
                        continue
                    steps.append((col, body_wo_md, anchor))

        if not steps:
            out.append("本讲无可复现步骤（以概念理解为主）。")
            out.append("")
            continue
        out.append("| # | 步骤 | 来源栏 | 锚点 |")
        out.append("|---|---|---|---|")
        for i, (col, s, anchor) in enumerate(steps, 1):
            body = re.sub(r"`[^`]*`", "", s)
            body = re.sub(r"^\d+\.\s*", "", body).strip()
            body = body.replace("|", "\\|")[:120]
            out.append(f"| {i} | {body} | {col} | {f'`{anchor}`' if anchor else '—'} |")
        out.append("")

    out.append(COPYRIGHT_FOOTER)
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="装配讲义索引与 PLAYBOOK")
    ap.add_argument("lectures", type=Path, help="讲义目录（含 lecture-*.md）")
    ap.add_argument("-o", "--out", type=Path, required=True, help="输出目录")
    ap.add_argument("--course", default="", help="课程名")
    args = ap.parse_args(argv)

    files = sorted(args.lectures.glob("lecture-*.md"))
    if not files:
        print(f"没有 lecture-*.md：{args.lectures}", file=sys.stderr)
        return 1

    course = args.course or args.lectures.parent.name
    lectures = [parse_lecture(f) for f in files]
    print(f"课程：{course}，讲义 {len(lectures)} 份")

    args.out.mkdir(parents=True, exist_ok=True)

    index_md = build_index(lectures, course)
    (args.out / "_index.md").write_text(index_md, encoding="utf-8")

    playbook_md = build_playbook(lectures, course)
    (args.out / "playbook.md").write_text(playbook_md, encoding="utf-8")

    # 统计
    stats = {"course": course, "lecture_count": len(lectures), "lectures": []}
    for lc in lectures:
        c = lc["columns"]
        row = {
            "file": lc["path"].name,
            "title": lc["title"],
            "columns": {col: count_entries(c.get(col, [])) for col in COLUMNS},
            "missing_columns": [col for col in COLUMNS if col not in c],
            "total_entries": sum(count_entries(c.get(col, [])) for col in COLUMNS),
        }
        stats["lectures"].append(row)
        miss = row["missing_columns"]
        print(f"  {lc['path'].name}: {row['total_entries']} 条"
              + (f"  ⚠ 缺栏 {miss}" if miss else ""))

    (args.out / "_meta").mkdir(parents=True, exist_ok=True)
    (args.out / "_meta" / "build-stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n→ {args.out / '_index.md'}")
    print(f"→ {args.out / 'playbook.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
