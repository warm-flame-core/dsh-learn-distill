#!/usr/bin/env python3
"""环境自检：检查依赖、模型、配置是否就绪。

用法：
    python scripts/env_check.py
    python scripts/env_check.py --config config/local.toml
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SKILL_ROOT = Path(__file__).resolve().parent.parent

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"

results: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str) -> None:
    results.append((name, status, detail))


def check_python() -> None:
    v = sys.version_info
    if v >= (3, 13):
        record("Python 版本", OK, f"{v.major}.{v.minor}.{v.micro}")
    elif v >= (3, 10):
        record("Python 版本", WARN,
               f"{v.major}.{v.minor}.{v.micro}（onnxruntime 可能无对应 wheel，建议 3.13+）")
    else:
        record("Python 版本", FAIL, f"{v.major}.{v.minor}（需 ≥3.13）")


def check_module(mod: str, required: bool = True) -> None:
    spec = importlib.util.find_spec(mod)
    if spec is None:
        record(f"模块 {mod}", FAIL if required else WARN, "未安装")
    else:
        try:
            m = importlib.import_module(mod)
            ver = getattr(m, "__version__", "?")
            record(f"模块 {mod}", OK, str(ver))
        except Exception as exc:
            record(f"模块 {mod}", FAIL, f"导入失败：{exc}")


def check_exe(path_or_name: str, label: str) -> None:
    if not path_or_name:
        found = shutil.which(label)
        if found:
            record(f"可执行 {label}", OK, found)
        else:
            record(f"可执行 {label}", FAIL, "未找到（PATH 中无，且配置未指定绝对路径）")
        return
    p = Path(path_or_name)
    if p.is_file():
        record(f"可执行 {label}", OK, str(p))
    else:
        record(f"可执行 {label}", FAIL, f"配置路径不存在：{p}")


def check_gpu() -> None:
    try:
        import ctranslate2
        n = ctranslate2.get_cuda_device_count()
        if n > 0:
            types = sorted(ctranslate2.get_supported_compute_types("cuda"))
            record("GPU (CTranslate2)", OK, f"{n} 个设备，支持 {types}")
        else:
            record("GPU (CTranslate2)", WARN, "无可用 CUDA 设备，将退回 CPU")
    except Exception as exc:
        record("GPU (CTranslate2)", WARN, f"检测失败：{exc}")


def check_models(cfg: dict) -> None:
    qwen = (cfg.get("models") or {}).get("qwen_vl_model") or ""
    if qwen:
        p = Path(qwen)
        record("Qwen2.5-VL 模型", OK if p.is_file() else FAIL,
               str(p) if p.is_file() else f"不存在：{p}")
    else:
        record("Qwen2.5-VL 模型", WARN, "未配置（手写识别不可用）")

    mmproj = (cfg.get("models") or {}).get("qwen_vl_mmproj") or ""
    if mmproj:
        p = Path(mmproj)
        record("mmproj", OK if p.is_file() else FAIL,
               str(p) if p.is_file() else f"不存在：{p}")

    hf = (cfg.get("models") or {}).get("hf_home") or ""
    if hf:
        p = Path(hf)
        record("HF_HOME", OK if p.is_dir() else WARN,
               str(p) if p.is_dir() else f"目录不存在：{p}")


def check_glossary() -> None:
    gdir = SKILL_ROOT / "assets" / "glossary"
    files = sorted(gdir.glob("*.json"))
    if not files:
        record("词库", FAIL, "assets/glossary 下无词库文件")
        return
    total = 0
    bad = []
    import json
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            total += len(d.get("rules") or [])
        except Exception:
            bad.append(f.name)
    if bad:
        record("词库", FAIL, f"解析失败：{bad}")
    else:
        record("词库", OK, f"{len(files)} 个文件，{total} 条规则")


def check_auth(cfg: dict) -> None:
    auth = (cfg.get("bilibili") or {}).get("auth_file") or ""
    if not auth:
        record("B站登录态", WARN, "未配置 auth_file（字幕与高画质不可用）")
        return
    p = Path(auth)
    record("B站登录态", OK if p.is_file() else WARN,
           str(p) if p.is_file() else f"认证文件不存在：{p}")


def load_config(path: Path | None) -> dict:
    try:
        import tomllib
    except ImportError:
        return {}
    cfg: dict = {}
    default = SKILL_ROOT / "config" / "default.toml"
    for f in [default, path] if path else [default]:
        if f and Path(f).is_file():
            with Path(f).open("rb") as fh:
                data = tomllib.load(fh)
            for k, v in data.items():
                if isinstance(v, dict):
                    cfg.setdefault(k, {}).update(v)
                else:
                    cfg[k] = v
    return cfg


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="环境自检")
    ap.add_argument("--config", type=Path, default=None)
    args = ap.parse_args(argv)

    cfg = load_config(args.config)

    check_python()
    for mod in ["yutto", "faster_whisper", "ctranslate2",
                "rapidocr_onnxruntime", "onnxruntime",
                "pymupdf", "pypdf", "yaml"]:
        check_module(mod)
    check_module("docx", required=False)
    check_module("pptx", required=False)

    check_exe((cfg.get("tools") or {}).get("ffmpeg") or "", "ffmpeg")
    check_exe((cfg.get("tools") or {}).get("ffprobe") or "", "ffprobe")
    check_exe((cfg.get("tools") or {}).get("yutto") or "", "yutto")

    check_gpu()
    check_models(cfg)
    check_glossary()
    check_auth(cfg)

    print("=" * 60)
    fails = warns = 0
    for name, status, detail in results:
        mark = {"OK": "✓", "WARN": "!", "FAIL": "✗"}[status]
        print(f"  {mark} {name:26} {detail}")
        if status == FAIL:
            fails += 1
        elif status == WARN:
            warns += 1
    print("=" * 60)
    print(f"结果：{len(results)} 项，{fails} 失败，{warns} 警告")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
