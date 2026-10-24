---
name: contract-redact
description: 中文合同本地脱敏：换成可还原代号供 AI 审查，或掩码后对外发送。
version: 1.0.0
author: Kelin Pan (kelinpan0524-cell), Hermes Agent
license: MIT
platforms: [macos, linux, windows]
metadata:
  hermes:
    tags: [脱敏, 合同, 隐私, 中文, 可逆]
    related_skills: []
---

# 中文合同脱敏

把一个中文合同里「能指认是谁」的信息在**本机**换成代号，再把代号版交给 AI 审查；映射表留在本地，审完换回来。
纯规则引擎（正则 + 校验位 + 词表），不联网、不调模型。支持 docx / doc / pdf / xlsx / md / txt，扫描件走 OCR。

不负责：手写签名、盖章、图片里的字、附件与嵌入对象。也不保证遮干净——见 [已知限制](references/limits.md)。

## When to Use

- 「这份要**发外审**」「发给 AI 看看」「帮我审一下这合同」→ 可逆模式
- 「这份**发给客户**」「对外发」「给供应商看」→ 掩码模式
- 「脱敏一下」「别把客户名带出去」「把手机号遮掉」

不用的情况：只在本机自己看、不出门；文件已经发出去过（直接提醒风险，别再脱敏）。

## Prerequisites

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt      # 按系统自动选依赖（含 OCR）
```

- `.doc` 转换：macOS 自带 `textutil`；Linux / Windows 需要 LibreOffice。
- 扫描件 OCR：macOS 用系统 Vision（零下载）；其它系统装 `rapidocr-onnxruntime`。

## How to Run

```bash
# 可逆：代号版 + 映射表
python redact.py 合同.docx -o 合同_脱敏.docx
python restore.py 合同_脱敏.docx --map 合同_脱敏.map.json -o 合同_还原.docx

# 掩码：留首尾打星，不生成映射表
python redact.py 合同.pdf -o 合同_对外.pdf --mask-only

# 带客户/供应商名单，并给映射表加密
python redact.py 合同.docx -o 合同_脱敏.docx --config vocab.json --passphrase '口令'
```

## Quick Reference

| 需求 | 参数 |
| --- | --- |
| 只遮某几类 | `--only phone,id,password` |
| 跳过某几类 | `--skip address,person` |
| 不可逆掩码 | `--mask-only` |
| 映射表口令 | `--passphrase '口令'`，或环境变量 `CONTRACT_REDACT_PASSPHRASE` |
| 补充词表 | `--config vocab.json` |
| 候选词回收 | `--candidates 候选.json`，再 `tools/merge_candidates.py` 并入 |
| 不写审计 | `--no-audit` |
| 指定 OCR | `--ocr-engine vision\|rapidocr` |

## Procedure

1. **选模式**：要还原→默认；对外不回收→`--mask-only`。完成标准：能一句话说清这版能不能还原。
2. **确认格式**：docx/doc/pdf/xlsx/md/txt 直接跑；扫描件先说明「走 OCR，版式会变」。
   完成标准：扩展名在支持列表内，或已明确告知不支持。
3. **跑脱敏**：命中统计会打印出来。完成标准：输出文件存在，报告里有各类命中数。
4. **读报告**：批注、修订、页眉页脚命中、文档属性里的作者/公司；扫描件另有版式提醒。
   完成标准：每条提醒都转述给用户，不吞掉。
5. **人工复核**：搜甲乙方真名、法人姓名、手机号；删批注、定了修订；看页眉页脚、文本框、附件、图片。
   完成标准：用户明确说「看过了」或「可以发」。
6. **交付**：只发脱敏件；映射表和候选词文件留在本机。还原后看恢复报告（哪些代号没用上、有没有陌生代号）。

## Pitfalls

- **映射表就是原文**：不进 docx、不上传、不贴进对话、不跟文件一起发；丢了还原不回来。
- **候选词文件也是原文**：`*.candidates.json` 权限 600，不外发；确认后并入词表或删掉。
- **掩码模式不可逆**：别承诺能还原，也别生成映射表。
- **OCR 会认错数字**：扫描件的金额、账号必须肉眼核对。
- **简称与全称**：词表里只有「星海」时，「芜湖星海精密制造有限公司」会自动扩成完整主体；字不一样的（和田、拼音）不硬认，进候选清单。
- **还原按词表规范写法回填**：一个代号只存一份原文。
- **金额/单价/比例/条款号默认不动**：合同审查要看这些。

## Verification

```bash
python tests/check.py          # docx：无残留 / 金额不动 / 还原逐字一致
python tests/check_multi.py    # 六种输入（含扫描件）脱敏 + 还原
python tests/check_modes.py    # 规则开关 / 掩码 / 口令 / 恢复报告 / 审计
python tests/check_skill.py    # 本技能自身：frontmatter、链接、篇幅
```

全绿才算改完；任何一项失败，先修再交付。

## References

- [两种模式与参数](references/modes.md) — 可逆 / 掩码、规则开关、口令、审计
- [格式与 OCR](references/formats.md) — 各格式行为、扫描件、跨平台差异
- [客户与供应商名单](references/wordlist.md) — 同步、别名、包含式扩展、候选回收
- [已知限制与复核清单](references/limits.md) — 会漏什么、外发前必须看什么
