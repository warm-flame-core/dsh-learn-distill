#!/usr/bin/env python3
"""learn-distill 主入口：编排完整蒸馏流程。

用法：
    python scripts/distill.py <target> --goal deep_mastery|exam_cram [options]

阶段：
  0 参数解析 → 1 probe → 2 代价闸门 → 3 取素材 → 4 术语纠错
  → 5 抽帧OCR → 6 对齐 → 7 写作（外部代理）→ 8 装配 → 9 校验 → 10 盲测
"""

from __future__ import annotations

import argparse
import json
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

SCRIPTS = Path(__file__).resolve().parent

GOAL_ALIASES = {
    "deep_mastery": ["深度掌握", "好好学", "精通", "吃透", "学明白"],
    "exam_cram": ["考试突击", "备考", "速成", "不挂科", "应付考试"],
}

SHORT_COURSE_LECTURES = 3
SHORT_COURSE_HOURS = 2.0


def py() -> str:
    return sys.executable


def run(cmd: list[str], desc: str) -> int:
    print(f"\n{'='*64}\n[{desc}]")
    shown = " ".join(str(c) for c in cmd[:8])
    print("  " + shown + (" ..." if len(cmd) > 8 else ""))
    return subprocess.run(cmd).returncode


def load_cfg(path: Path | None) -> dict:
    if not path or not path.is_file():
        return {}
    try:
        import tomllib
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except Exception:
        return {}


def cfg_get(cfg: dict, section: str, key: str, default=""):
    return (cfg.get(section) or {}).get(key, default)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="网课蒸馏：deep_mastery（自足讲义+PLAYBOOK）/ exam_cram（Anki 卡片）")
    ap.add_argument("target", help="BV号 / B站URL / 本地视频路径")
    ap.add_argument("--goal", required=True, choices=["deep_mastery", "exam_cram"],
                    help="学习目标（必填，无默认值）")
    ap.add_argument("--course", default="", help="课程 id（kebab-case），不填则自动生成")
    ap.add_argument("--pages", default="", help="分P范围，如 2-19 或 1,3,5")
    ap.add_argument("--sections", default="", help="只跑片段，如 00:00:00-00:05:00")
    ap.add_argument("--textbook", type=Path, default=None)
    ap.add_argument("--slides", type=Path, default=None)
    ap.add_argument("--exams", type=Path, default=None)
    ap.add_argument("--emphasis", default="", choices=["", "subject", "course", "textbook"])
    ap.add_argument("--config", type=Path, default=None, help="local.toml")
    ap.add_argument("--dry-run", action="store_true", help="只做 probe，不继续")
    ap.add_argument("--resume", action="store_true", help="从 state.json 续跑")
    args = ap.parse_args(argv)

    cfg = load_cfg(args.config)
    root = Path.cwd()
    course = args.course or _derive_course(args.target)
    work = root / "courses" / course
    distill_dir = work / ".distill"
    dist_dir = work / "dist"
    state_file = dist_dir / "_meta" / "state.json"

    print(f"目标：{args.target}")
    print(f"目标类型：{args.goal}")
    print(f"课程：{course}")
    print(f"工作区：{work}")

    # Stage 0：素材定位与分P盘点
    target = _resolve_target(args.target)
    if target is None:
        print(f"\n✗ 找不到素材：{args.target}")
        return 1

    video: Path | None = None
    srt: Path | None = None
    pages_info: dict = {}

    if isinstance(target, Path):
        video = target
        srt = _find_srt(video)
        print(f"本地视频：{video}")
        print(f"字幕：{srt if srt else '无（需 ASR）'}")
    else:
        # B 站：列分P
        print(f"\n[Stage 0b 列分P]")
        pages_json = distill_dir / "pages.json"
        distill_dir.mkdir(parents=True, exist_ok=True)
        rc = run([py(), str(SCRIPTS / "fetch.py"), "list", str(target),
                  "-o", str(pages_json)], "列分P")
        if rc != 0:
            return rc
        pages_info = json.loads(pages_json.read_text(encoding="utf-8"))
        pages = _select_pages(pages_info, args.pages)
        if not pages:
            print("\n✗ 没有可蒸馏的分P")
            return 1
        print(f"\n选定 {len(pages)} 个分P：{[p['page'] for p in pages]}")

        if args.dry_run:
            print("\n（dry-run，停在分P确认）")
            return 0

        # 下载第一个分P做 probe
        first = pages[0]
        raw_dir = distill_dir / "raw"
        print(f"\n[Stage 3a 下载 p{first['page']}]")
        rc = run([py(), str(SCRIPTS / "fetch.py"), "video", str(target),
                  "-p", str(first["page"]), "-d", str(raw_dir),
                  "-q", str(cfg_get(cfg, "bilibili", "quality_1080p", 80)),
                  *(["--auth-file", str(cfg_get(cfg, "bilibili", "auth_file"))]
                    if cfg_get(cfg, "bilibili", "auth_file") else []),
                  *(["--yutto", str(cfg_get(cfg, "tools", "yutto"))]
                    if cfg_get(cfg, "tools", "yutto") else []),
                  *(["--ffmpeg", str(cfg_get(cfg, "tools", "ffmpeg"))]
                    if cfg_get(cfg, "tools", "ffmpeg") else [])],
                 f"下载 p{first['page']}")
        if rc != 0:
            return rc
        rc = run([py(), str(SCRIPTS / "fetch.py"), "subs", str(target),
                  "-p", str(first["page"]), "-d", str(raw_dir),
                  *(["--auth-file", str(cfg_get(cfg, "bilibili", "auth_file"))]
                    if cfg_get(cfg, "bilibili", "auth_file") else []),
                  *(["--yutto", str(cfg_get(cfg, "tools", "yutto"))]
                    if cfg_get(cfg, "tools", "yutto") else []),
                  *(["--ffmpeg", str(cfg_get(cfg, "tools", "ffmpeg"))]
                    if cfg_get(cfg, "tools", "ffmpeg") else [])],
                 f"取 p{first['page']} 字幕")

        vids = sorted(raw_dir.glob("*.mp4"))
        srts = sorted(raw_dir.glob("*.srt"))
        video = vids[0] if vids else None
        srt = srts[0] if srts else None
        if video is None:
            print("\n✗ 下载后找不到视频文件")
            return 1
        print(f"\n本地视频：{video.name}")
        print(f"字幕：{srt.name if srt else '无'}")

    # 初始化状态
    lecture_ids = ([f"lecture-{p['page']:02d}" for p in pages_info.get("keep", [])]
                   if pages_info else ["lecture-01"])
    if args.resume and state_file.is_file():
        print(f"\n续跑：读 {state_file}")
    else:
        run([py(), str(SCRIPTS / "state.py"), "init", str(dist_dir),
             "--course", course, "--goal", args.goal,
             "--lectures", ",".join(lecture_ids)], "初始化状态")

    # Stage 1：probe
    probe_out = distill_dir / "probe"
    rc = run([py(), str(SCRIPTS / "probe.py"), str(video), "-o", str(probe_out),
              *(["--subtitle", str(srt)] if srt else []),
              *(["--pages-json", str(distill_dir / "pages.json")]
                if (distill_dir / "pages.json").is_file() else [])],
             "Stage 1 probe")
    if rc != 0:
        return rc
    run([py(), str(SCRIPTS / "state.py"), "set", str(dist_dir), "--stage", "1"],
        "更新状态")

    profile = json.loads((probe_out / "material-profile.json").read_text(encoding="utf-8"))
    kind = profile["classify"]["kind"]
    dur = profile["media"]["duration"]

    # Stage 2：代价闸门
    total_dur = (pages_info.get("total_duration") or dur) if pages_info else dur
    est_tokens = total_dur / 3600 * 12000 * 1.3
    n_pages = len(pages_info.get("keep", [])) if pages_info else 1
    print(f"\n{'='*64}\n[Stage 2 代价评估]")
    print(f"  本讲时长 {dur/60:.1f} 分钟，判型 {kind}")
    print(f"  全部 {n_pages} 讲合计 {total_dur/60:.1f} 分钟")
    print(f"  预估 token ≈ {est_tokens:,.0f}")
    if total_dur > 5 * 3600:
        print("  ⚠ 超过 5 小时，建议分层：先跑 2-3 讲看质量")

    if args.dry_run:
        print("\n（dry-run，停在 Stage 2）")
        return 0

    run([py(), str(SCRIPTS / "state.py"), "set", str(dist_dir), "--stage", "2"],
        "更新状态")

    # Stage 5-6：抽帧 OCR → 对齐
    anchors = dist_dir / "_meta" / "anchors.json"
    anchors.parent.mkdir(parents=True, exist_ok=True)

    if kind in ("slides-lecture", "document"):
        frames_out = distill_dir / "frames"
        rc = run([py(), str(SCRIPTS / "frames.py"), str(video),
                  "-o", str(frames_out), "--ocr",
                  *(["--sections", args.sections] if args.sections else []),
                  *(["--subtitle", str(srt)] if srt else [])],
                 "Stage 5 抽帧 + OCR")
        if rc != 0:
            return rc

        # 复制 chosen 帧供 OCR 调度
        sel_dir = distill_dir / "ocr_sel"
        sel_dir.mkdir(parents=True, exist_ok=True)
        idx = json.loads((frames_out / "index.json").read_text(encoding="utf-8"))
        for s in idx["segments"]:
            src = frames_out / "frames" / s["chosen"]["file"]
            if src.exists():
                shutil.copy(src, sel_dir / s["chosen"]["file"])

        ocr_args = [py(), str(SCRIPTS / "ocr.py"), str(sel_dir),
                    "-o", str(distill_dir / "ocr.json"), "--auto-vlm"]
        if cfg_get(cfg, "ocr", "llama_bin"):
            ocr_args += ["--llama-bin", str(cfg_get(cfg, "ocr", "llama_bin"))]
        if cfg_get(cfg, "models", "qwen_vl_model"):
            ocr_args += ["--model", str(cfg_get(cfg, "models", "qwen_vl_model"))]
        if cfg_get(cfg, "models", "qwen_vl_mmproj"):
            ocr_args += ["--mmproj", str(cfg_get(cfg, "models", "qwen_vl_mmproj"))]
        rc = run(ocr_args, "Stage 5b OCR 手写判定")
        if rc != 0:
            return rc

        rc = run([py(), str(SCRIPTS / "align.py"),
                  str(frames_out / "index.json"),
                  "--subtitle", str(srt), "--ocr", str(distill_dir / "ocr.json"),
                  "-o", str(anchors), "--course", course, "--lecture", "lecture-01"],
                 "Stage 6 三向对齐")
        if rc != 0:
            return rc
    else:
        print(f"\n⚠ 判型为 {kind}，画面无信息量，跳过抽帧与 OCR")
        print("  需要 info-stream 模式的 transcript 管线（尚未实现）")

    run([py(), str(SCRIPTS / "state.py"), "set", str(dist_dir), "--stage", "6"],
        "更新状态")

    # Stage 7：写作（外部代理）
    print(f"\n{'='*64}\n[Stage 7 写作]")
    print(f"  锚点已就绪：{anchors}")
    print("  → 派子代理写六栏讲义：references/contracts/chapter_writer.md")
    print("  → 多讲次用智能体团队：references/agent-teams.md")
    print(f"  → 写完用 state.py 标记：--lecture lecture-01 --status done")

    # Stage 8-9：装配 + 校验（讲义存在才跑）
    lectures = sorted(dist_dir.glob("lecture-*.md"))
    if lectures:
        rc = run([py(), str(SCRIPTS / "build_doc.py"), str(dist_dir),
                  "-o", str(dist_dir), "--course", course], "Stage 8 装配")
        if rc != 0:
            return rc

        rc = run([py(), str(SCRIPTS / "validate.py"), str(anchors),
                  "--media", str(video), "--lecture", str(lectures[0]),
                  "-o", str(dist_dir / "_meta" / "validation.json")],
                 "Stage 9 校验")
        if rc != 0:
            print("\n✗ 校验未通过，见 validation.json")
            return rc
        run([py(), str(SCRIPTS / "state.py"), "set", str(dist_dir), "--stage", "9"],
            "更新状态")
    else:
        print(f"\n[Stage 8-9] 还没有讲义（{dist_dir}/lecture-*.md），跳过装配与校验")

    run([py(), str(SCRIPTS / "state.py"), "show", str(dist_dir)], "状态")

    print(f"\n{'='*64}\n完成")
    print(f"产物：{dist_dir}")
    return 0


def _derive_course(target: str) -> str:
    import re
    if "BV" in target.upper():
        m = re.search(r"(BV[0-9A-Za-z]+)", target)
        return (m.group(1).lower() if m else "course-1")
    p = Path(target)
    name = p.stem if p.suffix else p.name
    name = re.sub(r"[^\w\u4e00-\u9fff]+", "-", name).strip("-").lower()
    return name[:40] or "course-1"


def _resolve_target(target: str):
    p = Path(target)
    if p.is_file():
        return p
    if "bilibili" in target or "BV" in target.upper():
        return target
    return None


def _find_srt(video: Path) -> Path | None:
    cands = list(video.parent.glob("*.srt"))
    return cands[0] if cands else None


def _select_pages(pages_info: dict, spec: str) -> list[dict]:
    """按 --pages 选择分P。空则用 keep 列表。"""
    keep = pages_info.get("keep") or []
    if not spec:
        return keep

    wanted = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            wanted.update(range(int(a), int(b) + 1))
        elif part:
            wanted.add(int(part))
    return [p for p in keep if p["page"] in wanted]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
