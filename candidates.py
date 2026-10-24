#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""候选词收集：把「规则认得、词表里没有」的实体记下来。

脱敏时顺手记录公司/机构名和人名，写到输出文件旁边的 *.candidates.json。
文件里是没遮住的原始名称，属于敏感信息，权限固定 600。
确认后可用 tools/merge_candidates.py 一键并入词表。

只做本地规则判断：不发网络请求、不调模型，和脱敏主流程保持一致。
"""
from __future__ import annotations

import json
import os
from datetime import datetime

CATEGORY_ORDER = ("org", "people", "customer", "supplier")

CATEGORY_LABELS = {
    "org": "机构",
    "people": "人名",
    "customer": "客户",
    "supplier": "供应商",
}

# 候选分类 → 词表字段。合并时按这张表落库。
CONFIG_KEYS = {
    "org": "extra_orgs",
    "people": "extra_people",
    "customer": "extra_customers",
    "supplier": "extra_suppliers",
}

WARNING = (
    "本文件是未脱敏的原始名称，属于敏感信息；权限 600，切勿外发。"
    "确认后用 tools/merge_candidates.py 并入词表。"
)


class CandidateCollector:
    """按原文去重收集候选：同一个名字只出现一次，记次数和首次位置。"""

    def __init__(self, known: set[str] | None = None):
        self.known = set(known or ())
        self._items: dict[str, dict] = {}
        self._order = 0

    def add(self, category: str, text: str, location: str) -> None:
        text = (text or "").strip()
        if not text or category not in CONFIG_KEYS or text in self.known:
            return
        entry = self._items.get(text)
        if entry is not None:
            entry["count"] += 1
            # 同一串名字既出现在普通正文、又出现在「客户：」「供应商：」后面时，
            # 以带标签的细分类为准。
            if entry["category"] in ("org", "people") and category in ("customer", "supplier"):
                entry["category"] = category
            return
        self._items[text] = {
            "category": category,
            "text": text,
            "count": 1,
            "first_seen": location,
            "_order": self._order,
        }
        self._order += 1

    def __bool__(self) -> bool:
        return bool(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def counts(self) -> dict[str, int]:
        counts = {category: 0 for category in CATEGORY_ORDER}
        for entry in self._items.values():
            counts[entry["category"]] += 1
        return counts

    def entries(self) -> list[dict]:
        ordered = sorted(
            self._items.values(),
            key=lambda entry: (CATEGORY_ORDER.index(entry["category"]), entry["_order"]),
        )
        return [
            {key: value for key, value in entry.items() if key != "_order"}
            for entry in ordered
        ]

    def payload(self, source: str = "") -> dict:
        entries = self.entries()
        counts = self.counts()
        summary = {"total": len(entries)}
        summary.update(counts)
        return {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": source,
            "warning": WARNING,
            "summary": summary,
            "candidates": entries,
        }


def _write_private(path: str, content: str) -> None:
    """写文件并强制 600 权限：候选文件里是没遮住的原名。"""
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.chmod(path, 0o600)


def write_candidates(path: str, collector: CandidateCollector, source: str = "") -> str:
    content = json.dumps(collector.payload(source), ensure_ascii=False, indent=2)
    _write_private(path, content + "\n")
    return path


def finalize_candidates(
    collector: CandidateCollector | None,
    path: str | None,
    source: str = "",
) -> tuple[str, str]:
    """写入候选文件，返回（报告摘要，敏感文件提醒）。"""
    if collector is None:
        return "已关闭（--no-candidates）", ""
    if not len(collector):
        return "未发现词表外实体（识别到的机构名、人名都已在词表内）", ""
    if not path:
        return (
            "发现 %d 个词表外实体，但未指定候选文件路径，未写入" % len(collector),
            "",
        )
    counts = collector.counts()
    detail = "、".join(
        "%s %d" % (CATEGORY_LABELS[category], count)
        for category, count in counts.items()
        if count
    )
    try:
        write_candidates(path, collector, source)
    except OSError as exc:
        return "发现 %d 个词表外实体，但候选文件写入失败：%s" % (len(collector), exc), ""
    return (
        "发现 %d 个词表外实体，已写入 %s（%s）；确认后可一键并入词表"
        % (len(collector), path, detail),
        "候选词文件含未脱敏的原始名称，权限 600，切勿随脱敏件一起外发",
    )
