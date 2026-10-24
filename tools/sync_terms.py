#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把客户/供应商名单同步进脱敏词表。

名单来源随便：ERP 导出、CRM 导出、多维表格导出的 CSV，或者一份 JSON。
每次跑都会把新出现的名字并进配置文件，词表里已有的保留，别的手写项（keep、别名）不动。

用法：
  # 从 CSV 同步客户（列名：客户名称 / 客户简称）
  python tools/sync_terms.py --config vocab.json --kind customer \\
      --csv 客户.csv --column 客户名称 --short-column 客户简称

  # 从 CSV 同步供应商
  python tools/sync_terms.py --config vocab.json --kind supplier \\
      --csv 供应商.csv --column 供应商名称

  # 只看会新增多少，不写文件
  python tools/sync_terms.py ... --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys


def read_csv(path: str, column: str, short_column: str | None):
    """返回 (名称集合, 简称→名称 的别名表)。"""
    names, aliases = set(), {}
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if column not in (reader.fieldnames or []):
            raise SystemExit("CSV 里没有列「%s」，现有列：%s" % (column, reader.fieldnames))
        for row in reader:
            name = (row.get(column) or "").strip()
            if not name:
                continue
            names.add(name)
            if short_column:
                short = (row.get(short_column) or "").strip()
                if short and short != name:
                    aliases.setdefault(short, set()).add(name)
    return names, aliases


def read_json(path: str, column: str, short_column: str | None):
    data = json.load(open(path, encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("items") or data.get("records") or []
    names, aliases = set(), {}
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get(column) or "").strip()
        if not name:
            continue
        names.add(name)
        if short_column:
            short = str(item.get(short_column) or "").strip()
            if short and short != name:
                aliases.setdefault(short, set()).add(name)
    return names, aliases


def main():
    ap = argparse.ArgumentParser(description="把客户/供应商名单同步进脱敏词表")
    ap.add_argument("--config", required=True, help="词表 JSON（不存在会新建）")
    ap.add_argument("--kind", choices=["customer", "supplier"], required=True)
    ap.add_argument("--csv", action="append", default=[], help="CSV 文件，可重复")
    ap.add_argument("--json", dest="json_file", action="append", default=[], help="JSON 文件，可重复")
    ap.add_argument("--column", required=True, help="名称所在列")
    ap.add_argument("--short-column", help="简称所在列（可选，会建成别名）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.csv and not args.json_file:
        raise SystemExit("至少给一个 --csv 或 --json")

    names, aliases = set(), {}
    for path in args.csv:
        n, a = read_csv(path, args.column, args.short_column)
        names |= n
        for k, v in a.items():
            aliases.setdefault(k, set()).update(v)
    for path in args.json_file:
        n, a = read_json(path, args.column, args.short_column)
        names |= n
        for k, v in a.items():
            aliases.setdefault(k, set()).update(v)

    config = {}
    if os.path.isfile(args.config):
        config = json.load(open(args.config, encoding="utf-8"))

    key = "extra_customers" if args.kind == "customer" else "extra_suppliers"
    existing = list(config.get(key) or [])
    merged = sorted(set(existing) | names)
    added = sorted(set(names) - set(existing))

    print("读入 %d 个名字，词表原有 %d 条，新增 %d 条" % (len(names), len(existing), len(added)))
    for name in added[:20]:
        print("  +", name)
    if len(added) > 20:
        print("  ……还有 %d 条" % (len(added) - 20))

    if args.dry_run:
        return 0

    config[key] = merged
    alias_map = dict(config.get("aliases") or {})
    for short, full_set in aliases.items():
        current = set(alias_map.get(short) or [])
        alias_map[short] = sorted(current | full_set)
    config["aliases"] = dict(sorted(alias_map.items()))

    descriptor = os.open(args.config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.chmod(args.config, 0o600)
    print("已写入 %s（%s 共 %d 条）" % (args.config, key, len(merged)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
