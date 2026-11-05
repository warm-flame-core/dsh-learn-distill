#!/usr/bin/env python3
"""规范自检：引用有效性、禁写内容、可分发性。

用法：
    python scripts/check_skill.py [--root <skill根>]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 禁写内容（规范里不应出现）
FORBIDDEN_PATTERNS = [
    (r"20\d{2}-\d{2}-\d{2}", "时间表述（日期）"),
    (r"20\d{2}\s*年", "时间表述（年份）"),
    (r"实测[^。\n]{0,20}", "实测叙述"),
    (r"曾经[^。\n]{0,20}", "历史决定"),
    (r"之前[^。\n]{0,20}后来", "演变过程"),
    (r"特化自", "出处叙述"),
    (r"借鉴[^。\n]{0,20}的", "出处叙述"),
    (r"踩过[^。\n]{0,10}", "实测叙述"),
    (r"[FDC]:\\\\", "本机绝对路径"),
]

# 允许出现的例外（如 LICENSES/NOTICE.md 需要写来源）
EXEMPT_FILES = {"NOTICE.md", "LICENSES"}
# 用户自建、不入 git 的文件：文档引用它们是正确的，缺文件不是错误
EXPECTED_ABSENT = {"config/local.toml"}


def check_refs(root: Path) -> list[str]:
    """检查 md 里的文件引用是否存在（带反引号与裸路径都查）。"""
    issues = []
    pattern = re.compile(
        r"`?(references/[^\s`\)]+\.md|scripts/[^\s`\)]+\.(?:py|ps1)"
        r"|config/[^\s`\)]+\.(?:toml|example|toml\.example))`?")
    for md in root.rglob("*.md"):
        if any(e in str(md) for e in EXEMPT_FILES):
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        for m in pattern.finditer(text):
            ref = m.group(1).rstrip(".,;:）)」")
            if ref in EXPECTED_ABSENT:
                continue
            target = root / ref
            if not target.exists():
                line_no = text[:m.start()].count("\n") + 1
                issues.append(f"{md.relative_to(root)}:{line_no} 引用不存在 {ref}")
    return issues


def check_forbidden(root: Path) -> list[str]:
    """检查禁写内容。"""
    issues = []
    for md in root.rglob("*.md"):
        rel = str(md.relative_to(root))
        if any(e in rel for e in EXEMPT_FILES):
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        for pattern, label in FORBIDDEN_PATTERNS:
            for m in re.finditer(pattern, text):
                line_no = text[:m.start()].count("\n") + 1
                snippet = text[max(0, m.start() - 20):m.end() + 20].replace("\n", " ")
                issues.append(f"{rel}:{line_no} {label}「{snippet.strip()}」")
    return issues


def check_portability(root: Path) -> list[str]:
    """检查可分发性：脚本里不应有本机绝对路径。"""
    issues = []
    for py in root.rglob("*.py"):
        rel = str(py.relative_to(root))
        text = py.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'["\']([A-Z]:\\\\[^"\']+)["\']', text):
            path = m.group(1)
            line_no = text[:m.start()].count("\n") + 1
            # media_util 的 COMMON_FFMPEG 是兜底，允许
            if "media_util" in rel and "COMMON_FFMPEG" in text[:m.end()]:
                continue
            issues.append(f"{rel}:{line_no} 硬编码路径「{path}」")
    return issues


def check_frontmatter(root: Path) -> list[str]:
    """检查 SKILL.md frontmatter。"""
    issues = []
    skill = root / "SKILL.md"
    if not skill.is_file():
        return ["SKILL.md 不存在"]
    text = skill.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    if not m:
        return ["SKILL.md 缺 frontmatter"]
    fm = m.group(1)
    if not re.search(r"^name:\s*\S+", fm, re.M):
        issues.append("frontmatter 缺 name")
    else:
        name_m = re.search(r"^name:\s*(\S+)", fm, re.M)
        if name_m and not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", name_m.group(1)):
            issues.append(f"name 不是 kebab-case：{name_m.group(1)}")
    if not re.search(r"^description:\s*\S+", fm, re.M):
        issues.append("frontmatter 缺 description")
    # DSH 不支持的字段
    for bad in ("allowed-tools", "license:"):
        if re.search(rf"^{re.escape(bad)}", fm, re.M):
            issues.append(f"frontmatter 含 DSH 不支持的字段：{bad}")
    return issues


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="规范自检")
    ap.add_argument("--root", type=Path, default=None)
    args = ap.parse_args(argv)

    root = args.root or Path(__file__).resolve().parent.parent

    groups = [
        ("引用有效性", check_refs(root)),
        ("禁写内容", check_forbidden(root)),
        ("可分发性", check_portability(root)),
        ("frontmatter", check_frontmatter(root)),
    ]

    total = 0
    for label, issues in groups:
        print(f"\n=== {label} ===")
        if not issues:
            print("  ✓ 通过")
        else:
            for i in issues:
                print(f"  ✗ {i}")
            total += len(issues)

    print(f"\n{'='*50}")
    print(f"结果：{total} 个问题")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
