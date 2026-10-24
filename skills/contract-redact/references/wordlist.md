# 客户与供应商名单

合同里最难靠格式认出来的是客户代号和供应商名——`ACME`、`NORTHWIND` 这种两三字母的代号，光看形状判断不了是公司。名单要交给词表。

## 词表结构

```json
{
  "extra_orgs": ["星海集团"],
  "extra_customers": ["ACME 工业有限公司", "北方传动有限公司"],
  "extra_suppliers": ["星海电子"],
  "aliases": {"ACME": ["ACME 工业有限公司"]},
  "keep": ["市仲裁委员会", "中国工商银行", "TÜV SÜD", "CNAS"]
}
```

| 字段 | 作用 |
| --- | --- |
| `extra_orgs` / `extra_customers` / `extra_suppliers` / `extra_people` | 必须遮、但规则认不出的词；客户编【客户A】，供应商编【供应商A】 |
| `aliases` | 同一家的多种写法绑成一个代号；只归到本来就有这家的那张表 |
| `keep` | 公共机构、银行、认证机构——遮了反而看不懂，放过 |

## 同步

名单多了就同步，不用手改：

```bash
python tools/sync_terms.py --config vocab.json --kind customer \
    --csv 客户名单.csv --column 客户名称 --short-column 客户简称
python tools/sync_terms.py --config vocab.json --kind supplier \
    --csv 供应商名单.csv --column 供应商名称
```

- 只增不减，保留手写的 `keep`、别名和其他字段；
- 每次打印新增了谁；重复跑不会重复；
- 挂到定时任务（cron / 计划任务）就自动发现新客户。

## 相近写法怎么处理

| 正文写法 | 词表有 | 结果 |
| --- | --- | --- |
| `星海` | `星海` | 直接换 |
| `芜湖星海精密制造有限公司` | 只有 `星海` | 自动扩成完整主体，整串换成一个代号 |
| `某某科技有限公司`（完全没有） | — | 按规则遮掉，同时记进 `*.candidates.json` |
| `星海` vs `星河`、拼音 | — | 不自动认，进候选清单人工确认 |

## 候选回收

```bash
python redact.py 合同.docx -o 合同_脱敏.docx --candidates 合同.candidates.json
python tools/merge_candidates.py 合同.candidates.json --config vocab.json --only org,people
```

候选文件是**未脱敏原文**：600 权限，不外发，确认后并入或删掉。

## 短代号

纯英文、≤4 个字符的代号（`MS`、`ACME`）按词边界匹配，嵌在别的单词里不算，避免误伤。
