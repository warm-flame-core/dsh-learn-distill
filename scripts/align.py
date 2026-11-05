#!/usr/bin/env python3
"""三向对齐：字幕 ↔ 画面 ↔ 课件 → anchors.json

anchors.json 是下游写作的唯一真相源，把三种锚点合并到同一个结构：
- 时间戳（字幕段落 / 视频位置）
- 幻灯片（段内终态帧）
- 教材页码（可选）

用法：
    python scripts/align.py <index.json> --subtitle <srt> --ocr <ocr.json> -o <anchors.json>
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


def parse_srt(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    segs = []
    pat = re.compile(
        r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
        r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")
    for block in re.split(r"\r?\n\r?\n", raw.strip()):
        m = pat.search(block)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        frm = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000.0
        to = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000.0
        text = "\n".join(l for l in block.splitlines()
                         if not l.strip().isdigit() and "-->" not in l)
        segs.append({"from": round(frm, 3), "to": round(to, 3),
                     "text": text.strip()})
    return segs


def fmt_ts(sec: float) -> str:
    """秒 → mm:ss 或 hh:mm:ss"""
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def align_segments(segments: list[dict], srt: list[dict], ocr: dict,
                   course: str, lecture: str) -> dict:
    """把每个画面段与字幕、OCR 对齐。"""
    ocr_by_file = {}
    if ocr and ocr.get("frames"):
        for f in ocr["frames"]:
            ocr_by_file[f["file"]] = {
                "text": f.get("text", ""),
                "engine": f.get("engine"),
                "is_handwriting": f.get("metrics", {}).get("is_handwriting", False),
            }

    sections = []
    for seg in segments:
        t_start = float(seg["t_start"])
        t_end = float(seg["t_end"])
        chosen = seg.get("chosen") or {}
        fname = chosen.get("file", "")

        # 字幕：落在该段时间窗口内的所有句子
        subs = [s for s in srt if s["to"] > t_start and s["from"] < t_end]
        subtitle_text = " ".join(s["text"] for s in subs)

        # 画面文字：优先用 OCR 结果，没有则用 chosen 里的
        ocr_info = ocr_by_file.get(fname, {})
        frame_text = ocr_info.get("text") or chosen.get("ocr_text", "")

        section = {
            "id": f"s{seg['id']:02d}",
            "title": _guess_title(frame_text, subtitle_text),
            "t_start": fmt_ts(t_start),
            "t_end": fmt_ts(t_end),
            "t_start_sec": round(t_start, 3),
            "t_end_sec": round(t_end, 3),
            "slide": {
                "frame": fname,
                "t": chosen.get("t"),
                "score": chosen.get("score"),
                "jump": chosen.get("jump"),
            },
            "subtitle": {
                "text": subtitle_text,
                "count": len(subs),
                "from": round(subs[0]["from"], 3) if subs else None,
                "to": round(subs[-1]["to"], 3) if subs else None,
            },
            "frame_text": {
                "text": frame_text,
                "engine": ocr_info.get("engine", "unknown"),
                "is_handwriting": ocr_info.get("is_handwriting", False),
            },
            "boundary_evidence": seg.get("boundary_evidence"),
            "confidence": "direct_source" if (subs or frame_text) else "unsupported",
        }
        sections.append(section)

    return {
        "course": course,
        "lecture": lecture,
        "generated_from": "align.py",
        "stats": {
            "section_count": len(sections),
            "with_subtitle": sum(1 for s in sections if s["subtitle"]["count"] > 0),
            "with_frame_text": sum(1 for s in sections if s["frame_text"]["text"]),
            "handwriting_sections": sum(1 for s in sections if s["frame_text"]["is_handwriting"]),
        },
        "sections": sections,
    }


def _guess_title(frame_text: str, subtitle_text: str) -> str:
    """从画面文字或字幕推断小节标题。

    优先用画面文字的第一行（通常是标题），否则用字幕第一句。
    """
    if frame_text:
        first = frame_text.strip().split("\n")[0]
        # 去掉常见前缀
        first = re.sub(r"^\s*[【\[]?\s*第?\s*\d+\s*[章节讲]\s*", "", first).strip()
        if 2 <= len(first) <= 40:
            return first
    if subtitle_text:
        first = subtitle_text.strip().split("。")[0]
        if 2 <= len(first) <= 40:
            return first
    return ""


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="三向对齐 → anchors.json")
    ap.add_argument("index", type=Path, help="frames 产出的 index.json")
    ap.add_argument("--subtitle", type=Path, required=True, help="SRT")
    ap.add_argument("--ocr", type=Path, default=None, help="ocr.py 产出的 JSON")
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--course", default="", help="课程 id")
    ap.add_argument("--lecture", default="", help="讲次 id")
    args = ap.parse_args(argv)

    idx = json.loads(args.index.read_text(encoding="utf-8"))
    srt = parse_srt(args.subtitle)
    ocr = json.loads(args.ocr.read_text(encoding="utf-8")) if args.ocr and args.ocr.is_file() else {}

    course = args.course or idx.get("video", "").split("/")[-1].rsplit(".", 1)[0]
    lecture = args.lecture or "lecture-01"

    anchors = align_segments(idx.get("segments", []), srt, ocr, course, lecture)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(anchors, ensure_ascii=False, indent=2), encoding="utf-8")

    st = anchors["stats"]
    print(f"段数 {st['section_count']}")
    print(f"  有字幕 {st['with_subtitle']}")
    print(f"  有画面文字 {st['with_frame_text']}")
    print(f"  手写段 {st['handwriting_sections']}")
    print()
    for s in anchors["sections"][:10]:
        hw = " [手写]" if s["frame_text"]["is_handwriting"] else ""
        print(f"  {s['id']} {s['t_start']}-{s['t_end']}{hw} 「{s['title'][:30]}」")
        print(f"     字幕 {s['subtitle']['count']} 句 | 画面 {len(s['frame_text']['text'])} 字")
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
