#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 tests/sample.docx：一份完全虚构的中文合同样例。

覆盖：公司名、人名、手机、座机、身份证、统一社会信用代码、银行账号、
邮箱、完整地址、金额/比例/期限/条款号，以及表格、页眉、页脚、跨 run 文本。
"""

from __future__ import annotations

import os
import re
import sys
from xml.sax.saxutils import escape as xml_escape

from docx import Document
from docx.shared import Pt

HERE = os.path.dirname(os.path.abspath(__file__))
APP_COMPANY_RE = re.compile(rb"<Company(?:\s[^>]*)?(?:/>|>.*?</Company>)", re.S)

# 虚构数据
COMPANY_A = "北辰智联科技有限公司"
COMPANY_B = "上海蓝海精密机械制造有限公司"
PERSON_LEGAL = "张伟"
PERSON_CONTACT = "李静"
PERSON_AGENT = "王小明"
MOBILE = "13812345678"
LANDLINE = "010-87654321"
EMAIL = "li.jing@example.com"
ID_NUMBER = "110105199003071239"  # GB 11643 校验位有效
USCC_A = "91110105MA01ABCD2X"
USCC_B = "91310115MA1K4ABC7Q"
BANK_ACCOUNT = "6222021234567890123"
ADDRESS = "北京市朝阳区建国路88号院3号楼501室"


def set_app_company(doc, company: str) -> None:
    for part in doc.part.package.iter_parts():
        if str(part.partname) != "/docProps/app.xml":
            continue
        blob = part.blob
        match = APP_COMPANY_RE.search(blob)
        if match is None:
            return
        value = xml_escape(company).encode("utf-8")
        part._blob = blob[: match.start()] + b"<Company>" + value + b"</Company>" + blob[match.end():]
        return


def main() -> int:
    doc = Document()
    doc.core_properties.author = "合同管理员"
    doc.core_properties.last_modified_by = "法务部"
    doc.core_properties.title = "采购框架合同（虚构样例）"
    set_app_company(doc, COMPANY_A)

    title = doc.add_paragraph()
    title_run = title.add_run("采购框架合同")
    title_run.bold = True
    title_run.font.size = Pt(16)
    doc.add_paragraph("合同编号：HT-2026-0417（虚构）")

    # 甲方：故意拆成两个 run，验证跨 run 替换
    party_a = doc.add_paragraph()
    party_a.add_run("甲方：北京华信智")
    bold_tail = party_a.add_run("联科技有限公司")
    bold_tail.bold = True

    doc.add_paragraph("统一社会信用代码：%s" % USCC_A)
    doc.add_paragraph("地址：%s" % ADDRESS)

    # 人名也拆 run
    legal = doc.add_paragraph()
    legal.add_run("法定代表人：张")
    legal.add_run("伟").bold = True

    doc.add_paragraph(
        "联系人：%s    手机：%s    座机：%s"
        % (PERSON_CONTACT, MOBILE, LANDLINE)
    )
    doc.add_paragraph("乙方：%s" % COMPANY_B)
    doc.add_paragraph("统一社会信用代码：%s" % USCC_B)
    doc.add_paragraph("注册地址：江苏省苏州市姑苏区人民路199号")
    doc.add_paragraph("委托代理人：%s" % PERSON_AGENT)
    doc.add_paragraph("邮箱：%s" % EMAIL)
    doc.add_paragraph("法定代表人身份证：%s" % ID_NUMBER)

    doc.add_paragraph("第一条 合同金额与付款")
    doc.add_paragraph(
        "1.1 本合同含税总价为人民币 1,000,000.00 元"
        "（大写：壹佰万元整）。"
    )
    doc.add_paragraph("1.2 预付款比例为 30%，余款在验收后支付。")
    doc.add_paragraph("1.3 收款账户：%s" % BANK_ACCOUNT)
    doc.add_paragraph("第二条 交付与验收")
    doc.add_paragraph(
        "2.1 乙方应在 2026 年 12 月 31 日前完成交付，履行期限 12 个月。"
    )
    doc.add_paragraph("2.2 具体验收标准见第 3.2 条。")
    doc.add_paragraph(
        "由%s负责交付，%s负责验收。" % (COMPANY_A, COMPANY_B)
    )

    table = doc.add_table(rows=3, cols=2)
    table.cell(0, 0).text = "签约主体"
    table.cell(0, 1).text = COMPANY_A
    table.cell(1, 0).text = "联系人：%s" % PERSON_CONTACT
    table.cell(1, 1).text = "手机：%s" % MOBILE
    table.cell(2, 0).text = "合同金额"
    table.cell(2, 1).text = "人民币 1,000,000.00 元"

    section = doc.sections[0]
    header = section.header
    header.is_linked_to_previous = False
    header.paragraphs[0].text = "%s · 合同文本" % COMPANY_A

    footer = section.footer
    footer.is_linked_to_previous = False
    footer.paragraphs[0].text = "地址：%s　电话：%s" % (ADDRESS, LANDLINE)

    output = os.path.join(HERE, "sample.docx")
    doc.save(output)

    print("已生成：%s" % output)
    print("虚构公司：%s / %s" % (COMPANY_A, COMPANY_B))
    print("虚构人名：%s / %s / %s" % (PERSON_LEGAL, PERSON_CONTACT, PERSON_AGENT))
    print("身份证：%s（校验位有效）" % ID_NUMBER)
    print("手机：%s  座机：%s" % (MOBILE, LANDLINE))
    print("信用代码：%s / %s" % (USCC_A, USCC_B))
    print("银行账号：%s" % BANK_ACCOUNT)
    print("地址：%s" % ADDRESS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
