#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按映射表把脱敏文档里的代号换回原文。

用法：
    python restore.py 合同_脱敏.docx --map 合同_脱敏.map.json -o 合同_还原.docx

映射表由 redact.py 生成，内含原文，属于敏感文件，用完请妥善保管或删除。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from docx import Document

from redact import (
    Span,
    apply_spans,
    direct_runs,
    iter_document_paragraphs,
    paragraph_text,
)


def find_token_spans(text: str, mapping: dict[str, str]) -> list[Span]:
    """找出所有代号出现位置；重叠时保留更长的代号。"""
    candidates: list[Span] = []
    for token, original in mapping.items():
        start = 0
        while True:
            index = text.find(token, start)
            if index < 0:
                break
            candidates.append(Span(index, index + len(token), original))
            start = index + len(token)

    candidates.sort(key=lambda span: (span.start, -(span.end - span.start)))
    accepted: list[Span] = []
    for span in candidates:
        if any(span.start < other.end and other.start < span.end for other in accepted):
            continue
        accepted.append(span)
    accepted.sort(key=lambda span: span.start)
    return accepted


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按映射表还原脱敏文档")
    parser.add_argument("input", help="脱敏后的 .docx 文件")
    parser.add_argument("--map", dest="map_path", required=True, help="映射表 JSON")
    parser.add_argument(
        "--passphrase",
        help="映射表加密时用的口令（也可用环境变量 CONTRACT_REDACT_PASSPHRASE）",
    )
    parser.add_argument(
        "--report",
        dest="report_path",
        help="把恢复报告写成 JSON",
    )
    parser.add_argument(
        "-o", "--output",
        help="还原后的输出文件（默认：原文件名 + .restored.docx）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source = os.path.abspath(args.input)
    map_path = os.path.abspath(args.map_path)

    if not os.path.isfile(source):
        print("错误：找不到文件 %s" % source, file=sys.stderr)
        return 2
    ext = os.path.splitext(source)[1].lower()
    if ext not in {".docx", ".md", ".markdown", ".txt", ".xlsx", ".xlsm", ".pdf"}:
        print("错误：不支持的格式 %s（支持 .docx / .pdf / .md / .txt / .xlsx）" % (ext or "无扩展名"), file=sys.stderr)
        return 2
    if not os.path.isfile(map_path):
        print("错误：找不到映射表 %s" % map_path, file=sys.stderr)
        return 2

    if args.output:
        output = os.path.abspath(args.output)
    else:
        root, _ = os.path.splitext(source)
        output = root + ".restored" + ext
    if output == source:
        print("错误：输出文件不能和源文件相同，避免覆盖脱敏件", file=sys.stderr)
        return 2

    passphrase = args.passphrase or os.environ.get("CONTRACT_REDACT_PASSPHRASE")
    from mapping_crypto import load_mapping

    try:
        mapping = load_mapping(map_path, passphrase)
    except ValueError as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 2
    if not isinstance(mapping, dict) or not all(
        isinstance(key, str) and key and isinstance(value, str)
        for key, value in mapping.items()
    ):
        print("错误：映射表格式不对，应为非空代号到原文的 JSON 对象", file=sys.stderr)
        return 2

    if os.path.realpath(output) == os.path.realpath(map_path):
        print("错误：输出文件不能覆盖映射表", file=sys.stderr)
        return 2

    if ext != ".docx":
        from formats import restore_any

        return restore_any(source, output, mapping)

    doc = Document(source)
    total = 0
    changed_paragraphs = 0
    used_counts: dict[str, int] = {token: 0 for token in mapping}
    for paragraph, _where in iter_document_paragraphs(doc):
        runs = direct_runs(paragraph)
        if not runs:
            continue
        text = paragraph_text(runs)
        if not text:
            continue
        spans = find_token_spans(text, mapping)
        if not spans:
            continue
        for span in spans:
            token = text[span.start:span.end]
            used_counts[token] = used_counts.get(token, 0) + 1
        apply_spans(runs, text, spans)
        total += len(spans)
        changed_paragraphs += 1

    doc.save(output)

    # 恢复报告：每个代号用了几次、哪些没用上、文档里还有哪些不认识的代号
    import re as _re

    used = used_counts
    unknown = set()
    for paragraph, _where in iter_document_paragraphs(doc):
        unknown.update(_re.findall(r"【[^】]{1,12}】", paragraph.text))
    unused = sorted(t for t, n in used.items() if n == 0)
    report = {
        "restored": sum(used.values()),
        "tokens_in_mapping": len(mapping),
        "unused_tokens": unused,
        "unknown_tokens": sorted(t for t in unknown if t not in mapping),
    }
    if args.report_path:
        with open(args.report_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        os.chmod(args.report_path, 0o600)

    print("还原完成：%s" % output)
    print("共替换 %d 处，涉及 %d 段（含表格、页眉页脚、文本框）" % (total, changed_paragraphs))
    print("映射表 %d 个代号，用上 %d 个" % (len(mapping), len(mapping) - len(unused)))
    if unused:
        print("没用上的代号（可能这段文字被删了，或这版文档没用到）：%s" % "、".join(unused[:10]))
    if report["unknown_tokens"]:
        print("警告：文档里还有不认识的代号（映射表里没有，可能来自别的文件）：%s" % "、".join(report["unknown_tokens"][:10]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
