#!/usr/bin/env python3
"""OCR 调度：印刷体走 RapidOCR，手写页走 Qwen2.5-VL。

判据：
- 手写页 = 核心区彩色笔画占比高（手写笔迹通常是彩色，PPT 印刷体是灰黑）
- 侧栏（右 20%）排除，避免 PPT 模板的彩色装饰干扰

用法：
    python scripts/ocr.py <frame_or_dir> -o <out.json> [--vlm]
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

# 手写判据：先用廉价像素特征筛出候选，再用 VLM/RapidOCR 的识别质量做终判。
#
# 像素特征不能单用：手写也可能是黑笔（核心区彩色 0.0000），
# 印刷体也可能带彩色装饰（0.0044）。实测两者区间重叠。
HANDWRITING_CORE_RATIO = 0.012
SAT_MIN = 0.35
VAL_MIN = 0.30

# 识别质量判据：VLM 字数 / RapidOCR 字数
# 手写页 RapidOCR 只能出碎片，VLM 能出完整 LaTeX
VLM_RAPID_RATIO_THRESHOLD = 2.0
VLM_MIN_CHARS = 40


def page_metrics(img_path: Path) -> dict:
    """页面特征：彩色笔画占比、核心区彩色占比、边缘密度、墨迹占比。"""
    from PIL import Image
    import numpy as np

    img = Image.open(img_path).convert("RGB")
    hsv = np.asarray(img.convert("HSV"), dtype=np.float32)
    S = hsv[:, :, 1] / 255.0
    V = hsv[:, :, 2] / 255.0
    sat_mask = (S >= SAT_MIN) & (V >= VAL_MIN)

    w = hsv.shape[1]
    core = sat_mask[:, int(w * 0.05):int(w * 0.80)]

    g = np.asarray(img.convert("L"), dtype=np.float32) / 255.0
    gx = np.abs(np.diff(g, axis=1))
    gy = np.abs(np.diff(g, axis=0))
    edge = float(((gx > 0.15).mean() + (gy > 0.15).mean()) / 2)

    return {
        "color_ratio": round(float(sat_mask.mean()), 4),
        "core_color_ratio": round(float(core.mean()), 4),
        "edge_density": round(edge, 4),
        "is_handwriting": bool(float(core.mean()) >= HANDWRITING_CORE_RATIO),
    }


def ocr_rapid(img_path: Path) -> dict:
    """RapidOCR：印刷体识别。返回块列表与置信度统计。"""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception as exc:
        return {"available": False, "error": str(exc), "blocks": [], "text": ""}

    ocr = RapidOCR()
    res, _ = ocr(str(img_path))
    blocks = []
    for r in (res or []):
        box, txt, conf = r[0], r[1], float(r[2])
        blocks.append({
            "text": txt,
            "conf": round(conf, 3),
            "y": int(box[0][1]),
            "x": int(box[0][0]),
        })
    blocks.sort(key=lambda b: (b["y"], b["x"]))
    confs = [b["conf"] for b in blocks]
    return {
        "available": True,
        "blocks": blocks,
        "text": " ".join(b["text"] for b in blocks),
        "avg_conf": round(sum(confs) / len(confs), 3) if confs else 0.0,
        "low_conf_ratio": round(sum(1 for c in confs if c < 0.75) / len(confs), 3) if confs else 0.0,
    }


def ocr_vlm(img_path: Path, llama_bin: Path, model: Path, mmproj: Path,
            prompt: str = "", n_predict: int = 1200, timeout: int = 300) -> dict:
    """Qwen2.5-VL：手写/公式识别，输出 LaTeX。"""
    if not llama_bin.is_file():
        return {"available": False, "error": f"llama-mtmd-cli 不存在：{llama_bin}"}
    if not model.is_file():
        return {"available": False, "error": f"模型不存在：{model}"}
    if not mmproj.is_file():
        return {"available": False, "error": f"mmproj 不存在：{mmproj}"}

    p = prompt or (
        "识别图片中所有手写内容。公式用 LaTeX 输出，文字照抄。"
        "按从上到下、从左到右顺序。只输出内容，不要解释。")

    cmd = [str(llama_bin), "-m", str(model), "--mmproj", str(mmproj),
           "--image", str(img_path), "-p", p, "-n", str(n_predict),
           "--temp", "0.1", "-ngl", "99"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"available": False, "error": f"VLM 超时（{timeout}s）"}

    out = proc.stdout or ""
    # 剥掉 llama.cpp 的日志行（时间戳开头）
    lines = [ln for ln in out.splitlines()
             if not re.match(r"^\d+\.\d+\.\d+\.\d+ ", ln)]
    text = "\n".join(lines).strip()
    # 去掉 chat template 的提示部分
    if "<|im_start|>assistant" in text:
        text = text.split("<|im_start|>assistant", 1)[1].strip()
    text = text.replace("<|im_end|>", "").strip()

    # 截断检测：输出没有常见收尾标记 → 可能被 n_predict 截断
    truncated = False
    if text:
        tail = text[-40:]
        if not any(t in tail for t in ("```", "。", "$$", "\\end", "\n\n")):
            truncated = True

    return {
        "available": True,
        "text": text,
        "chars": len(text.replace(" ", "")),
        "returncode": proc.returncode,
        "truncated": truncated,
    }


def process_frame(img_path: Path, vlm_cfg: dict, force_vlm: bool = False,
                  auto_vlm: bool = False) -> dict:
    """单帧处理：先判手写，再决定走哪条 OCR。

    auto_vlm=True 时做双路识别对比：
    - 先跑 RapidOCR
    - 若 RapidOCR 结果像碎片（字数少）→ 再跑 VLM
    - VLM 字数 / RapidOCR 字数 ≥ 阈值 → 判为手写，用 VLM 结果
    - 否则判为印刷，用 RapidOCR 结果

    像素特征（核心区彩色占比）只作辅助记录，不作主判据——
    手写也可能用黑笔（实测核心彩色 0.0000），印刷也可能带彩色装饰。
    """
    m = page_metrics(img_path)
    result = {"file": img_path.name, "metrics": m, "engine": None}

    vlm_ready = all(vlm_cfg.get(k) for k in ("llama_bin", "model", "mmproj"))
    use_vlm = force_vlm or (auto_vlm and vlm_ready)

    if not use_vlm:
        r = ocr_rapid(img_path)
        result["engine"] = "rapidocr"
        result["rapid"] = r
        result["text"] = r.get("text", "")
        return result

    # 双路识别
    r = ocr_rapid(img_path)
    result["rapid"] = r
    rc = len(r.get("text", "").replace(" ", ""))

    v = ocr_vlm(img_path,
                Path(vlm_cfg["llama_bin"]),
                Path(vlm_cfg["model"]),
                Path(vlm_cfg["mmproj"]),
                n_predict=vlm_cfg.get("n_predict", 1200))
    result["vlm"] = v
    vc = v.get("chars", 0) if v.get("available") else 0
    ratio = vc / max(1, rc)
    result["ratio"] = round(ratio, 2)

    is_handwriting = (force_vlm
                      or (ratio >= VLM_RAPID_RATIO_THRESHOLD and vc >= VLM_MIN_CHARS))
    result["is_handwriting"] = is_handwriting
    result["metrics"]["is_handwriting"] = is_handwriting

    if is_handwriting and v.get("available"):
        result["engine"] = "vlm"
        result["text"] = v["text"]
    else:
        result["engine"] = "rapidocr"
        result["text"] = r.get("text", "")
    return result


def load_config(path: Path | None) -> dict:
    try:
        import tomllib
    except ImportError:
        return {}
    cfg = {}
    if path and Path(path).is_file():
        with Path(path).open("rb") as fh:
            cfg = tomllib.load(fh)
    return cfg


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="OCR 调度：印刷体 RapidOCR / 手写 Qwen2.5-VL")
    ap.add_argument("target", type=Path, help="图片或目录")
    ap.add_argument("-o", "--out", type=Path, required=True, help="输出 JSON")
    ap.add_argument("--config", type=Path, default=None, help="local.toml")
    ap.add_argument("--force-vlm", action="store_true", help="强制走 VLM")
    ap.add_argument("--auto-vlm", action="store_true",
                    help="双路识别对比：RapidOCR 结果像碎片时再跑 VLM，按字数比判定手写")
    ap.add_argument("--llama-bin", type=Path, default=None)
    ap.add_argument("--model", type=Path, default=None)
    ap.add_argument("--mmproj", type=Path, default=None)
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    ocr_cfg = cfg.get("ocr") or {}
    models = cfg.get("models") or {}

    vlm_cfg = {
        "llama_bin": str(args.llama_bin or ocr_cfg.get("llama_bin") or ""),
        "model": str(args.model or models.get("qwen_vl_model") or ""),
        "mmproj": str(args.mmproj or models.get("qwen_vl_mmproj") or ""),
        "n_predict": int(ocr_cfg.get("vlm_max_tokens", 1200)),
    }

    files = ([args.target] if args.target.is_file()
             else sorted(args.target.glob("*.jpg")) + sorted(args.target.glob("*.png")))
    if not files:
        print(f"没有可处理的图片：{args.target}", file=sys.stderr)
        return 1

    print(f"处理 {len(files)} 张图")
    mode = "force-vlm" if args.force_vlm else ("auto-vlm" if args.auto_vlm else "rapidocr")
    print(f"识别模式：{mode}")
    if args.auto_vlm:
        print(f"手写判据：VLM字数/RapidOCR字数 ≥ {VLM_RAPID_RATIO_THRESHOLD} "
              f"且 VLM ≥ {VLM_MIN_CHARS} 字")

    results = []
    n_hw = 0
    for f in files:
        r = process_frame(f, vlm_cfg, args.force_vlm, args.auto_vlm)
        results.append(r)
        if r["metrics"]["is_handwriting"]:
            n_hw += 1
        flag = "手写" if r["metrics"]["is_handwriting"] else "印刷"
        extra = (f" 比值={r['ratio']:.1f} rapid={r['rapid'].get('text','')[:0]}"
                 if "ratio" in r else "")
        print(f"  {f.name:16} [{flag}] 核心彩色={r['metrics']['core_color_ratio']:.4f} "
              f"→ {r['engine']} ({len(r.get('text',''))}字){extra}")

    payload = {
        "source": str(args.target),
        "count": len(files),
        "handwriting_count": n_hw,
        "thresholds": {"core_color_ratio": HANDWRITING_CORE_RATIO},
        "frames": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n手写页 {n_hw}/{len(files)}")
    print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
