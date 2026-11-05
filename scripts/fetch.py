#!/usr/bin/env python3
"""取素材：列分P、下载字幕/视频、过滤介绍页。

用法：
    python scripts/fetch.py list <BV号> -o <out.json>
    python scripts/fetch.py subs <BV号> -p <N> -d <dir>
    python scripts/fetch.py video <BV号> -p <N> -d <dir> [-q 80]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Referer": "https://www.bilibili.com",
}

API_VIEW = "https://api.bilibili.com/x/web-interface/view"

# 介绍页判据
INTRO_TITLE_PATTERNS = [
    "简介", "介绍", "前言", "预告", "导学", "课程说明",
    "获取", "最后", "结尾", "致谢", "花絮", "广告",
]
INTRO_MIN_DURATION = 180.0


def parse_bv(target: str) -> str:
    """从 URL 或裸 BV 号里提取 BV 号。"""
    import re
    m = re.search(r"(BV[0-9A-Za-z]{10})", target)
    if not m:
        raise SystemExit(f"无法解析 BV 号：{target}")
    return m.group(1)


def fetch_pages(bv: str) -> dict:
    """取课程元信息与分P列表。"""
    url = f"{API_VIEW}?bvid={bv}"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if data.get("code") != 0:
        raise SystemExit(f"B站 API 返回错误：{data.get('code')} {data.get('message')}")

    d = data["data"]
    pages = []
    for p in d.get("pages") or []:
        pages.append({
            "page": p["page"],
            "cid": p["cid"],
            "title": p.get("part", ""),
            "duration": p.get("duration", 0),
        })

    return {
        "bvid": d.get("bvid"),
        "aid": d.get("aid"),
        "title": d.get("title"),
        "owner": (d.get("owner") or {}).get("name"),
        "total_duration": d.get("duration"),
        "page_count": d.get("videos"),
        "pages": pages,
    }


def filter_pages(pages: list[dict]) -> tuple[list[dict], list[dict]]:
    """过滤介绍页/广告页。"""
    keep, drop = [], []
    for p in pages:
        title = p.get("title") or ""
        dur = float(p.get("duration") or 0)
        reason = None
        if dur < INTRO_MIN_DURATION:
            reason = f"时长 {dur:.0f}s < {INTRO_MIN_DURATION:.0f}s"
        if any(k in title for k in INTRO_TITLE_PATTERNS):
            reason = (reason + "；" if reason else "") + "标题含介绍类词"
        if reason:
            drop.append({**p, "reason": reason})
        else:
            keep.append(p)
    return keep, drop


def find_yutto(cfg_path: str = "") -> str:
    """探测 yutto：配置 > 环境变量 > PATH > 常见安装位置。"""
    import os
    import shutil

    if cfg_path and Path(cfg_path).is_file():
        return cfg_path
    env = os.environ.get("LEARN_DISTILL_YUTTO", "").strip()
    if env and Path(env).is_file():
        return env
    found = shutil.which("yutto")
    if found:
        return found
    local_app = os.environ.get("LOCALAPPDATA", "")
    if local_app:
        for c in sorted(Path(local_app, "Programs", "Python").glob("Python3*/Scripts/yutto.*")):
            if c.is_file():
                return str(c)
    raise SystemExit(
        "找不到 yutto。安装方法（任选）：\n"
        "  1) pip install yutto\n"
        "  2) 在 config/local.toml 的 [tools] yutto 写绝对路径\n"
        "  3) 设环境变量 LEARN_DISTILL_YUTTO")


def run_yutto(args: list[str], yutto_path: str = "", ffmpeg_path: str = "") -> int:
    """调用 yutto。

    yutto 合并音视频时内部调用 ffmpeg，必须保证 ffmpeg 在 PATH 里。
    """
    import os
    exe = find_yutto(yutto_path)

    env = dict(os.environ)
    ffmpeg = ffmpeg_path.strip() if ffmpeg_path else ""
    if not ffmpeg:
        try:
            from media_util import find_ffmpeg
            ffmpeg = find_ffmpeg()
        except Exception:
            ffmpeg = ""
    if ffmpeg:
        env["PATH"] = str(Path(ffmpeg).parent) + os.pathsep + env.get("PATH", "")

    proc = subprocess.run([exe] + args, text=True,
                          encoding="utf-8", errors="replace", env=env)
    if proc.returncode != 0:
        print(f"yutto 失败（rc={proc.returncode}）", file=sys.stderr)
    return proc.returncode


def cmd_list(args) -> int:
    bv = parse_bv(args.target)
    info = fetch_pages(bv)
    keep, drop = filter_pages(info["pages"])

    print(f"《{info['title']}》 | {info['owner']}")
    print(f"共 {info['page_count']} 个分P，总时长 {info['total_duration']/60:.1f} 分钟")
    print()
    print(f"{'P':>3} {'时长':>8} {'标题':<40} 状态")
    print("-" * 70)
    drop_ids = {d["page"] for d in drop}
    for p in info["pages"]:
        m, s = divmod(int(p["duration"]), 60)
        mark = "排除" if p["page"] in drop_ids else "保留"
        print(f"{p['page']:>3} {m:>3}:{s:02d} {p['title'][:40]:<40} {mark}")

    print()
    if drop:
        print(f"自动排除 {len(drop)} 个：")
        for d in drop:
            print(f"  p{d['page']} 「{d['title'][:40]}」→ {d['reason']}")
    print(f"待蒸馏 {len(keep)} 个，合计 {sum(p['duration'] for p in keep)/60:.1f} 分钟")

    payload = {
        **info,
        "keep": keep,
        "drop": drop,
        "recommended": [p["page"] for p in keep],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {args.out}")
    return 0


def cmd_subs(args) -> int:
    bv = parse_bv(args.target)
    args.dir.mkdir(parents=True, exist_ok=True)
    url = f"https://www.bilibili.com/video/{bv}?p={args.page}"
    cmd = [url, "-d", str(args.dir), "--subtitle-only", "--no-danmaku"]
    if args.auth_file:
        cmd += ["--auth-file", str(args.auth_file)]
    print(f"取字幕：p{args.page}")
    return run_yutto(cmd, args.yutto or "", getattr(args, "ffmpeg", "") or "")


def cmd_video(args) -> int:
    bv = parse_bv(args.target)
    args.dir.mkdir(parents=True, exist_ok=True)
    url = f"https://www.bilibili.com/video/{bv}?p={args.page}"
    cmd = [url, "-d", str(args.dir), "-q", str(args.quality), "--no-danmaku"]
    if args.auth_file:
        cmd += ["--auth-file", str(args.auth_file)]
    print(f"下载视频：p{args.page} q={args.quality}")
    return run_yutto(cmd, args.yutto or "", getattr(args, "ffmpeg", "") or "")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="取素材：列分P、下载字幕/视频")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列分P并过滤介绍页")
    p_list.add_argument("target", help="BV号或URL")
    p_list.add_argument("-o", "--out", type=Path, required=True)
    p_list.set_defaults(func=cmd_list)

    for name, func, helptext in [("subs", cmd_subs, "只取字幕"),
                                 ("video", cmd_video, "下载视频")]:
        p = sub.add_parser(name, help=helptext)
        p.add_argument("target", help="BV号或URL")
        p.add_argument("-p", "--page", type=int, required=True, help="分P序号")
        p.add_argument("-d", "--dir", type=Path, required=True, help="输出目录")
        p.add_argument("--auth-file", type=Path, default=None, help="yutto auth.toml")
        p.add_argument("--yutto", default="", help="yutto 可执行文件路径")
        p.add_argument("--ffmpeg", default="", help="ffmpeg 路径（yutto 合并需要）")
        if name == "video":
            p.add_argument("-q", "--quality", type=int, default=80,
                           help="画质：120=4K 80=1080P 64=720P")
        p.set_defaults(func=func)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))