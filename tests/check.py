#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验收脚本：生成样例 → 脱敏 → 检查残留/保留项/属性 → 还原 → 逐字比对。"""

from __future__ import annotations

import difflib
import html
import io
import json
import os
import re
import stat
import sys
import tempfile
import zipfile
from contextlib import redirect_stdout

from docx import Document

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import redact  # noqa: E402
import restore  # noqa: E402
import make_sample  # noqa: E402
from redact import direct_runs, iter_document_paragraphs, paragraph_text  # noqa: E402

APP_COMPANY_TEXT_RE = re.compile(rb"<Company(?:\s[^>]*)?>(.*?)</Company>", re.S)


def all_text(path: str) -> str:
    doc = Document(path)
    return "\n".join(
        paragraph_text(direct_runs(paragraph))
        for paragraph, _where in iter_document_paragraphs(doc)
    )


def app_company(path: str) -> str:
    with zipfile.ZipFile(path) as archive:
        data = archive.read("docProps/app.xml")
    match = APP_COMPANY_TEXT_RE.search(data)
    if match is None:
        return ""
    return html.unescape(match.group(1).decode("utf-8", "replace"))


def call_main(module, argv: list[str] | None = None) -> str:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = module.main() if argv is None else module.main(argv)
    if code != 0:
        raise RuntimeError("%s 返回 %s" % (module.__name__, code))
    return buffer.getvalue()


def main() -> int:
    sample = os.path.join(HERE, "sample.docx")
    redacted = os.path.join(HERE, "sample_redacted.docx")
    restored = os.path.join(HERE, "sample_restored.docx")
    map_path = os.path.join(HERE, "sample_redacted.map.json")
    report_path = os.path.join(tempfile.gettempdir(), "contract-redact-sample.report.txt")
    if os.path.exists(report_path):
        os.remove(report_path)

    print(call_main(make_sample))
    original_text = all_text(sample)

    print("===== 运行 redact.py =====")
    redact_output = call_main(
        redact,
        [
            sample,
            "-o", redacted,
            "--map", map_path,
            "--report", report_path,
        ],
    )
    print(redact_output)

    redacted_text = all_text(redacted)
    mapping = json.load(open(map_path, encoding="utf-8"))
    failures: list[str] = []

    print("===== 脱敏后全文 =====")
    print(redacted_text)

    print("\n===== 检查 =====")
    leftovers = sorted({value for value in mapping.values() if value in redacted_text})
    print("映射项：%d" % len(mapping))
    print("脱敏后仍能搜到原文的项：%s" % ("无" if not leftovers else "、".join(leftovers)))
    if leftovers:
        failures.append("有原文残留")

    expected_tokens = [
        "【公司A】", "【公司B】",
        "【姓名A】", "【姓名B】", "【姓名C】",
        "【电话1】", "【电话2】",
        "【身份证】", "【信用代码1】", "【信用代码2】",
        "【银行账号】", "【地址1】", "【地址2】", "【邮箱】",
    ]
    missing_tokens = [token for token in expected_tokens if token not in redacted_text]
    print("代号齐全：%s" % ("是" if not missing_tokens else "缺 " + "、".join(missing_tokens)))
    if missing_tokens:
        failures.append("缺少代号")

    keep_items = [
        "1,000,000.00",
        "30%",
        "12 个月",
        "第 3.2 条",
        "2026 年 12 月 31 日",
        "HT-2026-0417",
    ]
    for item in keep_items:
        ok = item in redacted_text
        print("  保留 %-18s %s" % (item, "✓" if ok else "✗"))
        if not ok:
            failures.append("误改 %s" % item)

    map_mode = stat.S_IMODE(os.stat(map_path).st_mode)
    report_mode = stat.S_IMODE(os.stat(report_path).st_mode)
    print("映射表权限：%o %s" % (map_mode, "✓" if map_mode == 0o600 else "✗"))
    print("报告权限：%o %s" % (report_mode, "✓" if report_mode == 0o600 else "✗"))
    if map_mode != 0o600:
        failures.append("映射表权限不是 600")
    if report_mode != 0o600:
        failures.append("报告权限不是 600")

    redacted_doc = Document(redacted)
    author_ok = redacted_doc.core_properties.author == ""
    last_ok = redacted_doc.core_properties.last_modified_by == ""
    company_ok = app_company(redacted) == ""
    print("作者已清空：%s" % ("是" if author_ok else "否"))
    print("最后修改者已清空：%s" % ("是" if last_ok else "否"))
    print("公司属性已清空：%s" % ("是" if company_ok else "否"))
    if not (author_ok and last_ok and company_ok):
        failures.append("文档属性未清空")

    for expected in (
        "页眉 1 处",
        "页脚 2 处",
        "作者：合同管理员",
        "最后修改者：法务部",
        "公司：北辰智联科技有限公司",
        "规则识别不保证遮干净",
    ):
        ok = expected in redact_output
        print("报告包含 %-22s %s" % (expected, "✓" if ok else "✗"))
        if not ok:
            failures.append("报告缺少 %s" % expected)

    print("\n===== 运行 restore.py =====")
    print(call_main(
        restore,
        [redacted, "--map", map_path, "-o", restored],
    ))

    restored_text = all_text(restored)
    remaining_tokens = [token for token in mapping if token in restored_text]
    same = restored_text == original_text
    print("还原后与原文逐字一致：%s" % ("是" if same else "否"))
    print("还原后仍残留代号：%s" % ("无" if not remaining_tokens else "、".join(remaining_tokens)))
    if not same:
        failures.append("还原不一致")
        diff = difflib.unified_diff(
            original_text.split("\n"),
            restored_text.split("\n"),
            fromfile="sample.docx",
            tofile="sample_restored.docx",
            lineterm="",
        )
        print("\n".join(list(diff)[:30]))
    if remaining_tokens:
        failures.append("还原后仍有代号")

    print("\n结果：%s" % ("全部通过" if not failures else "失败：" + "；".join(failures)))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
