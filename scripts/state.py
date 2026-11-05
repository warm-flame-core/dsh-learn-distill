#!/usr/bin/env python3
"""状态持久化：断点续跑、会话中断恢复。

状态文件：<course>/_meta/state.json

用法：
    python scripts/state.py show <course_dir>
    python scripts/state.py init <course_dir> --course <id> --goal <goal>
    python scripts/state.py set <course_dir> --stage 5 --lecture 01 --status done
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

STAGES = {
    0: "参数解析",
    1: "probe 判型",
    2: "代价闸门",
    3: "取素材",
    4: "术语纠错",
    5: "抽帧 OCR",
    6: "对齐",
    7: "写作",
    8: "装配",
    9: "校验",
    10: "盲测",
    11: "完成",
}

LECTURE_STATUS = ("pending", "in_progress", "done", "failed", "skipped")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def state_path(course_dir: Path) -> Path:
    return course_dir / "_meta" / "state.json"


def load(course_dir: Path) -> dict:
    p = state_path(course_dir)
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save(course_dir: Path, state: dict) -> None:
    state["updated"] = now_iso()
    p = state_path(course_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def init_state(course_dir: Path, course: str, goal: str,
               lectures: list[dict] | None = None) -> dict:
    state = {
        "course": course,
        "goal": goal,
        "stage": 0,
        "stage_name": STAGES[0],
        "lectures": lectures or [],
        "jobs": [],
        "created": now_iso(),
        "updated": now_iso(),
    }
    save(course_dir, state)
    return state


def advance(course_dir: Path, stage: int) -> dict:
    state = load(course_dir)
    state["stage"] = stage
    state["stage_name"] = STAGES.get(stage, f"unknown({stage})")
    save(course_dir, state)
    return state


def set_lecture(course_dir: Path, lecture_id: str, status: str,
                owner: str = "", note: str = "") -> dict:
    state = load(course_dir)
    lectures = state.setdefault("lectures", [])
    found = None
    for lc in lectures:
        if lc.get("id") == lecture_id:
            found = lc
            break
    if found is None:
        found = {"id": lecture_id}
        lectures.append(found)
    found["status"] = status
    if owner:
        found["owner"] = owner
    if note:
        found["note"] = note
    found["updated"] = now_iso()
    save(course_dir, state)
    return state


def add_job(course_dir: Path, job_id: str, kind: str, status: str = "running") -> dict:
    state = load(course_dir)
    jobs = state.setdefault("jobs", [])
    jobs.append({"id": job_id, "kind": kind, "status": status,
                 "started": now_iso()})
    save(course_dir, state)
    return state


def set_job(course_dir: Path, job_id: str, status: str) -> dict:
    state = load(course_dir)
    for j in state.get("jobs", []):
        if j.get("id") == job_id:
            j["status"] = status
            j["finished"] = now_iso()
            break
    save(course_dir, state)
    return state


def next_step(state: dict) -> str:
    """根据状态推断下一步该做什么。"""
    stage = state.get("stage", 0)
    lectures = state.get("lectures", [])
    pending = [lc for lc in lectures if lc.get("status") == "pending"]
    inprog = [lc for lc in lectures if lc.get("status") == "in_progress"]

    if stage >= 11:
        return "已完成"
    if inprog:
        return f"等待讲次 {inprog[0]['id']} 完成（owner={inprog[0].get('owner','?')}）"
    if stage == 7 and pending:
        return f"派发写作任务：{pending[0]['id']}（还有 {len(pending)} 个待写）"
    if stage == 7 and not pending:
        return "写作完成，进入装配"
    return f"Stage {stage} {STAGES.get(stage,'')}"


def cmd_show(args) -> int:
    state = load(args.course_dir)
    if not state:
        print(f"没有状态文件：{state_path(args.course_dir)}")
        return 1

    print(f"课程：{state.get('course')} | 目标：{state.get('goal')}")
    print(f"阶段：{state.get('stage')} {state.get('stage_name')}")
    print(f"更新：{state.get('updated')}")
    print()

    lectures = state.get("lectures", [])
    if lectures:
        print("讲次：")
        for lc in lectures:
            mark = {"done": "✓", "in_progress": "…", "pending": "○",
                    "failed": "✗", "skipped": "-"}.get(lc.get("status"), "?")
            owner = f" ({lc['owner']})" if lc.get("owner") else ""
            print(f"  {mark} {lc['id']} {lc.get('status')}{owner}")
        print()

    jobs = state.get("jobs", [])
    if jobs:
        print("后台任务：")
        for j in jobs:
            print(f"  {j['id']} [{j['kind']}] {j['status']}")
        print()

    print(f"下一步：{next_step(state)}")
    return 0


def cmd_init(args) -> int:
    lectures = []
    if args.lectures:
        for lid in args.lectures.split(","):
            lectures.append({"id": lid.strip(), "status": "pending"})
    state = init_state(args.course_dir, args.course, args.goal, lectures)
    print(f"初始化：{state_path(args.course_dir)}")
    print(f"  课程 {args.course} | 目标 {args.goal} | 讲次 {len(lectures)}")
    return 0


def cmd_set(args) -> int:
    if args.stage is not None:
        advance(args.course_dir, args.stage)
        print(f"阶段 → {args.stage} {STAGES.get(args.stage,'')}")
    if args.lecture:
        set_lecture(args.course_dir, args.lecture, args.status,
                    owner=args.owner or "", note=args.note or "")
        print(f"讲次 {args.lecture} → {args.status}")
    if not args.stage and not args.lecture:
        print("没有要更新的内容", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="状态持久化")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_show = sub.add_parser("show", help="显示状态与下一步")
    p_show.add_argument("course_dir", type=Path)
    p_show.set_defaults(func=cmd_show)

    p_init = sub.add_parser("init", help="初始化状态")
    p_init.add_argument("course_dir", type=Path)
    p_init.add_argument("--course", required=True)
    p_init.add_argument("--goal", required=True, choices=["deep_mastery", "exam_cram"])
    p_init.add_argument("--lectures", default="", help="逗号分隔，如 lecture-01,lecture-02")
    p_init.set_defaults(func=cmd_init)

    p_set = sub.add_parser("set", help="更新状态")
    p_set.add_argument("course_dir", type=Path)
    p_set.add_argument("--stage", type=int, default=None)
    p_set.add_argument("--lecture", default="")
    p_set.add_argument("--status", default="", choices=["", *LECTURE_STATUS])
    p_set.add_argument("--owner", default="")
    p_set.add_argument("--note", default="")
    p_set.set_defaults(func=cmd_set)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
