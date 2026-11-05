#!/usr/bin/env python3
"""素材盘点：判型、过滤介绍页、输出 material-profile.json。

判型依据（T1 元信息 + T2 取样量测）：
- 幻灯片型：画面切点密集、有稳定停留段、OCR 有文字
- 信息流型：画面变化平缓、无稳定段、人像为主

用法：
    python scripts/probe.py <video> -o <out> [--subtitle <srt>] [--pages-json <json>]
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

# 介绍页判据
INTRO_TITLE_PATTERNS = [
    "简介", "介绍", "前言", "预告", "导学", "课程说明",
    "获取", "最后", "结尾", "致谢", "花絮", "广告",
]
INTRO_MIN_DURATION = 180.0   # 秒，短于此且标题命中即判介绍页


def find_ffmpeg(cfg_path: str = "") -> str:
    import shutil
    if cfg_path and Path(cfg_path).is_file():
        return cfg_path
    found = shutil.which("ffmpeg")
    if found:
        return found
    for c in [r"D:\Software\FFmpeg\bin\ffmpeg.exe"]:
        if Path(c).is_file():
            return c
    raise SystemExit("找不到 ffmpeg")


def probe_media(ffmpeg: str, media: Path) -> dict:
    """读媒体基本信息。"""
    out = subprocess.run([ffmpeg, "-i", str(media)],
                         capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    err = out.stderr or ""

    info = {"duration": 0.0, "width": 0, "height": 0, "fps": 0.0, "has_audio": False}
    m = re.search(r"Duration: (\d+):(\d+):(\d+)\.(\d+)", err)
    if m:
        h, mi, s, cs = m.groups()
        info["duration"] = int(h) * 3600 + int(mi) * 60 + int(s) + int(cs) / 100.0
    m = re.search(r"Stream #.*Video.*?(\d{2,5})x(\d{2,5})", err)
    if m:
        info["width"], info["height"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+(?:\.\d+)?) fps", err)
    if m:
        info["fps"] = float(m.group(1))
    info["has_audio"] = "Audio:" in err
    return info


def sample_frames(ffmpeg: str, media: Path, duration: float,
                  out_dir: Path, n: int = 9) -> list[Path]:
    """在片头/中段/片尾均匀取样帧，用于版式量测（墨迹、边缘密度）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("sample_*.jpg"):
        old.unlink()

    if duration <= 0:
        return []
    times = [duration * f for f in
             (0.02, 0.05, 0.10, 0.40, 0.50, 0.60, 0.85, 0.92, 0.97)]

    paths = []
    for i, t in enumerate(times[:n], 1):
        dest = out_dir / f"sample_{i:02d}.jpg"
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
               "-ss", "%.2f" % t, "-i", str(media),
               "-frames:v", "1", "-vf", "scale=-2:480", str(dest)]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        if proc.returncode == 0 and dest.exists():
            paths.append(dest)
    return paths


def cut_density(ffmpeg: str, media: Path, duration: float,
                out_dir: Path, window_sec: float = 40.0, fps: float = 2.0,
                windows: int = 3) -> dict:
    """在多段片段内连续抽帧，测切点密度。

    稀疏取样抓不到翻页突变；单个片段也可能恰好没有翻页
    （实测线代 19-60 秒是同一页公式）。因此取多段（片头/中段/片尾）合并统计。

    采样率用 2fps：1fps 会漏掉短暂翻页（翻页过程约 0.3-0.5 秒）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("seq_*.jpg"):
        old.unlink()

    from PIL import Image
    import numpy as np

    starts = []
    for k in range(windows):
        frac = 0.10 + (0.75 * k / max(1, windows - 1))
        starts.append(max(0.0, duration * frac))

    all_diffs = []
    total_frames = 0
    per_window = []
    for wi, start in enumerate(starts, 1):
        end = min(duration, start + window_sec)
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
               "-ss", "%.2f" % start, "-to", "%.2f" % end,
               "-i", str(media),
               "-vf", f"fps={fps},scale=-2:480",
               str(out_dir / f"seq_{wi:02d}_%04d.jpg")]
        subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")

        files = sorted(out_dir.glob(f"seq_{wi:02d}_*.jpg"))
        if len(files) < 4:
            continue
        total_frames += len(files)
        prev = None
        wdiffs = []
        for f in files:
            small = np.asarray(Image.open(f).convert("L").resize((160, 90)),
                               dtype=np.float32) / 255.0
            if prev is not None:
                d = float(np.abs(small - prev).mean())
                wdiffs.append(d)
                all_diffs.append(d)
            prev = small
        per_window.append({"window": wi, "start": round(start, 1),
                           "frames": len(files),
                           "max_diff": round(max(wdiffs), 4) if wdiffs else 0.0})

    if not all_diffs:
        return {"frames": total_frames, "cut_count": 0, "cut_ratio": 0.0,
                "mean_diff": 0.0, "max_diff": 0.0, "windows": windows,
                "per_window": per_window}

    # 切点阈值：取 95 分位与固定下限的较大者，自适应不同素材
    q95 = float(np.quantile(all_diffs, 0.95))
    thr = max(0.05, min(q95, 0.15))
    big = sum(1 for d in all_diffs if d >= thr)
    # 稳定段：Δ 持续低于阈值的片段（手写板书课没有翻页突变，但有稳定书写段）
    stable_thr = 0.035
    stable_min = 3   # 2fps 下 1.5 秒
    stable_runs = 0
    run = 0
    for d in all_diffs:
        if d <= stable_thr:
            run += 1
        else:
            if run >= stable_min:
                stable_runs += 1
            run = 0
    if run >= stable_min:
        stable_runs += 1

    return {
        "frames": total_frames,
        "windows": windows,
        "window_sec": window_sec,
        "cut_threshold": round(thr, 4),
        "cut_count": big,
        "cut_ratio": round(big / len(all_diffs), 4),
        "stable_runs": stable_runs,
        "stable_ratio": round(sum(1 for d in all_diffs if d <= stable_thr) / len(all_diffs), 4),
        "mean_diff": round(sum(all_diffs) / len(all_diffs), 4),
        "max_diff": round(max(all_diffs), 4),
        "per_window": per_window,
    }


def classify(stat_samples: list[Path], cut: dict, duration: float) -> dict:
    """判型：版式量测（稀疏取样）+ 切点密度（连续片段）。"""
    from PIL import Image
    import numpy as np

    inks, edges = [], []
    for p in stat_samples:
        small = np.asarray(Image.open(p).convert("L").resize((160, 90)),
                           dtype=np.float32) / 255.0
        inks.append(float((small < 0.62).mean()))
        gx = np.abs(np.diff(small, axis=1))
        gy = np.abs(np.diff(small, axis=0))
        edges.append(float(((gx > 0.15).mean() + (gy > 0.15).mean()) / 2))

    mean_ink = sum(inks) / len(inks) if inks else 0.0
    mean_edge = sum(edges) / len(edges) if edges else 0.0

    cut_ratio = cut.get("cut_ratio", 0.0)
    stable_ratio = cut.get("stable_ratio", 0.0)
    stable_runs = cut.get("stable_runs", 0)
    max_diff = cut.get("max_diff", 0.0)
    mean_diff = cut.get("mean_diff", 0.0)

    # 判型规则
    if max_diff < 0.05 and mean_ink < 0.05:
        kind = "info-stream"       # 画面几乎不变且墨迹少 → 人像/信息流
    elif stable_ratio >= 0.5 and mean_edge > 0.02:
        # 有大量稳定段（书写/停留）且文字密集 → 幻灯片或板书
        # 手写板书课没有翻页突变，但仍有稳定书写段，不能用切点密度判定
        kind = "slides-lecture"
    elif cut_ratio >= 0.005 and mean_edge > 0.02:
        kind = "slides-lecture"
    elif mean_edge > 0.02:
        kind = "document"
    else:
        kind = "unknown"

    return {
        "kind": kind,
        "cut_ratio": cut_ratio,
        "cut_count": cut.get("cut_count", 0),
        "stable_runs": stable_runs,
        "stable_ratio": stable_ratio,
        "mean_diff": round(mean_diff, 4),
        "max_diff": round(max_diff, 4),
        "mean_ink": round(mean_ink, 4),
        "mean_edge": round(mean_edge, 4),
        "sample_count": len(stat_samples),
        "cut_window_sec": cut.get("window_sec"),
    }


def filter_pages(pages: list[dict]) -> tuple[list[dict], list[dict]]:
    """过滤介绍页/广告页。返回 (保留, 排除)。"""
    keep, drop = [], []
    for p in pages:
        title = p.get("title") or p.get("part") or ""
        dur = float(p.get("duration") or 0)
        reason = None
        if dur < INTRO_MIN_DURATION:
            reason = f"时长 {dur:.0f}s < {INTRO_MIN_DURATION:.0f}s"
        if any(k in title for k in INTRO_TITLE_PATTERNS):
            reason = (reason + "；" if reason else "") + f"标题含介绍类词"
        if reason:
            drop.append({**p, "reason": reason})
        else:
            keep.append(p)
    return keep, drop


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="素材盘点与判型")
    ap.add_argument("video", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True, help="输出目录")
    ap.add_argument("--subtitle", type=Path, default=None)
    ap.add_argument("--pages-json", type=Path, default=None,
                    help="分P列表（含 title/duration），用于过滤介绍页")
    ap.add_argument("--ffmpeg", default="")
    args = ap.parse_args(argv)

    ffmpeg = find_ffmpeg(args.ffmpeg)
    if not args.video.is_file():
        raise SystemExit(f"视频不存在：{args.video}")

    info = probe_media(ffmpeg, args.video)
    print(f"视频：{args.video.name}")
    print(f"  时长 {info['duration']:.1f}s | {info['width']}x{info['height']} "
          f"| {info['fps']} fps | 音轨 {'有' if info['has_audio'] else '无'}")

    sample_dir = args.out / "probe_samples"
    frames = sample_frames(ffmpeg, args.video, info["duration"], sample_dir)
    print(f"  版式取样 {len(frames)} 帧")

    cut = cut_density(ffmpeg, args.video, info["duration"], args.out / "probe_seq")
    print(f"  切点量测：{cut.get('frames',0)} 帧 / {cut.get('window_sec',0)}s，"
          f"切点 {cut.get('cut_count',0)} 个（密度 {cut.get('cut_ratio',0):.2%}），"
          f"稳定段 {cut.get('stable_runs',0)} 个（占比 {cut.get('stable_ratio',0):.1%}）")

    cls = classify(frames, cut, info["duration"])
    print(f"  判型：{cls['kind']}（edge={cls['mean_edge']}, ink={cls['mean_ink']}, "
          f"cut={cls['cut_ratio']:.2%}, stable={cls['stable_ratio']:.1%}）")

    subtitle = None
    if args.subtitle and args.subtitle.is_file():
        raw = args.subtitle.read_text(encoding="utf-8", errors="replace")
        n = len(re.findall(r"\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->", raw))
        subtitle = {"path": str(args.subtitle), "segments": n}

    pages_keep, pages_drop = [], []
    if args.pages_json and args.pages_json.is_file():
        pages = json.loads(args.pages_json.read_text(encoding="utf-8"))
        pages_keep, pages_drop = filter_pages(pages)
        print(f"  分P：保留 {len(pages_keep)}，排除 {len(pages_drop)}")
        for d in pages_drop:
            print(f"    - p{d.get('page')} 「{d.get('title','')}」→ {d['reason']}")

    # 缺口分析
    gaps = []
    if cls["kind"] == "info-stream":
        gaps.append("画面无信息量，无课件时只能依赖口述")
    if subtitle is None or subtitle["segments"] == 0:
        gaps.append("无字幕，需本地 ASR")
    if cls["kind"] == "document":
        gaps.append("滚动文档，无稳定页边界")

    profile = {
        "video": str(args.video),
        "media": info,
        "classify": cls,
        "subtitle": subtitle,
        "pages": {"keep": pages_keep, "drop": pages_drop},
        "gaps": gaps,
        "recommended_pipeline": _pipeline_for(cls["kind"], subtitle),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    out_file = args.out / "material-profile.json"
    out_file.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")

    if gaps:
        print("\n缺口：")
        for g in gaps:
            print(f"  - {g}")
    print(f"\n推荐管线：{' → '.join(profile['recommended_pipeline'])}")
    print(f"→ {out_file}")
    return 0


def _pipeline_for(kind: str, subtitle) -> list[str]:
    steps = ["probe"]
    if subtitle and subtitle.get("segments"):
        steps.append("subtitle")
    else:
        steps.append("asr")
    if kind in ("slides-lecture", "document"):
        steps += ["frames", "ocr", "align"]
    elif kind == "info-stream":
        steps += ["transcript"]
    steps += ["structure", "assemble", "validate"]
    return steps


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
