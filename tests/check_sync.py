#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证词表同步：新增并入、别名合并、手写项不丢、重复跑不重复。"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable


def write_text(path, text):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)


def run(config, kind, csv_path, column, short=None):
    cmd = [PY, os.path.join(ROOT, "tools", "sync_terms.py"),
           "--config", config, "--kind", kind, "--csv", csv_path, "--column", column]
    if short:
        cmd += ["--short-column", short]
    return subprocess.run(cmd, capture_output=True, text=True)


def main():
    tmp = tempfile.mkdtemp(prefix="sync-test-")
    config = os.path.join(tmp, "vocab.json")
    customers = os.path.join(tmp, "c.csv")
    write_text(customers, "客户名称,客户简称\nACME 工业有限公司,ACME\n北方传动有限公司,NC\n")

    # 预置一个手写项，确认同步不会把它冲掉
    write_text(config, json.dumps({"keep": ["市仲裁委员会"]}, ensure_ascii=False))

    r1 = run(config, "customer", customers, "客户名称", "客户简称")
    data = json.load(open(config, encoding="utf-8"))
    ok1 = r1.returncode == 0 and len(data["extra_customers"]) == 2
    ok2 = data.get("keep") == ["市仲裁委员会"]
    ok3 = data["aliases"].get("ACME") == ["ACME 工业有限公司"]

    r2 = run(config, "customer", customers, "客户名称", "客户简称")
    data2 = json.load(open(config, encoding="utf-8"))
    ok4 = len(data2["extra_customers"]) == 2          # 重复跑不重复
    ok5 = "新增 0 条" in r2.stdout

    fails = [name for name, ok in
             [("首次同步写入", ok1), ("手写项保留", ok2), ("简称建别名", ok3),
              ("重复跑不重复", ok4), ("打印新增 0 条", ok5)] if not ok]
    print("同步测试：%s" % ("全部通过" if not fails else "失败 - " + "、".join(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
