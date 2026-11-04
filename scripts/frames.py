#!/usr/bin/env python3
"""抽帧与稳定段切分。

算法来源：移植自 skill-bnote（MIT，Copyright (c) 2026 HarveyZed）的
bnote/segmenters/stable.py 与 bnote/segmenters/framesig.py，按本 skill 的
配置与依赖改写。详见 LICENSES/NOTICE.md。

核心判据：
1. 帧差 Δ(t) = alpha × dHash汉明率 + (1-alpha) × 像素MAE
2. 稳定段 = Δ 连续低于阈值且持续 ≥ stable_min_sec
3. 稳定段必须紧跟硬切才算新页（帧差 ≥ p85 分位 且 ≥ 均值×倍数）
   否则只是同页内的标注停顿
4. 段边界吸附最近的字幕句首（±window 秒）
5. 薄段吸收（帧数不足 min_seg_sec 的段并入前段）
6. 终态帧打分 = w_chars×OCR字数 + w_ink×墨迹 + w_sharp×清晰度 + 0.001×位置比
7. 同页合并：dHash 距离 ≤ max_hash，或 OCR 4-gram 包含度 ≥ gram_th

用法：
    python scripts/frames.py <video> -o <out> [--fps 2] [--height 720]
    python scripts/frames.py <video> -o <out> --sections 00:00:00-00:05:00
    python scripts/frames.py <video> -o <out> --subtitle <srt>
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

DEFAULTS = {
    "fps": 2.0,
    "height": 720,
    "diff_threshold": 0.035,
    "diff_alpha": 0.5,
    "stable_min_sec": 1.5,
    "min_seg_sec": 1.0,
    "page_cut_percentile": 0.85,
    "page_cut_multiple": 3.0,
    "page_cut_lookback_sec": 1.5,
    "page_cut_only_cuts": True,
    "snap_window": 3.0,
    "merge_hash_dist": 6,
    "merge_text_min_chars": 30,
    "merge_gram_contain": 0.60,
    "boilerplate_patterns": [],
    "native_ocr": False,
    "merge_max_span_sec": 120.0,
    "absorb_thin_chars": 20,
    "absorb_thin_max_sec": 60.0,
    "weight_chars": 1.0,
    "weight_ink": 30.0,
    "weight_sharp": 5.0,
    "weight_jump": 20.0,
    "weight_ink_total": 0.0,
    "jump_lookahead_sec": 3.0,
    "jump_full_score": 0.10,
    "candidates_per_seg": 6,
}

SMALL_W, SMALL_H = 48, 27


def find_ffmpeg(cfg_path: str = "") -> str:
    import shutil
    if cfg_path and Path(cfg_path).is_file():
        return cfg_path
    found = shutil.which("ffmpeg")
    if found:
        return found
    for c in [r"D:\Software\FFmpeg\bin\ffmpeg.exe", r"C:\ffmpeg\bin\ffmpeg.exe"]:
        if Path(c).is_file():
            return c
    raise SystemExit("找不到 ffmpeg：请安装或在配置中指定 [tools] ffmpeg 绝对路径")


def probe_duration(ffmpeg: str, video: Path) -> float:
    out = subprocess.run(
        [ffmpeg, "-i", str(video)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"Duration: (\d+):(\d+):(\d+)\.(\d+)", out.stderr or "")
    if not m:
        raise SystemExit(f"无法读取视频时长：{video}")
    h, mi, s, cs = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(s) + int(cs) / 100.0


def parse_hms(s: str) -> float:
    m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", s.strip())
    if not m:
        raise SystemExit(f"时间格式应为 HH:MM:SS 或 MM:SS，实际 {s!r}")
    a, b, c = m.groups()
    if c is None:
        return float(int(a) * 60 + int(b))
    return float(int(a) * 3600 + int(b) * 60 + int(c))


def extract_frames(ffmpeg, video, out_dir, fps, height, start=0.0, end=None) -> list[dict]:
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for old in frames_dir.glob("*.jpg"):
        old.unlink()

    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    if start > 0:
        cmd += ["-ss", str(start)]
    if end is not None:
        cmd += ["-to", str(end)]
    cmd += ["-i", str(video),
            "-vf", f"fps={fps},scale=-2:{height}",
            "-q:v", "3",
            str(frames_dir / "%04d.jpg")]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SystemExit(f"抽帧失败：{(proc.stderr or '')[:500]}")

    files = sorted(frames_dir.glob("*.jpg"))
    return [{"seq": i, "file": f.name, "t": round(start + (i - 1) / fps, 3),
             "bytes": f.stat().st_size}
            for i, f in enumerate(files, 1)]


def _dhash(img, size=8) -> int:
    import numpy as np
    g = img.convert("L").resize((size + 1, size))
    a = np.asarray(g, dtype=np.int16)
    bits = (a[:, 1:] > a[:, :-1]).flatten()
    v = 0
    for b in bits:
        v = (v << 1) | int(b)
    return v


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _ink_ratio(gray_small) -> float:
    """墨迹 = 非背景像素占比。

    逐帧自适应，因为固定阈值在深色主题下会整体失效：
    1. 背景水平 = 灰度直方图的峰
    2. 背景 >= 0.95（白底）→ 用固定阈值 0.62 数暗像素
    3. 否则（深色/彩色底）→ OTSU 分两类，取**远离背景**的那一类占比
    4. 两类均值差过小（纯色/低对比）→ ink = 0
    """
    import numpy as np

    hist, edges = np.histogram(gray_small, bins=51, range=(0.0, 1.0))
    # 平滑后取峰桶，用该桶内像素均值作背景水平
    k = np.ones(3) / 3.0
    smooth = np.convolve(hist.astype(np.float32), k, mode="same")
    peak = int(np.argmax(smooth))
    lo, hi = edges[peak], edges[peak + 1]
    in_bin = gray_small[(gray_small >= lo) & (gray_small <= hi)]
    bg = float(in_bin.mean()) if in_bin.size else float(np.median(gray_small))

    if bg >= 0.95:
        return float((gray_small < 0.62).mean())

    # OTSU
    h, e = np.histogram(gray_small, bins=64, range=(0.0, 1.0))
    total = h.sum()
    if total == 0:
        return 0.0
    centers = (e[:-1] + e[1:]) / 2.0
    w0 = np.cumsum(h)
    w1 = total - w0
    m0 = np.cumsum(h * centers)
    m1 = (h * centers).sum() - m0
    with np.errstate(divide="ignore", invalid="ignore"):
        mu0 = m0 / w0
        mu1 = m1 / w1
    valid = (w0 > 0) & (w1 > 0)
    var = np.where(valid, w0 * w1 * (mu0 - mu1) ** 2, -1.0)
    thr_idx = int(np.argmax(var))
    thr = centers[thr_idx]

    # OTSU 阈值落在两类之间但偏背景侧，直接用它做切分会把背景一起圈进来。
    # 取两类的**中点**做切分，保证只数内容那一类。
    thr_mid = (float(mu0[thr_idx]) + float(mu1[thr_idx])) / 2.0
    sep = abs(float(mu1[thr_idx]) - float(mu0[thr_idx]))
    if sep < 0.05:
        return 0.0

    # 内容在阈值的哪一侧：离背景更远的那一类
    d0 = abs(float(mu0[thr_idx]) - bg)
    d1 = abs(float(mu1[thr_idx]) - bg)
    if d1 > d0:
        return float((gray_small >= thr_mid).mean())
    return float((gray_small < thr_mid).mean())


def _sharpness(gray_small) -> float:
    """清晰度：拉普拉斯响应的方差。"""
    import numpy as np
    a = gray_small.astype(np.float32)
    lap = (a[1:-1, 1:-1] * 4 - a[:-2, 1:-1] - a[2:, 1:-1] - a[1:-1, :-2] - a[1:-1, 2:])
    return float(lap.var())


def build_signals(frames_dir: Path, index: list[dict], alpha: float):
    from PIL import Image
    import numpy as np

    sigs, cheap, diffs = [], [], [0.0]
    prev_small = prev_hash = None

    for item in index:
        img = Image.open(frames_dir / item["file"])
        small = np.asarray(img.convert("L").resize((SMALL_W, SMALL_H)), dtype=np.float32) / 255.0
        h = _dhash(img)
        sigs.append((small, h))
        cheap.append({"ink": _ink_ratio(small), "sharp": _sharpness(small)})
        if prev_small is not None:
            ham = _hamming(h, prev_hash) / 64.0
            pix = float(np.abs(small - prev_small).mean())
            diffs.append(round(alpha * ham + (1 - alpha) * pix, 4))
        prev_small, prev_hash = small, h

    return sigs, cheap, diffs


def hard_cuts(diffs: list[float], percentile: float, multiple: float) -> list[bool]:
    import numpy as np
    d = np.asarray(diffs, dtype=np.float32)
    if d.size < 8:
        return [False] * len(diffs)
    thr = max(float(np.quantile(d, percentile)), float(d.mean()) * multiple * 0.5)
    return [bool(x >= thr) for x in diffs]


def stable_runs(diffs: list[float], fps: float, threshold: float, min_sec: float):
    runs, start = [], None
    for i, d in enumerate(diffs):
        if i == 0:
            continue
        if d <= threshold:
            if start is None:
                start = i - 1
        else:
            if start is not None:
                runs.append((start, i - 1))
                start = None
    if start is not None:
        runs.append((start, len(diffs) - 1))
    return [(s, e) for (s, e) in runs if (e - s) / fps >= min_sec]


def after_jump(diffs: list[float], idx: int, lookahead: int) -> float:
    """该帧之后一段时间内的最大帧差。

    手写板书是"写一会停一下"，帧差在书写过程中几乎恒为 0。
    因此"静止"不能作为完成态判据。真正有效的是**后随跳变**：
    - 后随大变化（翻页/擦除/换题）→ 之前那帧才是书写完成态
    - 后随只有微变（继续写）→ 之前那帧还没写完
    """
    end = min(len(diffs), idx + 1 + lookahead)
    return max(diffs[idx + 1:end]) if idx + 1 < end else 0.0


def score_candidate(cheap, ocr_chars, pos_ratio, weights,
                    jump: float = 0.0, ink_total: float = 0.0) -> float:
    norm_sharp = min(cheap["sharp"] / 0.02, 1.0)
    return (weights["weight_chars"] * ocr_chars
            + weights["weight_ink"] * cheap["ink"]
            + weights["weight_sharp"] * norm_sharp
            + weights["weight_jump"] * min(jump / 0.10, 1.0)
            + weights["weight_ink_total"] * ink_total
            + 0.001 * pos_ratio)


def pick_frame(idxs, cheap, ocr_texts, weights, candidates_per_seg,
               stable_mask=None, diffs=None, cfg=None):
    """从该页的帧里挑「终态帧」。

    候选池规则（与 candidate_indices 保持一致）：
    1. 只取稳定帧（Δ 低，排除转场/运动模糊帧）
    2. 强制包含：首帧、末帧、墨迹最多帧、最清晰帧
    3. 剩余额度优先给后段（动画收敛区），再均匀补足
    4. 按 score 排序，取最高分

    手写讲次的关键：静止不等于写完。真正的完成态是"后随大变化"的那一帧，
    由 weight_jump 体现。
    """
    chosen_idx = set(candidate_indices(idxs, cheap, candidates_per_seg, stable_mask))

    span = max(1, len(idxs) - 1)
    base = min(idxs)
    lookahead = int((cfg or {}).get("jump_lookahead_sec", 3.0) * (cfg or {}).get("fps", 2.0))
    cands = []
    for gi in sorted(chosen_idx):
        ocr = ocr_texts.get(gi, "")
        jmp = after_jump(diffs, gi, lookahead) if diffs else 0.0
        ink_total = cheap[gi]["ink"]
        s = score_candidate(cheap[gi], len(ocr), (gi - base) / span, weights,
                            jump=jmp, ink_total=ink_total)
        cands.append({"idx": gi, "pos": gi - base, "score": s,
                      "ink": cheap[gi]["ink"], "sharp": cheap[gi]["sharp"],
                      "jump": jmp,
                      "ocr_chars": len(ocr), "ocr_text": ocr})
    cands.sort(key=lambda c: c["score"], reverse=True)

    # OCR 是概率性识别：对前两名各跑一次，按行合并（模糊去重）得到更完整的页面文字
    if len(cands) > 1:
        import difflib
        lines: list[str] = []
        for c in cands[:2]:
            for ln in re.split(r"[\n；;。]", c.get("ocr_text", "")):
                ln = ln.strip()
                if len(ln) < 2:
                    continue
                if any(difflib.SequenceMatcher(None, ln, old).ratio() >= 0.8 for old in lines):
                    continue
                lines.append(ln)
        if lines:
            merged = "；".join(lines)
            cands[0]["ocr_text"] = merged
            cands[0]["ocr_chars"] = len(merged.replace(" ", ""))

    return cands[0], cands


_NORMCH = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9]+")


def _boiler_re(patterns) -> re.Pattern | None:
    """样板文字正则：页眉/页码/站名水印等每页都出现的文字。

    同页判定前必须剔除，否则任意两页都"很像"。
    课程专有字样写在配置里，不硬编码在代码。
    """
    pats = [str(p) for p in (patterns or []) if str(p).strip()]
    return re.compile("|".join(pats), re.I) if pats else None


def _norm_text(s: str, boiler=None) -> str:
    t = boiler.sub(" ", s) if boiler else (s or "")
    return _NORMCH.sub("", t)


def _grams(text: str, n: int = 4, boiler=None) -> set:
    t = _norm_text(text, boiler)
    return {t[i:i + n] for i in range(0, max(0, len(t) - n + 1))}


def gram_containment(a: str, b: str, n: int = 4, boiler=None) -> float:
    """4-gram 包含度：短的那页有多少 n-gram 出现在长的那页里。

    相比字符集包含度，它对"同页不同阶段"（内容只增不减）敏感，
    而对"两页都提到同一个词"这种巧合不敏感。
    """
    A, B = _grams(a, n, boiler), _grams(b, n, boiler)
    if not A or not B:
        return 0.0
    small, big = (A, B) if len(A) <= len(B) else (B, A)
    return len(small & big) / len(small)


def snap_to_transcript(t: float, srt_segments: list[dict], window: float) -> float:
    if not srt_segments:
        return t
    best, best_d = None, None
    for seg in srt_segments:
        d = abs(seg["from"] - t)
        if best_d is None or d < best_d:
            best, best_d = seg["from"], d
    if best is not None and best_d <= window:
        return round(best, 3)
    return t


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
        segs.append({"from": round(frm, 3), "to": round(to, 3), "text": text.strip()})
    return segs


def segment(index, diffs, sigs, cheap, fps, cfg, srt_segments=None, ocr_texts=None,
            keep_idxs: bool = False):
    """切片主流程。

    keep_idxs=True 时保留 _idxs（供 OCR 阶段定位候选帧），False 时清除。
    """
    ocr_texts = ocr_texts or {}
    th = cfg["diff_threshold"]
    min_sec = cfg["stable_min_sec"]

    runs = stable_runs(diffs, fps, th, min_sec)
    cuts = hard_cuts(diffs, cfg["page_cut_percentile"], cfg["page_cut_multiple"])

    if cfg["page_cut_only_cuts"]:
        kept = []
        lookback_n = int(fps * cfg["page_cut_lookback_sec"])
        for (s, e) in runs:
            lb = max(0, s - lookback_n)
            if any(cuts[i] for i in range(lb, s + 1)):
                kept.append((s, e))
        if kept:
            runs = kept

    # 稳定帧掩码：只有落在稳定段内的帧才进终态帧候选池
    stable_mask = [False] * len(index)
    for (s, e) in runs:
        for i in range(max(0, s), min(len(index) - 1, e) + 1):
            stable_mask[i] = True

    bounds = sorted(set([0] + [s for (s, _) in runs if s > 0]))
    raw_segments = []
    for bi, b in enumerate(bounds):
        end = bounds[bi + 1] if bi + 1 < len(bounds) else len(index)
        idxs = [i for i in range(b, max(end, b + 1)) if 0 <= i < len(index)]
        if idxs:
            raw_segments.append(idxs)

    # 薄段吸收：帧数不足 min_seg_sec 的段并入前段
    merged = []
    for idxs in raw_segments:
        if merged and (idxs[-1] - idxs[0] + 1) / fps < cfg["min_seg_sec"]:
            merged[-1].extend(idxs)
        else:
            merged.append(list(idxs))

    segments = []
    for gi, idxs in enumerate(merged, start=1):
        chosen, cands = pick_frame(
            idxs, cheap, ocr_texts,
            {k: cfg[k] for k in ("weight_chars", "weight_ink", "weight_sharp",
                                 "weight_jump", "weight_ink_total")},
            cfg["candidates_per_seg"], stable_mask, diffs, cfg)
        t_start = index[idxs[0]]["t"]
        t_end = round(index[idxs[-1]]["t"] + 1.0 / fps, 3)
        if srt_segments:
            t_start = snap_to_transcript(t_start, srt_segments, cfg["snap_window"])
        segments.append({
            "id": gi,
            "t_start": t_start,
            "t_end": t_end,
            "n_frames": len(idxs),
            "stable_sec": round(len(idxs) / fps, 2),
            "chosen": {
                "t": index[chosen["idx"]]["t"],
                "file": index[chosen["idx"]]["file"],
                "score": round(chosen["score"], 4),
                "ocr_chars": chosen["ocr_chars"],
                "ocr_text": chosen.get("ocr_text", ""),
                "jump": round(chosen.get("jump", 0.0), 4),
                "ink": round(chosen["ink"], 4),
                "sharp": round(chosen["sharp"], 5),
                "dhash": sigs[chosen["idx"]][1],
            },
            "merged_from": [],
            "_idx": chosen["idx"],
            "_idxs": list(idxs),
        })

    boiler = _boiler_re(cfg.get("boilerplate_patterns"))
    merged_segs = []
    for seg in segments:
        if not merged_segs:
            merged_segs.append(seg)
            continue
        prev = merged_segs[-1]
        same = False
        if _hamming(prev["chosen"]["dhash"], seg["chosen"]["dhash"]) <= cfg["merge_hash_dist"]:
            same = True
        elif prev["chosen"]["ocr_chars"] >= cfg["merge_text_min_chars"] \
                and seg["chosen"]["ocr_chars"] >= cfg["merge_text_min_chars"]:
            if gram_containment(prev["chosen"].get("ocr_text", ""),
                                seg["chosen"].get("ocr_text", ""),
                                boiler=boiler) >= cfg["merge_gram_contain"]:
                same = True
        span = seg["t_end"] - prev["t_start"]
        if same and span <= cfg["merge_max_span_sec"]:
            prev["t_end"] = seg["t_end"]
            prev["n_frames"] += seg["n_frames"]
            prev["merged_from"].append(seg["id"])
        else:
            merged_segs.append(seg)

    # 边界证据：切片层不自己判同页，只把可测量的数字交给写作代理，
    # 由它看图决定相邻两段是否同一张幻灯片的不同阶段。
    import numpy as np
    for i, seg in enumerate(merged_segs):
        seg["boundary_evidence"] = None
        if i == 0:
            continue
        # 用段内真实帧号（_idxs）定位窗口，不用 t*fps：
        # 抽帧可从任意时刻开始（--sections），绝对时间与 diffs 索引不同步。
        idxs = seg.get("_idxs") or []
        hi = idxs[0] if idxs else 0
        lo = max(0, hi - int(fps * 1.5))
        win = (np.asarray(diffs[lo:hi + 1], dtype=np.float32)
               if hi > lo else np.asarray([0.0], dtype=np.float32))
        ev = {"delta_max": round(float(win.max()) if win.size else 0.0, 4)}
        a_idx = merged_segs[i - 1].get("_idx")
        b_idx = seg.get("_idx")
        if a_idx is not None and b_idx is not None:
            a = sigs[a_idx][0]
            b = sigs[b_idx][0]
            ch = np.abs(a - b) > 0.08
            ev["unchanged_frac"] = round(1.0 - float(ch.mean()), 3)
            ev["old_ink"] = round(float((a[ch] < 0.62).mean()), 3) if ch.any() else 1.0
            ev["new_ink"] = round(float((b[ch] < 0.62).mean()), 3) if ch.any() else 1.0
        seg["boundary_evidence"] = ev

    for i, s in enumerate(merged_segs, start=1):
        s["id"] = i
        s.pop("_idx", None)
        if not keep_idxs:
            s.pop("_idxs", None)
    return merged_segs, runs, cuts


def candidate_indices(idxs: list[int], cheap: list[dict], candidates_per_seg: int,
                      stable_mask: list[bool] | None = None) -> list[int]:
    """候选池规则。

    关键：动画页的完整状态出现在稳定区间**末尾**，所以末段权重必须高于均匀采样。
    分配：首帧 1 + 末帧 1 + 墨迹最多 1 + 最清晰 1 + **末段均匀采样**占剩余额度。
    """
    pool = [i for i in idxs if (stable_mask[i] if stable_mask else True)] or list(idxs)
    k = max(4, candidates_per_seg)

    chosen = {pool[0], pool[-1]}
    if len(pool) >= 3:
        chosen.add(max(pool, key=lambda i: cheap[i]["ink"]))
        chosen.add(max(pool, key=lambda i: cheap[i]["sharp"]))

    # 剩余额度优先投给**后段**（动画收敛区）
    remain = k - len(chosen)
    if remain > 0 and len(pool) > 1:
        # 后 40% 的帧里均匀取，再把剩余补给前段
        tail = pool[max(0, int(len(pool) * 0.6)):]
        tail_step = max(1, len(tail) // max(1, remain * 2 // 3 + 1))
        for i in tail[::tail_step]:
            if len(chosen) >= k:
                break
            chosen.add(i)
        if len(chosen) < k:
            step = max(1, len(pool) // (k - len(chosen) + 1))
            for i in pool[::step]:
                chosen.add(i)
                if len(chosen) >= k:
                    break

    if len(chosen) > k:
        ordered = sorted(chosen)
        keep = {ordered[0], ordered[-1]}
        step = max(1, len(ordered) // (k - len(keep)))
        for i in ordered[::step]:
            keep.add(i)
            if len(keep) >= k:
                break
        chosen = keep
    return sorted(chosen)


def ocr_candidates(out_dir: Path, index: list[dict], segments: list[dict],
                   cheap: list[dict], cfg: dict, media: Path | None = None,
                   ffmpeg: str = "") -> dict[int, str]:
    """对每段的候选帧跑 OCR，返回 {帧序号: 文本}。

    用 segment 里记录的 _idxs（真实帧号），不按 t_start/t_end 反查——
    t_start 会被字幕吸附偏移，反查会漏掉边界帧。

    读字必须回原生分辨率：720p 缩略帧上的小字与公式下标会糊，
    因此有媒体文件时现抽原生帧（PNG 无损）再 OCR。
    """
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception as exc:
        print(f"OCR 不可用：{exc}")
        return {}

    ocr = RapidOCR()
    texts: dict[int, str] = {}
    native_count = 0
    for seg in segments:
        idxs = seg.get("_idxs") or []
        for gi in candidate_indices(idxs, cheap, cfg["candidates_per_seg"]):
            if gi in texts:
                continue
            if media is not None and ffmpeg and cfg.get("native_ocr"):
                # 原生分辨率现抽（仅当显式开启，且原生高于抽帧高度）
                nf = extract_native(ffmpeg, media, index[gi]["t"], out_dir,
                                    cfg.get("height", 720))
                if nf is not None:
                    src = nf["path"]
                    native_count += 1
                else:
                    src = str(out_dir / "frames" / index[gi]["file"])
            else:
                src = str(out_dir / "frames" / index[gi]["file"])
            res, _ = ocr(src)
            texts[gi] = " ".join(r[1] for r in (res or []))
    print(f"OCR 完成：{len(texts)} 帧"
          + (f"（其中 {native_count} 帧用原生分辨率）" if native_count else ""))
    return texts


def native_size(ffmpeg: str, media: Path) -> tuple[int, int] | None:
    """读媒体原生分辨率。"""
    out = subprocess.run([ffmpeg, "-i", str(media)],
                         capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    m = re.search(r"Stream #.*Video.*?(\d{2,5})x(\d{2,5})", out.stderr or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def extract_native(ffmpeg: str, media: Path, t: float, out_dir: Path,
                   frame_height: int = 720) -> dict | None:
    """现抽一张原生分辨率单帧用于读字。

    抽帧阶段为速度会缩放到 frame_height，但读字（小字/公式/页脚）必须回原生分辨率：
    缩放等于丢像素，有损编码等于给字加噪声。

    只在原生分辨率高于抽帧高度时才现抽——否则没有额外信息，白费解码。
    落盘用 PNG 无损，文件名带 t 与尺寸，同一时刻第二次直接复用不解码。
    """
    native = native_size(ffmpeg, media)
    if native is None or native[1] <= frame_height:
        return None

    tag = f"{native[0]}x{native[1]}"
    dest_dir = out_dir / "frames_native"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / ("at_%09.3f_%s.png" % (float(t), tag))

    if dest.exists():
        return {"t": round(float(t), 3), "path": str(dest),
                "size": native, "reused": True}

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-ss", "%.3f" % float(t), "-i", str(media),
           "-frames:v", "1", str(dest)]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not dest.exists():
        raise SystemExit(f"现抽原生帧失败：{(proc.stderr or '')[:300]}")
    return {"t": round(float(t), 3), "path": str(dest),
            "size": native, "reused": False}


def main(argv):
    ap = argparse.ArgumentParser(description="抽帧 + 稳定段切分 + 终态帧选择")
    ap.add_argument("video", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--fps", type=float, default=DEFAULTS["fps"])
    ap.add_argument("--height", type=int, default=DEFAULTS["height"])
    ap.add_argument("--sections", default="")
    ap.add_argument("--subtitle", type=Path, default=None, help="SRT，用于段边界吸附")
    ap.add_argument("--ffmpeg", default="")
    ap.add_argument("--ocr", action="store_true", help="对候选帧跑 OCR（终态帧打分需要）")
    ap.add_argument("--native-ocr", action="store_true",
                    help="OCR 时现抽原生分辨率帧（仅当原生高于抽帧高度；实测收益有限）")
    args = ap.parse_args(argv)

    ffmpeg = find_ffmpeg(args.ffmpeg)
    if not args.video.is_file():
        raise SystemExit(f"视频不存在：{args.video}")

    cfg = dict(DEFAULTS)
    cfg["fps"] = args.fps
    cfg["height"] = args.height
    cfg["native_ocr"] = args.native_ocr

    duration = probe_duration(ffmpeg, args.video)
    start, end = 0.0, None
    if args.sections:
        a, b = args.sections.split("-", 1)
        start, end = parse_hms(a), parse_hms(b)

    print(f"视频：{args.video.name}（{duration:.1f}s）")
    print(f"抽帧：fps={cfg['fps']} height={cfg['height']}"
          + (f" 片段={start}-{end}" if args.sections else ""))

    index = extract_frames(ffmpeg, args.video, args.out, cfg["fps"], cfg["height"], start, end)
    print(f"抽出 {len(index)} 帧")

    sigs, cheap, diffs = build_signals(args.out / "frames", index, cfg["diff_alpha"])
    for item, d in zip(index, diffs):
        item["diff"] = d

    srt_segments = None
    if args.subtitle and args.subtitle.is_file():
        srt_segments = parse_srt(args.subtitle)
        print(f"字幕 {len(srt_segments)} 条（用于段边界吸附）")

    # 第一轮：不跑 OCR 先切片（保留 _idxs 供 OCR 阶段使用）
    segments, runs, cuts = segment(index, diffs, sigs, cheap, cfg["fps"], cfg,
                                   srt_segments, {}, keep_idxs=True)

    ocr_texts: dict[int, str] = {}
    if args.ocr:
        ocr_texts = ocr_candidates(args.out, index, segments, cheap, cfg,
                                   media=args.video, ffmpeg=ffmpeg)
        # 第二轮：带 OCR 重新切片（终态帧打分与同页合并都依赖 OCR）
        segments, runs, cuts = segment(index, diffs, sigs, cheap, cfg["fps"], cfg,
                                       srt_segments, ocr_texts)

    payload = {
        "video": str(args.video),
        "duration": duration,
        "fps": cfg["fps"],
        "height": cfg["height"],
        "sections": args.sections or None,
        "params": dict(cfg),
        "frames": index,
        "segments": segments,
        "stats": {
            "frame_count": len(index),
            "stable_runs": len(runs),
            "hard_cuts": sum(cuts),
            "segment_count": len(segments),
            "merged_count": sum(len(s["merged_from"]) for s in segments),
            "avg_segment_sec": round(
                sum(s["t_end"] - s["t_start"] for s in segments) / len(segments), 2) if segments else 0,
            "max_diff": max(diffs) if diffs else 0,
        },
    }
    out_file = args.out / "index.json"
    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    st = payload["stats"]
    print(f"\n稳定段 {st['stable_runs']} 个，硬切 {st['hard_cuts']} 处")
    print(f"最终 {st['segment_count']} 段（合并 {st['merged_count']} 个），平均段长 {st['avg_segment_sec']}s")
    print("\n前 10 段：")
    for s in segments[:10]:
        print(f"  {s['t_start']:7.1f}s - {s['t_end']:7.1f}s  ({s['n_frames']} 帧)  "
              f"chosen={s['chosen']['file']} score={s['chosen']['score']:.2f}")
    print(f"\n→ {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
