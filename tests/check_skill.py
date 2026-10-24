#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技能自检：frontmatter、篇幅、相对链接、路径卫生。

用法：
  python tests/check_skill.py [技能目录 ...]
默认检查 skills/contract-redact。
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = [os.path.join(ROOT, "skills", "contract-redact")]

MAX_SKILL_LINES = 200
MAX_DESCRIPTION = 120
REQUIRED_KEYS = ("name", "description", "version", "author", "license", "platforms")


def parse_frontmatter(text: str):
    if not text.startswith("---"):
        raise ValueError("文件必须以 --- 开头（第 0 字节）")
    match = re.search(r"\n---\s*\n", text[3:])
    if not match:
        raise ValueError("frontmatter 没有闭合的 ---")
    block = text[3:match.start() + 3]
    data, current = {}, None
    for raw in block.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.startswith("  ") or line.startswith("- "):
            continue  # 嵌套/列表字段这里不解析，够用即可
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if not key:
            continue
        if value.startswith("[") and value.endswith("]"):
            data[key] = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        else:
            data[key] = value.strip('"').strip("'")
        current = key
    return data, text[match.start() + 3:]


def check_skill(skill_dir: str) -> list[str]:
    problems: list[str] = []
    path = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(path):
        return ["缺少 SKILL.md：%s" % path]

    text = open(path, encoding="utf-8").read()
    try:
        fm, body = parse_frontmatter(text)
    except ValueError as exc:
        return ["frontmatter 有问题：%s" % exc]

    for key in REQUIRED_KEYS:
        if key not in fm:
            problems.append("frontmatter 缺字段：%s" % key)

    name = fm.get("name", "")
    if name != os.path.basename(os.path.normpath(skill_dir)):
        problems.append("name（%s）与目录名不一致" % name)

    desc = fm.get("description", "")
    if len(desc) > MAX_DESCRIPTION:
        problems.append("description 超过 %d 字（现在 %d）" % (MAX_DESCRIPTION, len(desc)))
    if not desc.endswith("。"):
        problems.append("description 建议以句号结尾")

    lines = text.splitlines()
    if len(lines) > MAX_SKILL_LINES:
        problems.append("SKILL.md 有 %d 行，超过 %d 行（该拆到 references/）" % (len(lines), MAX_SKILL_LINES))

    # 相对链接必须在
    for target in re.findall(r"\]\(([^)#]+\.md)\)", body):
        if target.startswith("http"):
            continue
        if not os.path.isfile(os.path.join(skill_dir, target)):
            problems.append("链接指向的文件不存在：%s" % target)

    # 必须有 references/ 且非空（渐进式披露）
    ref_dir = os.path.join(skill_dir, "references")
    if not os.path.isdir(ref_dir) or not any(
        f.endswith(".md") for f in os.listdir(ref_dir)
    ):
        problems.append("缺少 references/ 目录或其中没有 .md（渐进式披露要求）")

    # 路径卫生：不许本机绝对路径
    for bad in ("/Users/", "/home/", "C:\\Users"):
        if bad in text:
            problems.append("出现本机绝对路径：%s" % bad)

    return problems


def main(argv):
    targets = argv[1:] or DEFAULT
    failed = 0
    for target in targets:
        problems = check_skill(os.path.abspath(target))
        label = os.path.basename(os.path.normpath(target))
        if problems:
            failed += 1
            print("✗ %s" % label)
            for problem in problems:
                print("   - %s" % problem)
        else:
            print("✓ %s：frontmatter 完整、篇幅达标、链接都能打开" % label)
    print("\n结果：%s" % ("全部通过" if not failed else "%d 个技能不合格" % failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
