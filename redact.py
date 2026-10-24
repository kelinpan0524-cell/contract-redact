#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中文合同脱敏（本地规则版）。

扫描正文、表格、页眉页脚、文本框里的可识别身份信息，替换成代号后另存新文件。
原文与代号的对应关系写入 JSON 映射表（权限 600），可用 restore.py 还原。

用法：
    python redact.py 合同.docx -o 合同_脱敏.docx \
        --map 合同_脱敏.map.json --report 合同_脱敏.report.txt
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime

from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from candidates import CandidateCollector, finalize_candidates

# ---------------------------------------------------------------------------
# 类别、优先级与代号
# ---------------------------------------------------------------------------

GROUP_LABELS = {
    "company": "公司名",
    "person": "人名",
    "phone": "电话",
    "id": "身份证",
    "credit": "信用代码",
    "bank": "银行账号",
    "address": "地址",
    "email": "邮箱",
    "customer": "客户",
    "supplier": "供应商",
    "password": "密码",
}

TYPED_BASE = {
    "phone": "电话",
    "id": "身份证",
    "credit": "信用代码",
    "bank": "银行账号",
    "address": "地址",
    "email": "邮箱",
    "password": "密码",
}

# 数值越小越先占用文本区间。身份证必须优先于银行账号。
PRIORITY = {
    "id": 10,
    "password": 15,
    "bank": 20,
    "credit": 30,
    "phone": 40,
    "address": 60,
    "company": 70,
    "person": 80,
}

COMPOUND_SURNAMES = {
    "欧阳", "司马", "上官", "诸葛", "东方", "皇甫", "尉迟", "公孙", "慕容",
    "长孙", "宇文", "司徒", "令狐", "夏侯", "南宫", "西门", "独孤", "端木",
    "轩辕", "百里", "呼延", "澹台", "公冶", "宗政", "濮阳", "淳于",
    "单于", "太叔", "申屠", "公羊", "闻人", "赫连", "钟离", "万俟", "鲜于",
}

# ---------------------------------------------------------------------------
# 基础校验
# ---------------------------------------------------------------------------

ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
ID_CHECK = "10X98765432"


ENABLED_GROUPS: set[str] | None = None   # None = 全开
MASK_ONLY = False                        # 不可逆掩码模式


def set_enabled(only: str | None, skip: str | None) -> None:
    """--only / --skip：控制哪些类别参与脱敏。"""
    global ENABLED_GROUPS
    all_groups = {"company", "person", "phone", "id", "credit", "bank", "address", "email",
                  "customer", "supplier", "password"}
    if only:
        picked = {g.strip() for g in only.split(",") if g.strip()}
        unknown = picked - all_groups
        if unknown:
            raise SystemExit("--only 里有不认识的类别：%s（可用：%s）" % (",".join(sorted(unknown)), ",".join(sorted(all_groups))))
        ENABLED_GROUPS = picked
    elif skip:
        dropped = {g.strip() for g in skip.split(",") if g.strip()}
        ENABLED_GROUPS = all_groups - dropped
    if ENABLED_GROUPS is not None and not ENABLED_GROUPS:
        raise SystemExit("--only/--skip 把所有类别都关掉了，没有可脱敏的内容")


def mask_value(value: str) -> str:
    """不可逆掩码：留首尾，中间打星。用于「发出去不回收」的材料。"""
    if len(value) <= 2:
        return "*" * len(value)
    if len(value) <= 6:
        return value[0] + "*" * (len(value) - 2) + value[-1]
    stars = "*" * min(8, len(value) - 3)
    return value[0] + stars + value[-2:]


CFG = {
    "extra_orgs": [],
    "extra_people": [],
    "extra_customers": [],
    "extra_suppliers": [],
    "keep": [],
    "org_map": {},
    "person_map": {},
    "customer_map": {},
    "supplier_map": {},
}


def load_config(path: str) -> dict:
    """载入补充词表（JSON）。只影响识别，不影响已有规则。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for key in ("extra_orgs", "extra_people", "extra_customers", "extra_suppliers", "keep"):
        values = data.get(key) or []
        if not isinstance(values, list):
            raise ValueError("配置项 %s 必须是字符串数组" % key)
        CFG[key] = [str(v).strip() for v in values if str(v).strip()]

    # aliases：同一家的多种写法共用一个代号，例如 {"星海集团": ["星海", "星海实业", "星海集团"]}
    for target, key in (("org_map", "extra_orgs"), ("person_map", "extra_people"), ("customer_map", "extra_customers"), ("supplier_map", "extra_suppliers")):
        amap: dict[str, str] = {}
        for term in CFG[key]:
            amap[term] = term
        for canonical, variants in (data.get("aliases") or {}).items():
            family = [str(canonical).strip()] + [str(v).strip() for v in (variants or [])]
            family = [v for v in family if v]
            # 别名只归到「本来就有这一家」的那张表，避免把客户名塞进公司表
            if not any(v in amap for v in family):
                continue
            for variant in family:
                amap[variant] = str(canonical).strip()
        # 只保留确实在 extra_* 里出现过的词 + 别名
        CFG[target] = amap
    return CFG


def config_terms() -> set[str]:
    """词表里已经有的所有写法（含别名、规范名和白名单），用于排除候选。"""
    terms = set(CFG["keep"])
    for key in ("org_map", "person_map", "customer_map", "supplier_map"):
        for term, canonical in CFG[key].items():
            terms.add(term)
            terms.add(canonical)
    return terms


_ORG_TAIL_RE = re.compile(
    r"(股份有限公司|有限责任公司|有限公司|集团公司|集团|公司|工厂|厂|事务所|研究院|设计院|中心|合作社)$"
)


def _name_char(ch: str) -> bool:
    return ch.isalnum() or "\u4e00" <= ch <= "\u9fff" or ch in "（）()·&—"


def _expand_to_org(text: str, start: int, end: int, term: str):
    """词表里只有「星海」，正文写的是「星海精密制造有限公司」——向外扩成完整主体。

    只在扩出来的串确实像公司名时才扩；普通叙述（「星海的交付周期」）保持原样。
    允许中文名与英文代号之间有一个空格（「ACME 工业有限公司」）。
    """
    left = start
    while left > 0 and _name_char(text[left - 1]):
        left -= 1

    right = end
    while right < len(text) and _name_char(text[right]):
        right += 1

    # 「ACME 工业有限公司」：跳过空格再吃一段，只有整串像公司名时才算数
    probe = right
    while probe < len(text) and text[probe] == " ":
        probe += 1
    probe_end = probe
    while probe_end < len(text) and _name_char(text[probe_end]):
        probe_end += 1
    if probe_end > probe and _ORG_TAIL_RE.search(text[left:probe_end]):
        right = probe_end

    candidate = text[left:right].strip()
    if candidate == term or not _ORG_TAIL_RE.search(candidate):
        return start, end, term

    # 剥掉前面的连接词（由/与/和/及/向/给/为/是/在/按/经/本/该/称）
    while len(candidate) > len(term) and candidate[0] in "由与和及向给为是在按经本该称甲公司厂":
        if len(candidate) - 1 >= len(term):
            candidate = candidate[1:]
        else:
            break
    offset = text.find(candidate, max(0, start - len(candidate)))
    if offset != -1:
        return offset, offset + len(candidate), candidate
    return start, end, term


def _config_matches(text: str) -> list["RawMatch"]:
    """配置里的专有名词：简称、客户代号这类规则认不出的写法。"""
    out: list[RawMatch] = []
    for kind, group in (("org_map", "company"), ("person_map", "person"), ("customer_map", "customer"), ("supplier_map", "supplier")):
        for term, canonical in CFG[kind].items():
            # 纯英文/数字的短代号（如 MS、ACME）按词边界匹配，避免嵌在别的单词里误伤
            if term.isascii() and re.fullmatch(r"[A-Za-z0-9&.\-]+", term) and len(term) <= 4:
                pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])")
                for m in pattern.finditer(text):
                    if group in ("company", "customer", "supplier"):
                        start2, end2, _value = _expand_to_org(text, m.start(), m.end(), term)
                        out.append(RawMatch(start2, end2, group, canonical, 5))
                    else:
                        out.append(RawMatch(m.start(), m.end(), group, canonical, 5))
                continue
            start = text.find(term)
            while start != -1:
                if group in ("company", "customer", "supplier"):
                    # 代号按词表里的规范写法给：同一家的各种写法共用一个代号
                    start2, end2, _value = _expand_to_org(text, start, start + len(term), term)
                    out.append(RawMatch(start2, end2, group, canonical, 5))
                else:
                    out.append(RawMatch(start, start + len(term), group, canonical, 5))
                start = text.find(term, start + 1)
    return out


def _drop_keep(matches: list["RawMatch"], text: str) -> list["RawMatch"]:
    """白名单：公共机构、银行、认证机构这类不该遮的，命中就放过。"""
    if not CFG["keep"]:
        return matches
    spans = []
    for term in CFG["keep"]:
        start = text.find(term)
        while start != -1:
            spans.append((start, start + len(term)))
            start = text.find(term, start + 1)
    if not spans:
        return matches
    return [m for m in matches if not any(not (m.end <= s or m.start >= e) for s, e in spans)]


def valid_id(value: str) -> bool:
    """GB 11643 身份证校验位。"""
    if not re.fullmatch(r"\d{17}[\dXx]", value):
        return False
    total = sum(int(ch) * weight for ch, weight in zip(value[:17], ID_WEIGHTS))
    return ID_CHECK[total % 11] == value[17].upper()


# ---------------------------------------------------------------------------
# 正则与清洗
# ---------------------------------------------------------------------------

MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d(?:[\s\-]?\d{4}){2}(?!\d)")
LANDLINE_RE = re.compile(
    r"(?<!\d)(?:\(?0\d{2,3}\)?[\s\-－—])\d{7,8}(?:[\s\-转]\d{1,5})?(?!\d)"
)
PASSWORD_RE = re.compile(
    r"(?:密码|口令|passwd|password|pwd|secret|token|api[_-]?key|access[_-]?key)"
    r"\s*[:：=]\s*([^\s，。；;,、]{4,80})",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
ID_RE = re.compile(r"(?<!\d)(\d{17}[\dXx])(?!\d)")
# 统一社会信用代码字符集：数字 + 大写字母，排除 I、O、S、V、Z
USCC_RE = re.compile(r"(?<![0-9A-Za-z])([0-9A-HJ-NPQRTUWXY]{18})(?![0-9A-Za-z])")
BANK_RE = re.compile(r"(?<!\d)(\d{12,25})(?!\d)")

COMPANY_SUFFIX_RE = re.compile(
    r"股份有限公司|有限责任公司|集团有限公司|集团公司|有限公司"
    r"|设计院|研究院|事务所|合作社|集团|公司|厂|中心"
)

COMPANY_GENERIC = {
    "公司", "集团", "中心", "事务所", "研究院", "厂", "有限公司",
    "股份有限公司", "有限责任公司", "分公司", "子公司", "总公司",
    "本公司", "该公司", "贵公司", "集团公司", "集团有限公司",
}

COMPANY_PREFIXES = tuple(
    sorted(
        (
            "用人单位", "用工单位", "本合同", "本协议", "委托方", "受托方",
            "承租方", "出租方", "甲方", "乙方", "丙方", "丁方", "买方",
            "卖方", "供方", "需方", "合同", "协议", "项下", "上述", "下述",
            "下列", "如下", "以下", "双方", "各方", "三方", "现将", "现就",
            "针对", "关于", "对于", "委托", "聘请", "指定", "通知", "致函",
            "发函", "联系", "告知", "要求", "选择", "确定", "认可", "同意",
            "授权", "由", "向", "在", "为", "与",
        ),
        key=len,
        reverse=True,
    )
)

ORG_CHAR_RE = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9０-９Ａ-Ｚａ-ｚ（）()·&＃#\-—_]"
)
BOUNDARY_CHARS = set(
    " \t\r\n\u3000，。；：、！？,.;:!?<>《》「」『』“”‘’\"'【】[]{}"
)

ADDRESS_PREFIXES = tuple(
    sorted(
        (
            "公司注册地址", "公司办公地址", "注册地址", "办公地址", "公司地址",
            "联系地址", "通讯地址", "住所地", "住所", "住址", "地址",
            "坐落于", "位于", "在", "为", "系", "的", "是", "该", "其",
        ),
        key=len,
        reverse=True,
    )
)
ADDRESS_BOUNDARY = BOUNDARY_CHARS | set("（）()")

PERSON_LABEL_RE = re.compile(
    r"(?:法定代表人|委托代理人|授权代表|联系人|对接人|经办人|负责人|收件人|收货人|业务员|销售|签署人|签字|姓名)\s*[：:]?\s*"
    r"([\u4e00-\u9fa5]{2,12})"
)
PERSON_CUT_TOKENS = (
    "先生", "女士", "小姐", "经理", "总监", "董事", "主任", "老师", "同志",
    "律师", "代表", "电话", "手机", "签字", "盖章", "日期", "身份", "地址",
    "邮箱", "职务", "单位", "部门", "共同", "分别", "以及", "和", "与", "及",
)
PERSON_STOPWORDS = {
    "盖章", "日期", "签字", "姓名", "本人", "授权", "委托", "单位", "公司",
    "职务", "电话", "手机", "地址", "邮箱", "身份", "证号", "以上", "如下",
    "确认", "同意", "意见", "签章", "代表", "先生", "女士",
}
PERSON_BAD_PREFIXES = (
    "电话", "手机", "地址", "邮箱", "单位", "职务", "部门", "身份", "证号",
    "姓名", "签字", "盖章", "日期", "本人", "公司", "先生", "女士",
)

PARTY_RE = re.compile(
    r"(?:甲方|乙方|丙方|丁方|买方|卖方|供方|需方|委托方|受托方|承租方|出租方)"
    r"\s*[：:]\s*([^\s，。；;、：:（）()【】\[\]“”\"']{2,40})"
)
ORG_HINTS = (
    "科技", "贸易", "实业", "建设", "工程", "商贸", "咨询", "服务", "管理",
    "投资", "控股", "置业", "地产", "文化", "传媒", "物流", "运输", "制造",
    "机械", "电子", "食品", "医药", "集团", "工作室", "商行", "经营部",
    "门市部", "合作社", "医院", "学校", "大学", "银行", "证券", "保险",
    "事务所", "研究院", "中心",
)


def clean_company(raw: str) -> str:
    """剥掉公司名前面的“甲方”“由”“与”等连接词。"""
    name = raw.strip().lstrip("（）()·&＃#—-_ ")
    changed = True
    while changed and len(name) > 4:
        changed = False
        for prefix in COMPANY_PREFIXES:
            if name.startswith(prefix):
                name = name[len(prefix):]
                changed = True
                break
    return name


def clean_address(raw: str) -> str:
    name = raw.strip().lstrip("·&＃#—-_ ")
    changed = True
    while changed and len(name) > 6:
        changed = False
        for prefix in ADDRESS_PREFIXES:
            if name.startswith(prefix):
                name = name[len(prefix):]
                changed = True
                break
    return name


def valid_address(value: str) -> bool:
    if not (6 <= len(value) <= 80):
        return False
    if not value.endswith(("号", "室", "楼", "栋")):
        return False
    if not re.search(r"[0-9０-９〇零一二三四五六七八九十百千万两]+\s*[号室楼栋]", value):
        return False
    has_region = any(ch in value for ch in "省市区县镇乡旗州")
    has_road = any(word in value for word in ("路", "街", "大道", "巷", "胡同", "弄"))
    return has_region or has_road


def extract_person_name(raw: str) -> str | None:
    """从标签后的连续汉字里截出 2-4 字姓名。"""
    text = raw.lstrip("的")
    if not text or text.startswith(PERSON_BAD_PREFIXES):
        return None

    cut = len(text)
    for token in PERSON_CUT_TOKENS:
        idx = text.find(token, 2)
        if idx != -1:
            cut = min(cut, idx)
    text = text[:cut]

    if len(text) > 4:
        text = text[:4] if text[:2] in COMPOUND_SURNAMES else text[:3]

    if len(text) < 2 or text in PERSON_STOPWORDS:
        return None
    if not re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", text):
        return None
    return text


# ---------------------------------------------------------------------------
# 匹配
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawMatch:
    start: int
    end: int
    group: str
    value: str
    priority: int


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    replacement: str


def _add(matches: list[RawMatch], start: int, end: int, group: str, value: str, priority: int) -> None:
    value = value.strip()
    if value and end > start:
        matches.append(RawMatch(start, end, group, value, priority))


def _scan_backward(text: str, end: int, boundary: set[str], max_len: int) -> int:
    start = end
    while start > 0 and end - start < max_len:
        ch = text[start - 1]
        if ch in boundary or not ORG_CHAR_RE.match(ch):
            break
        start -= 1
    return start


# ---------------------------------------------------------------------------
# 候选词：规则认得、但词表里没有的实体
# ---------------------------------------------------------------------------

# 候选是给人确认的线索，宁可多报一条让人删，也别漏掉没遮住的名字。
# 但这几类明显是泛指，收进去只会污染词表。
CANDIDATE_ORG_STOPWORDS = COMPANY_GENERIC | {
    "母公司", "目标公司", "标的公司", "关联公司", "项目公司", "控股公司",
    "全资子公司", "下属公司", "合作公司", "第三方公司", "客户公司", "供应商公司",
}

# 「王经理」「李总监」是称呼不是姓名，候选里不单列。
CANDIDATE_PERSON_TITLES = (
    "经理", "总监", "主任", "董事", "老师", "先生", "女士", "小姐", "同志",
    "律师", "代表", "专员", "助理", "主管", "部长", "处长", "科长",
)

CUSTOMER_LABEL_RE = re.compile(r"客户\s*(?:名称|单位|全称)?\s*[：:]\s*$")
SUPPLIER_LABEL_RE = re.compile(r"供应商\s*(?:名称|单位|全称)?\s*[：:]\s*$")


def _candidate_org_category(text: str, start: int) -> str:
    """看名字前面的标签：客户 / 供应商 归到对应分类，其余算普通机构。"""
    prefix = text[max(0, start - 12):start].rstrip()
    if SUPPLIER_LABEL_RE.search(prefix):
        return "supplier"
    if CUSTOMER_LABEL_RE.search(prefix):
        return "customer"
    return "org"


def discover_candidates(text: str) -> list[tuple[str, str]]:
    """返回 [(分类, 原文)]：带后缀的机构名 + 带标签的人名。

    只认「长得像」的写法，不做语义判断；结果供人工确认，不直接改词表。
    """
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    org_hits: list[tuple[int, int, str]] = []
    for m in COMPANY_SUFFIX_RE.finditer(text):
        end = m.end()
        start = _scan_backward(text, end, BOUNDARY_CHARS, 40)
        name = clean_company(text[start:end])
        if len(name) < 4 or name in CANDIDATE_ORG_STOPWORDS:
            continue
        if not re.search(r"[\u4e00-\u9fff]", name):
            continue
        org_hits.append((end - len(name), end, name))

    # 后缀套后缀（「XX设计院有限公司」）会同时命中「设计院」和「有限公司」，
    # 只保留最长的完整名称，别把半截名字收进候选。
    for start, end, name in org_hits:
        if any(
            other_start <= start and end <= other_end and (other_end - other_start) > (end - start)
            for other_start, other_end, _other_name in org_hits
        ):
            continue
        key = (_candidate_org_category(text, start), name)
        if key not in seen:
            seen.add(key)
            found.append(key)

    for m in PERSON_LABEL_RE.finditer(text):
        raw = m.group(1)
        name = extract_person_name(raw)
        if not name:
            continue
        if name.endswith(CANDIDATE_PERSON_TITLES) or (len(name) <= 2 and name.endswith("总")):
            continue
        key = ("people", name)
        if key not in seen:
            seen.add(key)
            found.append(key)

    return found


def scan_candidates(text: str, location: str, collector: CandidateCollector | None) -> None:
    """把一段文本里的候选实体记进收集器；没开候选收集就直接返回。"""
    if collector is None or not text:
        return
    # 只排除「词表命中」（含别名扩展）的区间：规则遮掉的新名字仍要作为候选记下来
    masked = [m for m in find_matches(text) if m.priority == 5]
    for category, value in discover_candidates(text):
        position = text.find(value)
        if position != -1 and any(
            not (position + len(value) <= match.start or position >= match.end)
            for match in masked
        ):
            continue
        collector.add(category, value, location)


def find_matches(text: str) -> list[RawMatch]:
    """找出所有候选区间，按优先级消解重叠后返回。"""
    matches: list[RawMatch] = _config_matches(text)

    for m in ID_RE.finditer(text):
        if valid_id(m.group(1)):
            _add(matches, m.start(1), m.end(1), "id", m.group(1), PRIORITY["id"])

    # 校验位不对的 18 位纯数字：合同里几乎只可能是打错一位的身份证，
    # 仍然按身份证处理（否则会被下面的银行账号规则捡走，代号类型会误导人）。
    for m in ID_RE.finditer(text):
        if not valid_id(m.group(1)):
            _add(matches, m.start(1), m.end(1), "id", m.group(1), PRIORITY["id"])


    for m in BANK_RE.finditer(text):
        _add(matches, m.start(1), m.end(1), "bank", m.group(1), PRIORITY["bank"])

    for m in USCC_RE.finditer(text):
        _add(matches, m.start(1), m.end(1), "credit", m.group(1), PRIORITY["credit"])

    for m in MOBILE_RE.finditer(text):
        _add(matches, m.start(), m.end(), "phone", m.group(), PRIORITY["phone"])

    for m in LANDLINE_RE.finditer(text):
        _add(matches, m.start(), m.end(), "phone", m.group(), PRIORITY["phone"])

    for m in PASSWORD_RE.finditer(text):
        _add(matches, m.start(1), m.end(1), "password", m.group(1), PRIORITY["password"])

    for m in EMAIL_RE.finditer(text):
        _add(matches, m.start(), m.end(), "email", m.group(), 50)

    # 完整地址：从“号/室/楼/栋”往前回溯，回溯到标点或空白为止。
    for m in re.finditer(r"[号室楼栋]", text):
        end = m.end()
        start = _scan_backward(text, end, ADDRESS_BOUNDARY, 80)
        candidate = clean_address(text[start:end])
        if valid_address(candidate):
            _add(matches, end - len(candidate), end, "address", candidate, PRIORITY["address"])

    # 甲方/乙方……：后面紧跟的主体（可能是个人，也可能是不带公司后缀的单位）。
    for m in PARTY_RE.finditer(text):
        raw = m.group(1)
        core = re.sub(r"(?:先生|女士|小姐)$", "", raw)
        if not core:
            continue
        looks_company = (
            bool(COMPANY_SUFFIX_RE.search(core))
            or len(core) > 4
            or any(hint in core for hint in ORG_HINTS)
        )
        if looks_company:
            _add(matches, m.start(1), m.start(1) + len(core), "company", core, 65)
        elif 2 <= len(core) <= 4 and re.fullmatch(r"[\u4e00-\u9fa5]+", core):
            _add(matches, m.start(1), m.start(1) + len(core), "person", core, 65)

    # 公司名：以指定后缀结尾，向前回溯。
    for m in COMPANY_SUFFIX_RE.finditer(text):
        end = m.end()
        start = _scan_backward(text, end, BOUNDARY_CHARS, 40)
        name = clean_company(text[start:end])
        if len(name) < 4 or name in COMPANY_GENERIC:
            continue
        if not re.search(r"[\u4e00-\u9fff]", name):
            continue
        _add(matches, end - len(name), end, "company", name, PRIORITY["company"])

    # 人名：标签后 2-4 个汉字。
    for m in PERSON_LABEL_RE.finditer(text):
        raw = m.group(1)
        name = extract_person_name(raw)
        if not name:
            continue
        offset = m.start(1) + raw.find(name)
        _add(matches, offset, offset + len(name), "person", name, PRIORITY["person"])

    if ENABLED_GROUPS is not None:
        matches = [m for m in matches if m.group in ENABLED_GROUPS]
    return _resolve(_drop_keep(matches, text))


def _resolve(matches: list[RawMatch]) -> list[RawMatch]:
    unique: dict[tuple[int, int, str, str], RawMatch] = {}
    for match in matches:
        unique.setdefault((match.start, match.end, match.group, match.value), match)

    ordered = sorted(
        unique.values(),
        key=lambda item: (item.priority, -(item.end - item.start), item.start),
    )
    accepted: list[RawMatch] = []
    for match in ordered:
        if any(match.start < other.end and other.start < match.end for other in accepted):
            continue
        accepted.append(match)
    accepted.sort(key=lambda item: item.start)
    return accepted


# ---------------------------------------------------------------------------
# docx 遍历与改写
# ---------------------------------------------------------------------------


def direct_runs(paragraph: Paragraph) -> list[Run]:
    """只取属于本段的 run（排除文本框等嵌套段落里的 run）。"""
    p_element = paragraph._p
    runs: list[Run] = []
    for r_element in p_element.iter(qn("w:r")):
        parent = r_element.getparent()
        while parent is not None and parent.tag != qn("w:p"):
            parent = parent.getparent()
        if parent is p_element:
            runs.append(Run(r_element, paragraph))
    return runs


def paragraph_text(runs: list[Run]) -> str:
    return "".join(run.text for run in runs)


def apply_spans(runs: list[Run], text: str, spans: list[Span]) -> None:
    """按原文坐标改写 run；跨 run 的替换整体落在起始 run 里。"""
    if not spans:
        return

    run_of: list[int] = []
    for index, run in enumerate(runs):
        run_of.extend([index] * len(run.text))
    if len(run_of) != len(text):
        # 理论上不会发生；段落文本异常时宁可不动。
        return

    starts = {span.start: span for span in spans if span.end > span.start}
    new_texts = [""] * len(runs)
    pos = 0
    while pos < len(text):
        span = starts.get(pos)
        if span is not None:
            new_texts[run_of[pos]] += span.replacement
            pos = span.end
        else:
            new_texts[run_of[pos]] += text[pos]
            pos += 1

    for run, new_text in zip(runs, new_texts):
        if run.text != new_text:
            run.text = new_text


HEADER_FOOTER_VARIANTS = (
    ("header", "页眉"),
    ("footer", "页脚"),
    ("first_page_header", "首页页眉"),
    ("first_page_footer", "首页页脚"),
    ("even_page_header", "偶数页页眉"),
    ("even_page_footer", "偶数页页脚"),
)


def iter_document_paragraphs(doc):
    """按文档顺序产出 (段落, 位置描述)，覆盖正文、表格、文本框、页眉页脚。"""
    # 用 dict 持有元素引用：lxml 代理对象被回收后 id 会复用，不能只存 id。
    seen: dict[int, object] = {}

    def emit(element, where: str):
        for p_element in element.iter(qn("w:p")):
            key = id(p_element)
            if key in seen:
                continue
            seen[key] = p_element
            yield Paragraph(p_element, None), where

    yield from emit(doc.element.body, "正文")

    for section in doc.sections:
        for attribute, label in HEADER_FOOTER_VARIANTS:
            part = getattr(section, attribute)
            if part.is_linked_to_previous:
                continue
            yield from emit(part._element, label)


# ---------------------------------------------------------------------------
# 代号分配
# ---------------------------------------------------------------------------


def alpha_label(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA。"""
    result = ""
    value = index + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def assign_codes(plans) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    ordered: dict[str, dict[str, None]] = {}
    for plan in plans:
        for match in plan.matches:
            ordered.setdefault(match.group, {})
            ordered[match.group].setdefault(match.value, None)

    code_by_key: dict[tuple[str, str], str] = {}
    for group, values in ordered.items():
        items = list(values)
        if group == "company":
            for index, value in enumerate(items):
                code_by_key[(group, value)] = "【公司%s】" % alpha_label(index)
        elif group == "person":
            for index, value in enumerate(items):
                code_by_key[(group, value)] = "【姓名%s】" % alpha_label(index)
        elif group == "customer":
            for index, value in enumerate(items):
                code_by_key[(group, value)] = "【客户%s】" % alpha_label(index)
        elif group == "supplier":
            for index, value in enumerate(items):
                code_by_key[(group, value)] = "【供应商%s】" % alpha_label(index)
        else:
            base = TYPED_BASE[group]
            for index, value in enumerate(items):
                suffix = "" if len(items) == 1 else str(index + 1)
                code_by_key[(group, value)] = "【%s%s】" % (base, suffix)

    mapping: dict[str, str] = {}
    for plan in plans:
        for match in plan.matches:
            code = code_by_key[(match.group, match.value)]
            mapping.setdefault(code, match.value)
    return code_by_key, mapping


# ---------------------------------------------------------------------------
# 文档属性
# ---------------------------------------------------------------------------

APP_COMPANY_RE = re.compile(rb"<Company(?:\s[^>]*)?(?:/>|>.*?</Company>)", re.S)
APP_COMPANY_TEXT_RE = re.compile(rb"<Company(?:\s[^>]*)?>(.*?)</Company>", re.S)


def _clear_app_company(doc) -> str:
    """清空 docProps/app.xml 的 Company（用字节替换，避免解析任意 XML）。"""
    for part in doc.part.package.iter_parts():
        if str(part.partname) != "/docProps/app.xml":
            continue
        blob = part.blob
        match = APP_COMPANY_RE.search(blob)
        if match is None:
            return ""
        text_match = APP_COMPANY_TEXT_RE.search(match.group(0))
        old = ""
        if text_match is not None:
            old = html.unescape(text_match.group(1).decode("utf-8", "replace")).strip()
        part._blob = blob[: match.start()] + b"<Company></Company>" + blob[match.end():]
        return old
    return ""


def clear_metadata(doc) -> dict[str, str]:
    properties = doc.core_properties
    info = {
        "author": properties.author or "",
        "last_modified_by": properties.last_modified_by or "",
        "company": "",
    }
    properties.author = ""
    properties.last_modified_by = ""
    info["company"] = _clear_app_company(doc)
    return info


# ---------------------------------------------------------------------------
# 包内风险检查
# ---------------------------------------------------------------------------


def inspect_package(path: str) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        xml_names = [
            name for name in names
            if name.startswith("word/") and name.endswith(".xml")
            and (
                name == "word/document.xml"
                or name.startswith("word/header")
                or name.startswith("word/footer")
            )
        ]
        document_xml = b"".join(archive.read(name) for name in xml_names)
        return {
            "comments": any(name.startswith("word/comments") for name in names),
            "media": [name for name in names if name.startswith("word/media/")],
            "embeddings": [name for name in names if name.startswith("word/embeddings/")],
            "footnotes": "word/footnotes.xml" in names,
            "endnotes": "word/endnotes.xml" in names,
            "textboxes": b"<w:txbxContent" in document_xml,
            "revisions": b"<w:ins " in document_xml or b"<w:del " in document_xml,
        }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


@dataclass
class ParagraphPlan:
    paragraph: Paragraph
    runs: list[Run]
    text: str
    matches: list[RawMatch]
    where: str


def collect_plans(doc, collector: CandidateCollector | None = None) -> list[ParagraphPlan]:
    plans: list[ParagraphPlan] = []
    index = 0
    for paragraph, where in iter_document_paragraphs(doc):
        runs = direct_runs(paragraph)
        if not runs:
            continue
        text = paragraph_text(runs)
        if not text:
            continue
        index += 1
        scan_candidates(text, "第 %d 段（%s）" % (index, where), collector)
        matches = find_matches(text)
        if matches:
            plans.append(ParagraphPlan(paragraph, runs, text, matches, where))
    return plans


def build_report(
    source: str,
    output: str,
    map_path: str,
    plans: list[ParagraphPlan],
    metadata: dict[str, str],
    package: dict[str, object],
    warnings: list[str],
    candidate_summary: str = "",
    candidate_reminder: str = "",
) -> str:
    counts: dict[str, dict[str, int]] = {}
    values_by_group: dict[str, set[str]] = {}
    header_hits: dict[str, int] = {}
    for plan in plans:
        for match in plan.matches:
            entry = counts.setdefault(match.group, {"hits": 0, "entities": 0})
            entry["hits"] += 1
            values_by_group.setdefault(match.group, set()).add(match.value)
            if plan.where != "正文":
                header_hits[plan.where] = header_hits.get(plan.where, 0) + 1

    for group, entry in counts.items():
        entry["entities"] = len(values_by_group[group])

    lines = [
        "脱敏报告",
        "========",
        "源文件：%s" % source,
        "输出文件：%s" % output,
        "映射表：%s（权限 600，内含原文，切勿外发）" % map_path,
        "生成时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "",
        "命中统计：",
    ]
    if counts:
        for group in GROUP_LABELS:
            if group not in counts:
                continue
            entry = counts[group]
            lines.append(
                "  %s：%d 处 / %d 个实体"
                % (GROUP_LABELS[group], entry["hits"], entry["entities"])
            )
        lines.append(
            "  合计：%d 处 / %d 个实体"
            % (
                sum(entry["hits"] for entry in counts.values()),
                sum(entry["entities"] for entry in counts.values()),
            )
        )
    else:
        lines.append("  无（规则没有命中任何目标；也可能是格式不在覆盖范围内）")

    lines.append("")
    if header_hits:
        lines.append(
            "页眉页脚命中："
            + "；".join("%s %d 处" % (name, count) for name, count in header_hits.items())
        )
    else:
        lines.append("页眉页脚命中：无")

    lines.append("")
    lines.append("候选词（词表外实体）：")
    lines.append("  " + (candidate_summary or "未收集"))
    if candidate_reminder:
        lines.append("  " + candidate_reminder)

    lines.append("")
    lines.append("文档属性（已在输出中清空）：")
    lines.append("  作者：%s" % (metadata.get("author") or "（无）"))
    lines.append("  最后修改者：%s" % (metadata.get("last_modified_by") or "（无）"))
    lines.append("  公司：%s" % (metadata.get("company") or "（无）"))

    lines.append("")
    lines.append("提醒：")
    if package.get("comments"):
        lines.append("  - 文档含批注（word/comments.xml）。批注正文不会出现在页面正文里，但原话仍在文件中，外发前请删除批注。")
    if package.get("revisions"):
        lines.append("  - 文档含修订痕迹（插入/删除）。被删除的原文可能仍保存在 XML 里，请先接受或拒绝全部修订。")
    if package.get("media"):
        lines.append("  - 文档含 %d 个图片/媒体文件，图片里的文字（扫描件、截图、营业执照）不会被本工具处理。" % len(package["media"]))
    if package.get("embeddings"):
        lines.append("  - 文档含 %d 个嵌入对象/附件，内部数据不会被本工具处理。" % len(package["embeddings"]))
    if package.get("textboxes"):
        lines.append("  - 文档含文本框。文本框正文会被扫描，但组合形状、艺术字等特殊容器建议人工复核。")
    if package.get("footnotes"):
        lines.append("  - 文档含脚注，脚注正文不在本次扫描范围内。")
    if package.get("endnotes"):
        lines.append("  - 文档含尾注，尾注正文不在本次扫描范围内。")
    for warning in warnings:
        lines.append("  - %s" % warning)
    if not any(
        package.get(key)
        for key in ("comments", "revisions", "media", "embeddings", "textboxes", "footnotes", "endnotes")
    ) and not warnings:
        lines.append("  - 未发现批注、修订、图片、附件等额外风险。")
    lines.append("  - 规则识别不保证遮干净，外发前请按下面的清单人工复核。")

    lines.append("")
    lines.append("人工复核清单：")
    lines.append("  [ ] 手写签名、盖章、扫描签名")
    lines.append("  [ ] 图片中的文字（身份证照片、营业执照、聊天截图、Logo）")
    lines.append("  [ ] 附件与嵌入对象（OLE、嵌入的 Excel/PDF）")
    lines.append("  [ ] 文本框、艺术字、SmartArt、组合形状")
    lines.append("  [ ] 批注、修订记录（删除的文字仍可能留在文件里）")
    lines.append("  [ ] 脚注、尾注、域代码")
    lines.append("  [ ] 页眉页脚（本工具已扫，仍建议肉眼确认）")
    lines.append("  [ ] 无标签的姓名、不带公司后缀的主体")
    lines.append("  [ ] 带空格/横线/括号的号码，以及不完整地址")
    lines.append("  [ ] 其他文档属性（标题、主题、自定义属性）")
    lines.append("")

    return "\n".join(lines)


def write_audit(source: str, output: str, mapping: dict, plans, mask_only: bool) -> None:
    """一行一条审计：谁处理了什么、遮了几处、用的什么模式。只记元数据，不记原文。"""
    import hashlib
    from datetime import datetime

    home = os.environ.get("CONTRACT_REDACT_HOME") or os.path.join(os.path.expanduser("~"), ".contract-redact")
    os.makedirs(home, mode=0o700, exist_ok=True)
    path = os.path.join(home, "audit.log")

    counts: dict[str, int] = {}
    for plan in plans:
        for match in plan.matches:
            counts[match.group] = counts.get(match.group, 0) + 1

    entry = {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": os.path.abspath(source),
        "output": os.path.abspath(output),
        "mode": "mask-only" if mask_only else "reversible",
        "entities": counts,
        "mapping_sha256": hashlib.sha256(
            json.dumps(mapping, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16] if mapping else None,
        "rules": sorted(ENABLED_GROUPS) if ENABLED_GROUPS is not None else "all",
    }
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    os.chmod(path, 0o600)


def write_private(path: str, content: str) -> None:
    """写文件并强制 600 权限（映射表和报告都可能含原文）。"""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.chmod(path, 0o600)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="中文合同脱敏（本地规则版）")
    parser.add_argument("input", help="输入的 .docx 文件")
    parser.add_argument(
        "-o", "--output",
        help="输出的脱敏文件（默认：原文件名 + .redacted.docx）",
    )
    parser.add_argument(
        "--map",
        dest="map_path",
        help="映射表 JSON 路径（默认：输出文件名 + .map.json）",
    )
    parser.add_argument(
        "--report",
        dest="report_path",
        help="报告文件路径（报告同时打印到终端）",
    )
    parser.add_argument(
        "--config",
        dest="config_path",
        help="补充词表 JSON（公司简称、客户代号、不该遮的白名单）",
    )
    parser.add_argument(
        "--candidates",
        dest="candidates_path",
        help="候选词 JSON 路径（默认：输出文件名 + .candidates.json，权限 600）",
    )
    parser.add_argument(
        "--no-candidates",
        dest="no_candidates",
        action="store_true",
        help="不收集词表外实体候选",
    )
    parser.add_argument(
        "--only",
        help="只脱敏这些类别，逗号分隔：company,person,phone,id,credit,bank,address,email,customer,supplier,password",
    )
    parser.add_argument(
        "--skip",
        help="跳过这些类别（与 --only 二选一）",
    )
    parser.add_argument(
        "--mask-only",
        action="store_true",
        help="不可逆掩码：换成 138****5678 这种写法，不生成映射表，无法还原",
    )
    parser.add_argument(
        "--passphrase",
        help="给映射表加口令（也可用环境变量 CONTRACT_REDACT_PASSPHRASE）",
    )
    parser.add_argument(
        "--no-audit",
        action="store_true",
        help="不写审计日志（默认写在 ~/.contract-redact/audit.log）",
    )
    parser.add_argument(
        "--ocr-engine",
        choices=["vision", "rapidocr"],
        help="扫描件用哪个 OCR：vision 仅 macOS；rapidocr 跨平台。默认自动选可用的",
    )
    args = parser.parse_args(argv)
    if args.candidates_path and args.no_candidates:
        parser.error("--candidates 和 --no-candidates 不能同时使用")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    global MASK_ONLY
    MASK_ONLY = bool(getattr(args, "mask_only", False))
    if MASK_ONLY and getattr(args, "passphrase", None):
        print("提示：掩码模式不生成映射表，--passphrase 会被忽略", file=sys.stderr)
    try:
        set_enabled(getattr(args, "only", None), getattr(args, "skip", None))
    except SystemExit as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 2
    if args.config_path:
        try:
            load_config(os.path.abspath(args.config_path))
        except Exception as exc:
            print("错误：配置文件读取失败：%s" % exc, file=sys.stderr)
            return 2
    source = os.path.abspath(args.input)

    if not os.path.isfile(source):
        print("错误：找不到文件 %s" % source, file=sys.stderr)
        return 2

    ext = os.path.splitext(source)[1].lower()
    supported = {".docx", ".doc", ".md", ".markdown", ".txt", ".xlsx", ".xlsm", ".pdf"}
    if ext not in supported:
        print(
            "错误：不支持的格式 %s（支持 .docx / .doc / .pdf / .md / .txt / .xlsx）" % (ext or "无扩展名"),
            file=sys.stderr,
        )
        return 2

    if args.output:
        output = os.path.abspath(args.output)
    else:
        root, _ = os.path.splitext(source)
        out_ext = ".docx" if ext in (".docx", ".doc") else ext
        output = root + ".redacted" + out_ext
    if output == source:
        print("错误：输出文件不能和源文件相同，避免覆盖原件", file=sys.stderr)
        return 2

    map_path = os.path.abspath(
        args.map_path or (os.path.splitext(output)[0] + ".map.json")
    )
    report_path = os.path.abspath(args.report_path) if args.report_path else None
    candidates_path = None
    if not args.no_candidates:
        candidates_path = os.path.abspath(
            args.candidates_path or (os.path.splitext(output)[0] + ".candidates.json")
        )

    artifacts = [("输出文件", output), ("映射表", map_path)]
    if report_path:
        artifacts.append(("报告", report_path))
    if candidates_path:
        artifacts.append(("候选词文件", candidates_path))
    if args.config_path:
        artifacts.append(("词表", os.path.abspath(args.config_path)))
    seen_paths: dict[str, str] = {}
    for label, path in artifacts:
        resolved = os.path.realpath(path)
        if resolved == os.path.realpath(source):
            print("错误：%s不能覆盖源文件" % label, file=sys.stderr)
            return 2
        if resolved in seen_paths:
            print(
                "错误：%s和%s指向同一个文件，请换一个路径"
                % (label, seen_paths[resolved]),
                file=sys.stderr,
            )
            return 2
        seen_paths[resolved] = label

    # ---- 非 docx：.doc 先转 docx，其余交给 formats.py ----
    if ext == ".doc":
        import subprocess
        import tempfile
        import shutil

        tmp_dir = tempfile.mkdtemp(prefix="redact-doc-")
        tmp_docx = os.path.join(tmp_dir, os.path.splitext(os.path.basename(source))[0] + ".docx")
        if shutil.which("textutil"):
            conv = subprocess.run(
                ["textutil", "-convert", "docx", "-output", tmp_docx, source],
                capture_output=True,
                text=True,
            )
        elif shutil.which("soffice") or shutil.which("libreoffice"):
            exe = shutil.which("soffice") or shutil.which("libreoffice")
            conv = subprocess.run(
                [exe, "--headless", "--convert-to", "docx", "--outdir", tmp_dir, source],
                capture_output=True,
                text=True,
            )
            produced = os.path.join(tmp_dir, os.path.splitext(os.path.basename(source))[0] + ".docx")
            if os.path.isfile(produced) and not os.path.isfile(tmp_docx):
                os.rename(produced, tmp_docx)
        else:
            print(
                "错误：这台机器没有 textutil（macOS）也没有 LibreOffice，"
                "无法自动转换 .doc。请先手动另存为 .docx 再跑。",
                file=sys.stderr,
            )
            return 2
        if conv.returncode != 0 or not os.path.isfile(tmp_docx):
            print(
                "错误：.doc 转 .docx 失败。\n%s"
                % (conv.stderr or "").strip(),
                file=sys.stderr,
            )
            return 2
        forwarded = [tmp_docx, "-o", output, "--map", map_path]
        if report_path:
            forwarded += ["--report", report_path]
        if args.config_path:
            forwarded += ["--config", args.config_path]
        if args.no_candidates:
            forwarded += ["--no-candidates"]
        elif args.candidates_path:
            forwarded += ["--candidates", candidates_path]
        if args.ocr_engine:
            forwarded += ["--ocr-engine", args.ocr_engine]
        print("提示：.doc 已转为 .docx 处理，输出是 .docx，排版可能有细微差异。")
        code = main(forwarded)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return code

    if ext != ".docx":
        from formats import redact_any

        return redact_any(
            source, output, map_path, report_path,
            args.config_path, args.ocr_engine, candidates_path,
        )

    doc = Document(source)
    warnings: list[str] = []
    metadata = clear_metadata(doc)
    collector = CandidateCollector(config_terms()) if candidates_path else None
    plans = collect_plans(doc, collector)
    code_by_key, mapping = assign_codes(plans)

    for plan in plans:
        spans = []
        for match in plan.matches:
            replacement = (
                mask_value(match.value)
                if MASK_ONLY
                else code_by_key[(match.group, match.value)]
            )
            spans.append(Span(match.start, match.end, replacement))
        apply_spans(plan.runs, plan.text, spans)

    doc.save(output)

    if MASK_ONLY:
        mapping = {}
        if os.path.exists(map_path):
            try:
                os.chmod(map_path, 0o600)
            except OSError:
                pass
    else:
        passphrase = args.passphrase or os.environ.get("CONTRACT_REDACT_PASSPHRASE")
        from mapping_crypto import save_mapping

        save_mapping(map_path, mapping, passphrase)

    if not args.no_audit:
        write_audit(source, output, mapping, plans, MASK_ONLY)

    candidate_summary, candidate_reminder = finalize_candidates(
        collector, candidates_path, source,
    )

    package = inspect_package(output)
    report = build_report(
        source, output, map_path, plans, metadata, package, warnings,
        candidate_summary=candidate_summary,
        candidate_reminder=candidate_reminder,
    )
    if report_path:
        write_private(report_path, report + "\n")

    print(report)
    if report_path:
        print("报告文件：%s" % report_path)
    return 0


if __name__ == "__main__":
    # 直接跑脚本时，formats/ocr 里 `import redact` 会得到另一份模块副本，
    # 共享不到 MASK_ONLY / ENABLED_GROUPS 这类开关；这里把正在运行的模块登记进去。
    sys.modules.setdefault("redact", sys.modules[__name__])
    sys.exit(main())
