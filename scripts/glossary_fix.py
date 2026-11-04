#!/usr/bin/env python3
"""术语纠错引擎：把 ASR 字幕里的系统性错字改回正确术语。

规则：
1. longest-first：长词先匹配（`主大学元素` 先于 `主大学`）。
2. context 保护：带 context 的规则只在该上下文附近生效，
   避免 `成绩`→`乘积` 把"学习成绩"改成"学习乘积"。
3. 单字键默认拒绝：需显式 `allow_single` + `context` 才启用。
4. 命中计数：某规则命中 >200 次则告警，提示规则可能过宽。
5. 可审计：每次替换记录 (from, to, pos, context) 到 corrections.json。

词库格式（assets/glossary/*.json）：
{
  "rules": [
    {"from": "转型", "to": "转置", "context": "行列式|矩阵"},
    {"from": "主大学元素", "to": "主对角线元素"},
    {"from": "缪", "to": "μ", "allow_single": true, "context": "摩擦系数|磁导率"}
  ],
  "definitions": {"术语": "释义"}     // 供文档生成用，不参与替换
}
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Windows 控制台默认 GBK，输出中文/符号会炸。统一切 UTF-8，失败则降级为 ASCII 安全模式。
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

GLOSSARY_DIR = Path(__file__).resolve().parent.parent / "assets" / "glossary"
SINGLE_CHAR_MIN = 2          # 单字键默认拒绝（误伤风险高）
OVERHIT_WARN = 200           # 单条规则命中超过此值 → 警告可能过宽
CONTEXT_WINDOW = 40          # context 检查的字符窗口半径


@dataclass
class Rule:
    frm: str
    to: str
    context: re.Pattern[str] | None
    why: str = ""
    subject: str = ""
    hits: int = 0
    applied: int = 0


@dataclass
class Correction:
    frm: str
    to: str
    pos: int
    context: str
    subject: str


@dataclass
class Result:
    text: str
    corrections: list[Correction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "corrections": [
                {"from": c.frm, "to": c.to, "pos": c.pos,
                 "context": c.context, "subject": c.subject}
                for c in self.corrections
            ],
            "warnings": self.warnings,
            "stats": {"total": len(self.corrections)},
        }


def load_rules(
    subjects: list[str] | None = None,
    glossary_dir: Path = GLOSSARY_DIR,
    extra_files: list[Path] | None = None,
) -> tuple[list[Rule], dict[str, str], list[str]]:
    """载入词库。subjects 为空则载入全部；返回 (规则, 释义, 警告)。"""
    warnings: list[str] = []
    files: list[Path] = []

    if subjects:
        for s in subjects:
            p = glossary_dir / f"{s}.json"
            if p.exists():
                files.append(p)
            else:
                warnings.append(f"词库不存在：{p}")
    else:
        files = sorted(glossary_dir.glob("*.json"))

    # 用户自定义词库优先级最高（后加载 → 覆盖）
    if extra_files:
        files.extend(extra_files)

    rules: list[Rule] = []
    definitions: dict[str, str] = {}
    seen: dict[str, Rule] = {}

    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            warnings.append(f"{path.name} 解析失败：{exc}")
            continue

        subject = (data.get("_meta") or {}).get("subject") or path.stem
        definitions.update(data.get("definitions") or {})

        for raw in data.get("rules") or []:
            frm = (raw.get("from") or "").strip()
            to = (raw.get("to") or "").strip()
            if not frm or not to:
                continue
            ctx = raw.get("context")
            # 单字规则默认拒绝，除非显式声明 "allow_single": true 且带 context
            if len(frm) < SINGLE_CHAR_MIN:
                if not (raw.get("allow_single") and ctx):
                    warnings.append(
                        f"跳过单字规则 {frm!r}（{path.name}）："
                        f"单字替换误伤风险过高（如需启用请加 allow_single:true + context）")
                    continue
            rule = Rule(
                frm=frm, to=to,
                context=re.compile(ctx) if ctx else None,
                why=raw.get("why", ""),
                subject=subject,
            )
            # 同一 from 以先加载者为准（内置在前，用户在后时覆盖）
            if frm in seen:
                seen[frm].to = to
                seen[frm].context = rule.context
            else:
                seen[frm] = rule
                rules.append(rule)

    # longest-first：同一次扫描中长词优先
    rules.sort(key=lambda r: -len(r.frm))
    return rules, definitions, warnings


PROTECT_CHAR = "\x00"


def _unprotect(chunk: str, protected: list[str]) -> str:
    """把占位符还原成真值，用于日志/上下文展示。"""
    return re.sub(
        f"{PROTECT_CHAR}(\\d+){PROTECT_CHAR}",
        lambda m: protected[int(m.group(1))],
        chunk,
    )


def _context_ok(text: str, start: int, end: int, rule: Rule) -> bool:
    """context 判定。规则无 context 时直接允许；有 context 时需在窗口内命中。"""
    if rule.context is None:
        return True
    lo = max(0, start - CONTEXT_WINDOW)
    hi = min(len(text), end + CONTEXT_WINDOW)
    return rule.context.search(text[lo:hi]) is not None


def correct(text: str, rules: list[Rule]) -> Result:
    """执行替换。按 longest-first 单遍扫描，替换后的文本继续参与后续规则。

    实现方式：逐个规则做 re.sub，但用占位符保护已替换内容，
    避免 A→B 之后 B 又被 C→D 规则二次改写（链式污染）。
    """
    result = Result(text=text)
    # 用不可见分隔符保护已替换片段
    PROTECT = "\x00"

    working = text
    protected: list[str] = []

    for rule in rules:
        pattern = re.compile(re.escape(rule.frm))
        out_parts: list[str] = []
        last = 0
        for m in pattern.finditer(working):
            # 跳过落在保护区内的匹配
            if PROTECT in working[m.start():m.end()]:
                continue
            if not _context_ok(working, m.start(), m.end(), rule):
                continue
            rule.hits += 1
            # context 取自 working，其中已替换片段是占位符，需还原成真值再记录。
            ctx = _unprotect(working[max(0, m.start() - 15):m.end() + 15], protected)
            ctx = ctx.replace("\n", " ")
            result.corrections.append(
                Correction(rule.frm, rule.to, m.start(), ctx, rule.subject))
            out_parts.append(working[last:m.start()])
            # 用占位符代替，记下真值
            idx = len(protected)
            protected.append(rule.to)
            out_parts.append(f"{PROTECT}{idx}{PROTECT}")
            last = m.end()
            rule.applied += 1
        if out_parts:
            out_parts.append(working[last:])
            working = "".join(out_parts)

    # 还原占位符
    working = re.sub(
        f"{PROTECT}(\\d+){PROTECT}",
        lambda m: protected[int(m.group(1))],
        working,
    )
    result.text = working

    for rule in rules:
        if rule.hits > OVERHIT_WARN:
            result.warnings.append(
                f"规则 {rule.frm!r}→{rule.to!r} 命中 {rule.hits} 次，"
                f"可能过宽，建议复核（{rule.why or '无备注'}）")
    return result


def apply_to_srt(srt_path: Path, rules: list[Rule]) -> tuple[Result, dict]:
    """对 SRT 做纠正，返回 (汇总结果, 明细)。

    把全部字幕文本拼成一段再做替换，而不是逐条字幕独立替换：
    context 词常落在相邻句，逐条处理会让 context 规则失效。

    只改文本行，不动序号与时间轴——时间轴是锚点的唯一真相源。
    """
    raw = srt_path.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\r?\n\r?\n", raw.strip())

    # 第一遍：解析结构，收集每条字幕的文本行位置
    parsed: list[tuple[list[str], list[str]]] = []   # (head行, 文本行)
    for block in blocks:
        lines = block.splitlines()
        if not lines:
            continue
        head, text_lines = [], []
        for line in lines:
            (head if ("-->" in line or line.strip().isdigit()) else text_lines).append(line)
        parsed.append((head, text_lines))

    # 第二遍：把文本拼成整段（保留换行以便还原），整体纠正
    all_text = "\n".join("\n".join(t) for _, t in parsed)
    total = correct(all_text, rules)

    # 第三遍：按换行还原
    out_lines = total.text.split("\n")
    cursor = 0
    out_blocks: list[str] = []
    for head, text_lines in parsed:
        n = len(text_lines)
        new_text = out_lines[cursor:cursor + n]
        cursor += n
        out_blocks.append("\n".join(head + new_text))

    # 每条字幕的纠正明细（按位置映射）
    per_block: dict[int, dict] = {}
    if total.corrections:
        per_block = {"note": "corrections 为全片级，pos 相对拼接后的整段文本"}

    return total, {"srt_out": "\n\n".join(out_blocks) + "\n", "blocks": per_block}


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="术语纠错：SRT → 纠正后的 SRT + 审计 JSON")
    ap.add_argument("srt", type=Path, help="输入 SRT")
    ap.add_argument("-o", "--out", type=Path, help="输出 SRT（默认 <stem>.corrected.srt）")
    ap.add_argument("--subjects", default="", help="逗号分隔，如 math,physics；空=全部")
    ap.add_argument("--extra", type=Path, action="append", default=[],
                    help="额外词库 JSON（用户自定义，可多次）")
    ap.add_argument("--audit", type=Path, help="审计 JSON 输出路径")
    ap.add_argument("--dry-run", action="store_true", help="只报告不写文件")
    args = ap.parse_args(argv)

    subjects = [s.strip() for s in args.subjects.split(",") if s.strip()]
    rules, definitions, warns = load_rules(subjects, extra_files=args.extra)
    if not rules:
        print("✗ 没有可用规则", file=sys.stderr)
        return 1

    total, detail = apply_to_srt(args.srt, rules)
    out = args.out or args.srt.with_suffix(".corrected.srt")
    audit = args.audit or args.srt.with_suffix(".corrections.json")

    print(f"词库：{len(rules)} 条规则，{len(definitions)} 条释义")
    print(f"命中：{len(total.corrections)} 处")
    for w in warns:
        print(f"⚠ {w}")
    for c in total.corrections[:20]:
        print(f"  {c.frm} → {c.to}  「{c.context.strip()}」")
    if len(total.corrections) > 20:
        print(f"  ... 其余 {len(total.corrections) - 20} 处见审计文件")

    if args.dry_run:
        print("（dry-run，未写文件）")
        return 0

    out.write_text(detail["srt_out"], encoding="utf-8")
    payload = {
        "source": str(args.srt),
        "output": str(out),
        "subjects": subjects or "all",
        "rules_count": len(rules),
        "definitions_count": len(definitions),
        **total.to_json(),
    }
    audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"→ {out}")
    print(f"→ {audit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
