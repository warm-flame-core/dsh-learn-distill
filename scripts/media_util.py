#!/usr/bin/env python3
"""外部程序探测：ffmpeg / ffprobe。

探测顺序：配置绝对路径 > 环境变量 > PATH > 常见安装位置。
常见位置只是兜底，不是硬编码依赖——找不到就报错并给出配置方法。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ENV_FFMPEG = "LEARN_DISTILL_FFMPEG"
ENV_FFPROBE = "LEARN_DISTILL_FFPROBE"

# 常见安装位置（兜底，不是必需）
COMMON_FFMPEG = [
    "/usr/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    r"C:\ffmpeg\bin\ffmpeg.exe",
]


def find_ffmpeg(cfg_path: str = "") -> str:
    """按优先级探测 ffmpeg。"""
    if cfg_path and Path(cfg_path).is_file():
        return cfg_path
    env = os.environ.get(ENV_FFMPEG, "").strip()
    if env and Path(env).is_file():
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    for c in COMMON_FFMPEG:
        if Path(c).is_file():
            return c
    raise SystemExit(
        "找不到 ffmpeg。配置方法（任选）：\n"
        "  1) 在 config/local.toml 的 [tools] ffmpeg 写绝对路径\n"
        f"  2) 设环境变量 {ENV_FFMPEG}\n"
        "  3) 把 ffmpeg 加入 PATH")


def find_ffprobe(cfg_path: str = "") -> str | None:
    """按优先级探测 ffprobe。找不到返回 None（调用方退化解析 ffmpeg stderr）。"""
    if cfg_path and Path(cfg_path).is_file():
        return cfg_path
    env = os.environ.get(ENV_FFPROBE, "").strip()
    if env and Path(env).is_file():
        return env
    found = shutil.which("ffprobe")
    if found:
        return found
    # 与 ffmpeg 同目录
    for c in COMMON_FFMPEG:
        p = Path(c).parent / ("ffprobe" + (".exe" if c.endswith(".exe") else ""))
        if p.is_file():
            return str(p)
    return None


def probe_media(ffmpeg: str, media: Path) -> dict:
    """读媒体基本信息：时长、分辨率、帧率、有无音轨。"""
    import re
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


def media_duration(ffmpeg: str, media: Path) -> float:
    return probe_media(ffmpeg, media)["duration"]


def sha256_file(path: Path, chunk: int = 4 * 1024 * 1024) -> str:
    """流式算文件摘要。"""
    import hashlib
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for blk in iter(lambda: fh.read(chunk), b""):
            h.update(blk)
    return "sha256:" + h.hexdigest()