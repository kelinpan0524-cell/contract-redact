# 两种模式与参数

## 可逆（默认）

```bash
python redact.py 合同.docx -o 合同_脱敏.docx
python restore.py 合同_脱敏.docx --map 合同_脱敏.map.json -o 合同_还原.docx
```

- 换成【公司A】【电话1】，全文同一实体同一代号；
- 生成映射表（600 权限），审完能换回来；
- 适合：发 AI 审查、需要把结论对回真人。

## 掩码（不可逆）

```bash
python redact.py 报价单.pdf -o 报价单_对外.pdf --mask-only
```

- 换成 `星*******公司`、`1********01`：留首尾，中间打星；
- **不生成映射表**，谁也还原不回来；
- 适合：发客户、对外材料、事后不需要回收。

## 规则开关

```bash
python redact.py 合同.docx -o 合同_脱敏.docx --only phone,id,password
python redact.py 合同.docx -o 合同_脱敏.docx --skip address,person
```

可用类别：`company` `person` `phone` `id` `credit` `bank` `address` `email` `customer` `supplier` `password`。
`--only` 与 `--skip` 二选一；两个都用时只认 `--only`。

## 映射表加密

```bash
python redact.py 合同.docx -o 合同_脱敏.docx --passphrase '口令'
python restore.py 合同_脱敏.docx --map 合同_脱敏.map.json --passphrase '口令' -o 还原.docx
```

- scrypt 派生密钥 → HMAC-SHA256 流加密 → HMAC 认证，纯标准库；
- 口令错或文件被改，解密直接报错，不会静默读错；
- 口令丢了映射表就废了——交付前讲清楚。

## 恢复报告

还原后自动打印：替换多少处、映射表里哪些代号没用上、文档里有没有不认识的代号。
`--report 还原报告.json` 可落盘。含义：

- **没用上的代号**：这段原文被删了，或这版文档没用到；
- **不认识的代号**：文档里出现了映射表没有的【】，多半是别的文件处理时留下的。

## 审计日志

每次脱敏往 `~/.contract-redact/audit.log`（600）追加一行 JSON：时间、源文件、输出、模式、各类命中数、映射表指纹、生效的规则。
只记元数据，不记原文。不要就加 `--no-audit`。
