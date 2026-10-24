#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把候选词并入脱敏词表。

候选文件由 redact.py 生成（*.candidates.json），里面是「规则认得、词表里没有」的
机构名和人名。人工确认后用这个脚本合并：只增不删，keep、aliases 和其他字段原样保留。

用法：
  # 先看会加什么，不写文件
  python tools/merge_candidates.py 采购合同_脱敏.candidates.json --config vocab.json --dry-run

  # 只并入机构和联系人
  python tools/merge_candidates.py 采购合同_脱敏.candidates.json --config vocab.json --only org,people

  # 全部并入
  python tools/merge_candidates.py 采购合同_脱敏.candidates.json --config vocab.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

CATEGORIES = {
    "org": "extra_orgs",
    "people": "extra_people",
    "customer": "extra_customers",
    "supplier": "extra_suppliers",
}

# 也接受中文分类，省得记英文键
CATEGORY_ALIASES = {
    "机构": "org",
    "公司": "org",
    "人名": "people",
    "人员": "people",
    "客户": "customer",
    "供应商": "supplier",
}


def parse_only(raw: str) -> list[str]:
    if not raw:
        return list(CATEGORIES)
    selected: list[str] = []
    for token in raw.replace("，", ",").split(","):
        token = token.strip()
        if not token:
            continue
        key = CATEGORY_ALIASES.get(token, token)
        if key not in CATEGORIES:
            raise SystemExit(
                "不认识的分类：%s（可选：%s）" % (token, ", ".join(CATEGORIES))
            )
        if key not in selected:
            selected.append(key)
    if not selected:
        raise SystemExit("--only 没有给出有效分类（可选：%s）" % ", ".join(CATEGORIES))
    return selected


def load_candidates(path: str) -> list[tuple[str, str]]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    items = data.get("candidates") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise SystemExit("候选文件格式不对：应为包含 candidates 数组的 JSON")
    out: list[tuple[str, str]] = []
    for index, item in enumerate(items, 1):
        if isinstance(item, str):
            text, category = item, "org"
        elif isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            category = str(item.get("category") or "").strip()
        else:
            raise SystemExit("候选文件第 %d 项格式不对" % index)
        if not text:
            continue
        if category not in CATEGORIES:
            raise SystemExit(
                "候选文件第 %d 项的分类「%s」不认识（可选：%s）"
                % (index, category, ", ".join(CATEGORIES))
            )
        out.append((category, text))
    return out


def load_config(path: str) -> dict:
    if not os.path.isfile(path):
        raise SystemExit(
            "词表文件不存在：%s（第一次用请先建一个，或复制 examples/config.example.json）" % path
        )
    try:
        with open(path, encoding="utf-8") as handle:
            config = json.load(handle)
    except json.JSONDecodeError as exc:
        raise SystemExit("词表文件不是合法 JSON：%s" % exc)
    if not isinstance(config, dict):
        raise SystemExit("词表文件顶层应为 JSON 对象")
    for key in CATEGORIES.values():
        if key in config and not isinstance(config[key], list):
            raise SystemExit("词表字段 %s 必须是字符串数组" % key)
    return config


def known_terms(config: dict) -> dict[str, str]:
    """已经收录的写法 → 所在字段，用来跳过重复项。"""
    known: dict[str, str] = {}
    for key in CATEGORIES.values():
        for value in config.get(key) or []:
            text = str(value).strip()
            if text:
                known.setdefault(text, key)
    for canonical, variants in (config.get("aliases") or {}).items():
        known.setdefault(str(canonical).strip(), "aliases")
        for variant in variants or []:
            text = str(variant).strip()
            if text:
                known.setdefault(text, "aliases")
    for value in config.get("keep") or []:
        text = str(value).strip()
        if text:
            known.setdefault(text, "keep")
    return known


def write_config(path: str, config: dict) -> None:
    """原子写入，并强制 600：词表里有客户名单。"""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    descriptor, tmp = tempfile.mkstemp(
        prefix=".vocab-", suffix=".json", dir=directory, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(path, 0o600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把候选词并入脱敏词表（只增不删）")
    parser.add_argument(
        "candidates",
        help="候选词 JSON（redact.py 生成的 *.candidates.json）",
    )
    parser.add_argument("--config", required=True, help="要合并进去的词表 JSON")
    parser.add_argument(
        "--only",
        default="",
        help="只并入这些分类，逗号分隔：org,people,customer,supplier",
    )
    parser.add_argument("--dry-run", action="store_true", help="只预览，不写文件")
    args = parser.parse_args(argv)

    candidates_path = os.path.abspath(args.candidates)
    config_path = os.path.abspath(args.config)
    if os.path.realpath(candidates_path) == os.path.realpath(config_path):
        raise SystemExit("候选文件和词表不能是同一个文件")

    selected = parse_only(args.only)
    if not os.path.isfile(candidates_path):
        raise SystemExit("找不到候选文件：%s" % candidates_path)

    items = load_candidates(candidates_path)
    config = load_config(config_path)
    known = known_terms(config)

    added: list[tuple[str, str]] = []
    skipped: list[tuple[str, str, str]] = []
    for category, text in items:
        if category not in selected:
            continue
        if text in known:
            skipped.append((category, text, known[text]))
            continue
        key = CATEGORIES[category]
        config.setdefault(key, [])
        config[key].append(text)
        known[text] = key
        added.append((category, text))

    print("候选文件：%s" % candidates_path)
    print("词表：%s" % config_path)
    print("筛选分类：%s" % "、".join(selected))
    print("将并入 %d 条，跳过 %d 条" % (len(added), len(skipped)))
    for category, text in added:
        print("  + %s → %s" % (text, CATEGORIES[category]))
    for _category, text, where in skipped:
        print("  = %s（已在 %s，跳过）" % (text, where))

    if args.dry_run:
        print("预览结束，未写文件（去掉 --dry-run 才会写入）")
        return 0

    if not added:
        print("没有新词，词表未改动（权限仍为 600）")
        os.chmod(config_path, 0o600)
        return 0

    write_config(config_path, config)
    touched = list(dict.fromkeys(CATEGORIES[category] for category, _text in added))
    counts = "，".join(
        "%s 共 %d 条" % (key, len(config.get(key) or [])) for key in touched
    )
    print("已写入 %s（%s），权限 600" % (config_path, counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
