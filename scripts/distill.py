#!/usr/bin/env python3
"""learn-distill 主入口：编排完整蒸馏流程。

用法：
    python scripts/distill.py <target> --goal deep_mastery|exam_cram [options]

阶段：
  0 参数解析 → 1 probe → 2 代价闸门 → 3 取素材 → 4 术语纠错
  → 5 抽帧OCR → 6 对齐 → 7 写作（外部代理）→ 8 装配 → 9 校验
"""

from __future__ import annotations

import argparse
import json
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


def run(cmd: list[str], desc: str) -> int:
    print(f"\n{'='*60}\n[{desc}]")
    print("  " + " ".join(str(c) for c in cmd[:6]) + (" ..." if len(cmd) > 6 else ""))
    proc = subprocess.run(cmd)
    return proc.returncode


def python_exe() -> str:
    return sys.executable


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="网课蒸馏：deep_mastery（自足讲义+PLAYBOOK）/ exam_cram（Anki 卡片）")
    ap.add_argument("target", help="BV号 / B站URL / 本地视频路径")
    ap.add_argument("--goal", required=True, choices=["deep_mastery", "exam_cram"],
                    help="学习目标（必填，无默认值）")
    ap.add_argument("--course", default="", help="课程 id（kebab-case），不填则自动生成")
    ap.add_argument("--sections", default="", help="只跑片段，如 00:00:00-00:05:00")
    ap.add_argument("--textbook", type=Path, default=None)
    ap.add_argument("--slides", type=Path, default=None)
    ap.add_argument("--exams", type=Path, default=None)
    ap.add_argument("--emphasis", default="", choices=["", "subject", "course", "textbook"])
    ap.add_argument("--config", type=Path, default=None, help="local.toml")
    ap.add_argument("--dry-run", action="store_true", help="只做 probe，不继续")
    args = ap.parse_args(argv)

    root = Path.cwd()
    course = args.course or _derive_course(args.target)
    work = root / "courses" / course
    distill_dir = work / ".distill"
    dist_dir = work / "dist"

    print(f"目标：{args.target}")
    print(f"目标类型：{args.goal}")
    print(f"课程：{course}")
    print(f"工作区：{work}")

    # Stage 0：素材定位
    target = _resolve_target(args.target)
    if target is None:
        print(f"\n✗ 找不到素材：{args.target}")
        print("  需要 BV 号/链接（用 yutto 下载）或本地视频路径")
        return 1

    if isinstance(target, Path):
        video = target
        srt = _find_srt(video)
    else:
        print(f"\n素材是 B 站链接，需先下载：yutto {target} -d {distill_dir / 'raw'}")
        return 1

    print(f"本地视频：{video}")
    print(f"字幕：{srt if srt else '无（需 ASR）'}")

    # Stage 1：probe
    probe_out = distill_dir / "probe"
    rc = run([python_exe(), str(SCRIPTS / "probe.py"), str(video),
              "-o", str(probe_out),
              *(["--subtitle", str(srt)] if srt else []),
              *(["--ffmpeg", _cfg_tool(args.config, "ffmpeg")] if _cfg_tool(args.config, "ffmpeg") else [])],
             "Stage 1 probe")
    if rc != 0:
        return rc

    profile = json.loads((probe_out / "material-profile.json").read_text(encoding="utf-8"))
    kind = profile["classify"]["kind"]
    dur = profile["media"]["duration"]

    # Stage 2：代价闸门
    est_tokens = dur / 3600 * 12000 * 1.3
    print(f"\n{'='*60}\n[Stage 2 代价评估]")
    print(f"  时长 {dur/60:.1f} 分钟，判型 {kind}")
    print(f"  预估 token ≈ {est_tokens:,.0f}")
    if dur > 5 * 3600:
        print("  ⚠ 超过 5 小时，建议分层：先跑 2-3 讲看质量")
    print("  → 请用户确认范围与代价后再继续（当前为 --dry-run 以外的自动继续）")

    if args.dry_run:
        print("\n（dry-run，停在 Stage 2）")
        return 0

    if kind == "info-stream":
        print("\n⚠ 判型为信息流，画面无信息量，跳过抽帧与 OCR")

    # Stage 3-6：字幕纠错 → 抽帧OCR → 对齐
    frames_out = distill_dir / "frames"
    if kind in ("slides-lecture", "document"):
        rc = run([python_exe(), str(SCRIPTS / "frames.py"), str(video),
                  "-o", str(frames_out), "--ocr",
                  *(["--sections", args.sections] if args.sections else []),
                  *(["--subtitle", str(srt)] if srt else [])],
                 "Stage 5 抽帧 + OCR")
        if rc != 0:
            return rc

        # OCR 候选帧（用 chosen 帧）
        sel_dir = distill_dir / "ocr_sel"
        sel_dir.mkdir(parents=True, exist_ok=True)
        idx = json.loads((frames_out / "index.json").read_text(encoding="utf-8"))
        import shutil
        for s in idx["segments"]:
            src = frames_out / "frames" / s["chosen"]["file"]
            if src.exists():
                shutil.copy(src, sel_dir / s["chosen"]["file"])

        ocr_out = distill_dir / "ocr.json"
        rc = run([python_exe(), str(SCRIPTS / "ocr.py"), str(sel_dir),
                  "-o", str(ocr_out)] + _ocr_args(args.config), "Stage 5b OCR 手写判定")
        if rc != 0:
            return rc

        # 对齐
        anchors = dist_dir / "_meta" / "anchors.json"
        anchors.parent.mkdir(parents=True, exist_ok=True)
        rc = run([python_exe(), str(SCRIPTS / "align.py"),
                  str(frames_out / "index.json"),
                  "--subtitle", str(srt), "--ocr", str(ocr_out),
                  "-o", str(anchors), "--course", course, "--lecture", "lecture-01"],
                 "Stage 6 三向对齐")
        if rc != 0:
            return rc
    else:
        anchors = dist_dir / "_meta" / "anchors.json"
        anchors.parent.mkdir(parents=True, exist_ok=True)
        run([python_exe(), str(SCRIPTS / "align.py"),
             str(probe_out / "material-profile.json"),
             "--subtitle", str(srt), "-o", str(anchors),
             "--course", course, "--lecture", "lecture-01"],
            "Stage 6 对齐（信息流模式）")

    # Stage 7：写作（外部代理，这里只提示）
    print(f"\n{'='*60}\n[Stage 7 写作]")
    print(f"  锚点已就绪：{anchors}")
    print("  → 派子代理写六栏讲义：references/contracts/chapter_writer.md")
    print("  → 多讲次（≥5）用智能体团队：references/agent-teams.md")

    # Stage 8-9：装配 + 校验（讲义存在才跑）
    lectures = sorted(dist_dir.glob("lecture-*.md"))
    if lectures:
        rc = run([python_exe(), str(SCRIPTS / "build_doc.py"), str(dist_dir),
                  "-o", str(dist_dir), "--course", course], "Stage 8 装配")
        if rc != 0:
            return rc

        rc = run([python_exe(), str(SCRIPTS / "validate.py"), str(anchors),
                  "--media", str(video), "--lecture", str(lectures[0]),
                  "-o", str(dist_dir / "_meta" / "validation.json")],
                 "Stage 9 校验")
        if rc != 0:
            print("\n✗ 校验未通过，见 validation.json")
            return rc
    else:
        print(f"\n[Stage 8-9] 还没有讲义（{dist_dir}/lecture-*.md），跳过装配与校验")

    print(f"\n{'='*60}\n完成")
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


def _cfg_tool(config: Path | None, key: str) -> str:
    if not config or not config.is_file():
        return ""
    try:
        import tomllib
        with config.open("rb") as fh:
            cfg = tomllib.load(fh)
        return (cfg.get("tools") or {}).get(key) or ""
    except Exception:
        return ""


def _ocr_args(config: Path | None) -> list[str]:
    if not config or not config.is_file():
        return []
    try:
        import tomllib
        with config.open("rb") as fh:
            cfg = tomllib.load(fh)
        ocr = cfg.get("ocr") or {}
        models = cfg.get("models") or {}
        out = ["--config", str(config)]
        if ocr.get("llama_bin"):
            out += ["--llama-bin", str(ocr["llama_bin"])]
        return out
    except Exception:
        return []


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
