# contract-redact

> 把中文合同里「能指认是谁」的信息在本机换成代号，再把代号版交给 AI 审查；审完用映射表换回来。

给 AI 审合同，最大的风险不是它审得不好，而是你在脱敏之前就把原件发出去了。常见的做法是「先上传，再让模型帮你打码」——那一步合同已经交出去了。

这个工具反过来做：**脱敏在本机完成，用正则加校验位，不联网、不调模型；出去的是代号版，原文和映射表都留在你自己电脑上。**

```
甲方：星海精密制造有限公司            甲方：【公司A】
法定代表人：陈志远                   法定代表人：【姓名A】
电话：13800000001                    电话：【电话1】
统一社会信用代码：91340200MA2T8K9L3D  统一社会信用代码：【信用代码1】
地址：江苏省苏州市工业园区星海路88号   地址：【地址A】

1.1 含税总价 1,234,567.89 元         1.1 含税总价 1,234,567.89 元      ← 不动
1.2 逾期违约金按未付金额的 20% 计      1.2 逾期违约金按未付金额的 20% 计   ← 不动
```

金额、比例、期限、条款号、日期一律不动——AI 要看的正是这些。被换掉的只有「这是谁、怎么联系、钱打到哪」。

## 它怎么工作

四步，全部在本机：

| 步骤 | 做法 | 说明 |
| --- | --- | --- |
| 1. 解析 | python-docx + 直接读 OOXML | `.docx` 其实是 zip 包，正文、表格、页眉页脚、文本框都拆成段落处理，原有格式保留 |
| 2. 识别 | 正则 + 校验位 + 后缀词典 + 上下文标签 | 手机、身份证、信用代码、账号靠格式与校验位；公司名靠后缀词；人名靠「法定代表人：」这类标签 |
| 3. 替换 | 一致性映射 | 同一实体全文共用一个代号，甲方出现 9 次都是【公司A】，AI 仍看得出谁对谁 |
| 4. 还原 | 映射表反向替换 | 审完把结论里的【公司A】换回真名 |

识别完还会顺手对照一遍词表：规则认得、但词表里没有的机构名和人名，会记进候选词文件。它不改变这次脱敏的结果，只是把「可能漏了谁」摆到你面前，确认后可以一键补进词表（见下面「客户与供应商名单」）。

**不用大模型、不装分词库、不调任何 API。** 好处是可解释、结果可复现、几秒出结果、断网也能跑；代价是只认「长得像」的东西，见下面的「已知限制」。

## 快速开始

```bash
git clone https://github.com/kelinpan0524-cell/contract-redact.git
cd contract-redact
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt      # 按系统自动装对应依赖
```

```bash
# 1. 脱敏（格式自动识别）
python redact.py 采购合同.docx -o 采购合同_脱敏.docx
python redact.py 报价单.pdf   -o 报价单_脱敏.pdf
python redact.py 明细.xlsx    -o 明细_脱敏.xlsx
python redact.py 备忘.md      -o 备忘_脱敏.md

# 2. 把脱敏版发给 AI 审

# 3. 审完，把结论里的代号换回真名（同格式）
python restore.py 采购合同_脱敏.docx --map 采购合同_脱敏.map.json -o 采购合同_还原.docx
```

| 输入格式 | 输出 | 说明 |
| --- | --- | --- |
| `.docx` | `.docx` | 正文、表格、页眉页脚、文本框；原格式保留 |
| `.doc` | `.docx` | macOS 用 `textutil`，Linux/Windows 用 LibreOffice 转 docx 再处理；输出是 docx，排版可能有细微差异 |
| `.pdf` | `.pdf` | 文字层做真擦除（不是盖白块）再写代号；扫描件用本机 OCR 识别后按行重写 |
| `.xlsx` / `.xlsm` | 同格式 | 单元格、批注、隐藏工作表；公式不动，图片/图表里的字不处理 |
| `.md` / `.txt` | 同格式 | 纯文本替换，编码跟随原文件 |

跑完生成三个文件（候选词文件只在发现词表外实体时生成）：

| 文件 | 用途 | 注意 |
| --- | --- | --- |
| `采购合同_脱敏.docx` | 唯一可以发出去的版本 | 发之前按下面清单人工过一遍 |
| `采购合同_脱敏.map.json` | 代号 ↔ 原文 | **这就是原文**，权限自动设 600，绝不能跟着文件发出去 |
| `采购合同_脱敏.candidates.json` | 这次没被词表收录的机构名、人名（候选） | 里面也是没遮住的原文，权限自动设 600；确认后并入词表或删掉 |

## 词表里只有「星海」，合同里写「芜湖星海」，认不认？

分四种情况，工具都能给你一个明确结果：

| 正文里的写法 | 词表里有 | 结果 |
| --- | --- | --- |
| `星海` | `星海` | 直接换成【公司A】 |
| `星海精密制造有限公司` | 只有 `星海` | **自动扩成完整主体**再换，整串变【公司A】，不会只剩半截「芜湖…有限公司」 |
| `华信科技有限公司`（词表里完全没有） | 无 | 按规则遮掉，同时记进 `*.candidates.json`，确认后可一键并入词表 |
| `和田`、`HT`、拼音（字不一样） | 只有 `星海` | **不自动认**（硬认容易误伤），在候选清单里人工确认或加别名 |

几个要点：

- 同一个主体的不同写法，通过 `aliases` 绑成一个代号；扩出来的全称也归到同一个代号，不会一家编出两个号。
- 还原时按词表里的规范写法还原（`星海` 还是 `星海`，不会变成「星海精密制造有限公司」）。
- 候选清单里的名字是**未脱敏原文**，权限 600，只用来确认，不跟着脱敏件发出去。

把候选并进词表：

```bash
# 脱敏时顺手记下词表里没有的公司/人名
python redact.py 合同.docx -o 合同_脱敏.docx --config vocab.json --candidates 合同.candidates.json

# 确认后一键并入（只增不删，可只并某一类）
python tools/merge_candidates.py 合同.candidates.json --config vocab.json --only org,people
```

## 两种模式：能还原 / 不能还原

| | 可逆（默认） | 不可逆掩码 |
| --- | --- | --- |
| 命令行 | `redact.py 合同.docx -o 合同_脱敏.docx` | 加 `--mask-only` |
| 换成什么 | 【公司A】【电话1】 | `星*******公司`、`1********01` |
| 映射表 | 生成，可还原 | **不生成**，谁也还原不回来 |
| 适合 | 发 AI 审、要拿回结论 | 发客户、发外部、事后不需要回收 |

```bash
# 只遮某一类（其余原样留着）
python redact.py 合同.docx -o 合同_脱敏.docx --only phone,id,password
python redact.py 合同.docx -o 合同_脱敏.docx --skip address,person

# 不可逆掩码：给客户看的报价、对外材料
python redact.py 报价单.pdf -o 报价单_对外.pdf --mask-only

# 映射表加口令（scrypt 派生密钥 + HMAC 认证；口令错了直接报错）
python redact.py 合同.docx -o 合同_脱敏.docx --passphrase '你的口令'
python restore.py 合同_脱敏.docx --map 合同_脱敏.map.json --passphrase '你的口令' -o 还原.docx
```

还原时会给出报告：替换了多少处、映射表里哪些代号没用上（说明这段被删了）、文档里有没有不认识的代号（可能来自别的文件）。加 `--report 还原报告.json` 留档。

每次脱敏还会往 `~/.contract-redact/audit.log` 追加一行审计：时间、文件、各类各遮了几处、模式、映射表指纹。只记元数据，不记原文；不想写就加 `--no-audit`。

## 支持哪些平台（跨平台）

**全流程本地、零联网、不调模型。** 代码里没有任何网络请求（`requests`、`urllib`、`http`、`socket` 都没有），也不调用任何 AI 模型或云端 API；断网也能跑。数字、金额、条款号靠格式和校验位识别，不需要联网理解语义。

功能三端一致：`.docx` `.doc` `.pdf` `.xlsx` `.md` `.txt` 全都能脱敏和还原，扫描件也都能 OCR。差别只在用哪套引擎。

| | macOS | Linux | Windows |
| --- | --- | --- | --- |
| 文档处理（docx / xlsx / pdf / md / txt） | ✅ | ✅ | ✅ |
| 扫描件 OCR | 系统 Vision，零下载，约 0.4 秒/页 | 本地 RapidOCR（ONNX，模型随包，约 100MB，不联网），1–1.5 秒/页 | 同 Linux |
| `.doc` 转 `.docx` | 系统 `textutil`，开箱可用 | LibreOffice（`soffice`） | LibreOffice |
| 安装 | `pip install -r requirements.txt` | 同上 + 需要转 .doc 时装 LibreOffice | 同上 + 装 LibreOffice |

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # 按系统自动装对应依赖
```

- OCR 引擎自动选，也可以 `--ocr-engine vision|rapidocr` 指定；两个都没装时，扫描件会跳过并在报告里写明。
- RapidOCR 的模型随 pip 包一起装好，运行时不再下载；整个过程不需要网络。
- PDF 里写代号用的是 PyMuPDF 自带的 CJK 字体，不依赖系统字体，三端排版一致。
- `.doc` 转换由程序自己调 `textutil` 或 `soffice`，不用你手动转；两样都没有时，会直接提示「先另存为 .docx」。

## 客户与供应商名单：从系统同步，自己补漏

合同里最难靠规则认出来的是「客户代号」和「供应商名称」——`ACME`、`NORTHWIND` 这种两三个字母的代号，光看格式判断不出来是公司。所以要把名单交给它。名单有两个来源：系统里已经登记的，靠定时同步；系统里没有的，靠脱敏时自动发现。

### 名单从哪来

名单来自客户自己的系统：飞书多维表格「售后中台」里的**客户等级表**和**供应商表**。导出成 CSV / JSON 后，交给 `tools/sync_terms.py` 合并进词表。它只读本地文件，不联网。

同步后词表里会多出这些内容：

| 来源（「售后中台」里的表） | 落到词表 | 效果 |
| --- | --- | --- |
| 客户等级表 | `extra_customers`，简称进 `aliases` | 客户全称、简称、英文代号都编成【客户A】 |
| 供应商表 | `extra_suppliers`，简称进 `aliases` | 供应商全称、简称都编成【供应商A】 |
| 两张表里的简称 | `aliases` | 同一家的全称和简称共用一个代号，不会一家编出两个号 |

```bash
# 客户：名称 + 简称一起同步，简称会自动建成别名
python tools/sync_terms.py --config vocab.json --kind customer \
    --csv 客户名单.csv --column 客户名称 --short-column 客户简称

# 供应商
python tools/sync_terms.py --config vocab.json --kind supplier \
    --csv 供应商名单.csv --column 供应商名称

# 先看看会新增哪些，不写文件
python tools/sync_terms.py ... --dry-run
```

每次跑都会打印新增了谁：

```
读入 12 个名字，词表原有 10 条，新增 2 条
  + 北方传动有限公司
  + NORTHWIND GmbH
已写入 vocab.json（extra_customers 共 12 条）
```

几条行为约定：

- 名单里的每个写法都进 `extra_customers` / `extra_suppliers`，编成【客户A】【供应商A】；
- 同一家的全称和简称用 `aliases` 绑成一个代号，不会一家编出两个号；
- 纯英文短代号（≤4 个字符，如 `MS`、`ACME`）按词边界匹配，嵌在别的单词里不算；
- 词表是敏感文件：里面有客户名单，权限 600，别提交到公开仓库。

### 怎么保持最新

把「导出 + 同步」挂成定时任务（每周一次；cron / Windows 计划任务 / CI 都行）。系统里新增客户或供应商，下一次同步就自动进词表，脱敏时立刻生效，不用改代码、不用手工维护。

同步只增不减：手写的 `keep`、`aliases` 和其他字段不会被冲掉。

### 自己发现新的：候选词闭环

系统名单覆盖的是已经登记过的客户和供应商；合同里还会出现没登记的机构、个人、合作方。这些名字规则可能认得出来，但词表里没有，以前你无从知道漏了谁。

现在脱敏时会顺手收集：

- 以「有限公司 / 有限责任公司 / 股份有限公司 / 集团 / 公司 / 厂 / 事务所 / 研究院 / 设计院 / 中心 / 合作社」结尾、但词表里没有的机构名；
- 「法定代表人 / 委托代理人 / 授权代表 / 联系人 / 签署人」等标签后面、但词表里没有的人名；
- 名字前面标着「客户：」「供应商：」的，分别归到 customer / supplier，其余归到 org。

结果写到输出文件旁边的 `*.candidates.json`（也可以用 `--candidates` 指定路径）：

```json
{
  "generated_at": "2026-10-24 16:00:00",
  "source": "/path/采购合同.docx",
  "warning": "本文件是未脱敏的原始名称，属于敏感信息；权限 600，切勿外发。确认后用 tools/merge_candidates.py 并入词表。",
  "summary": {"total": 2, "org": 1, "people": 1, "customer": 0, "supplier": 0},
  "candidates": [
    {"category": "org", "text": "某某精密制造有限公司", "count": 3, "first_seen": "第 5 段（正文）"},
    {"category": "people", "text": "某某某", "count": 1, "first_seen": "第 8 段（正文）"}
  ]
}
```

确认后一键并入词表：

```bash
# 先预览，不写文件
python tools/merge_candidates.py 采购合同_脱敏.candidates.json \
    --config vocab.json --dry-run

# 只并入机构和联系人
python tools/merge_candidates.py 采购合同_脱敏.candidates.json \
    --config vocab.json --only org,people

# 全部并入
python tools/merge_candidates.py 采购合同_脱敏.candidates.json --config vocab.json
```

合并只增不删：`keep`、`aliases`、`extra_*` 里的手写项和其他字段原样保留；已经在词表里的会跳过；重复跑不会重复加。并入后再脱敏，这些名字就会被正常替换，候选列表里也不会再出现——闭环就合上了。

如果分类不对（比如其实是客户），直接改候选文件里的 `category` 再合并。

**候选词文件是敏感文件。** 里面是没遮住的原始名称，和映射表一样：权限自动设 600，别外发、别贴进对话、别提交到仓库。确认完尽快并入词表或删掉。终端报告里也会打印一行摘要，并单独提醒这一点。

## 命令说明

```bash
python redact.py <输入> [-o 输出] [--map 映射表.json] [--report 报告.txt] [--config 词表.json]
                 [--candidates 候选词.json | --no-candidates] [--ocr-engine vision|rapidocr]
```

| 参数 | 不填时的默认 | 说明 |
| --- | --- | --- |
| `-o` | 原文件名 + `.redacted.docx` | 不能和源文件同名，避免覆盖原件 |
| `--map` | 输出文件名 + `.map.json` | 权限强制 600 |
| `--report` | 只打印到终端 | 填了就另存一份报告 |
| `--config` | 不加补充词表 | 公司简称、客户代号、白名单 |
| `--only` / `--skip` | 全开 | 按类别开关：company,person,phone,id,credit,bank,address,email,customer,supplier,password |
| `--mask-only` | 关 | 不可逆掩码，不生成映射表 |
| `--passphrase` | 关 | 给映射表加口令（也可用环境变量 `CONTRACT_REDACT_PASSPHRASE`） |
| `--no-audit` | 关 | 不写审计日志 |
| `--candidates` | 输出文件名 + `.candidates.json` | 候选词文件路径，权限强制 600 |
| `--no-candidates` | 不关 | 不收集候选词（批量跑、不想多一个敏感文件时用） |
| `--ocr-engine` | 自动选可用的 | `vision` 仅 macOS；`rapidocr` 跨平台 |

```bash
# 还原：只替换映射表里有的代号，映射表丢了就还原不回来
python restore.py 采购合同_脱敏.docx --map 采购合同_脱敏.map.json -o 采购合同_还原.docx

# 候选词并入词表（--dry-run 预览，--only 选分类）
python tools/merge_candidates.py 采购合同_脱敏.candidates.json --config vocab.json
```

## 会自动识别什么

| 类别 | 代号 | 怎么认出来的 |
| --- | --- | --- |
| 公司 / 机构 | 【公司A】【公司B】 | 以 有限公司、有限责任公司、股份有限公司、集团、公司、厂、事务所、研究院、设计院、中心、合作社 结尾，向前取全名，剥掉「甲方」「由」「与」这类前缀 |
| 人名 | 【姓名A】 | 法定代表人、委托代理人、授权代表、联系人、签署人、姓名、签字 之后的 2–4 个汉字；自动切掉「先生」「经理」「电话」这类尾随词 |
| 手机 | 【电话1】 | `1[3-9]` 开头 11 位，允许空格和横线 |
| 座机 | 【电话2】 | 区号-号码，允许全角横线、`转8001` 分机 |
| 身份证 | 【身份证1】 | 18 位 + GB 11643 校验位。校验位不对也按身份证处理（多半是打错一位），不会误当银行账号 |
| 统一社会信用代码 | 【信用代码1】 | 18 位，排除 I O S V Z，加权模 31 校验 |
| 银行账号 | 【银行账号1】 | 12–25 位连续数字，避开身份证和手机 |
| 邮箱 | 【邮箱1】 | 常规邮箱格式 |
| 地址 | 【地址1】 | 省/市 + 区/县 + 路/街/大道 + 号/室/楼 |
| 密码 / 密钥 | 【密码1】 | `密码：`、`password:`、`token=`、`api_key=` 后面那串值 |
| 客户代号 | 【客户A】 | 来自词表：客户全称、简称、英文代号（ACME、NORTHWIND 这类） |
| 供应商代号 | 【供应商A】 | 来自词表：供应商全称与简称 |
| 词表外实体候选 | 写入 `*.candidates.json` | 规则认得、但词表里没有的机构名（带后缀）和人名（带标签）；记录分类、出现次数、第一次出现的位置；不改变脱敏结果 |

扫描范围：正文段落、表格（含嵌套表格与合并单元格）、页眉页脚（主版本、首页、偶数页）、文本框里的段落。md/txt 按行扫，xlsx 按单元格和批注扫，PDF 按页扫（扫描件按 OCR 行扫）。

**顺带处理**：清空文档属性里的作者、最后修改者、公司（这三个字段经常写着真名和公司全称）；文档里有批注或修订痕迹时，在报告里单独提醒——不擅自改你的文件。候选词文件单独写、单独提醒，不混进映射表。

## 代号规则

同类只有一个值时写【电话】，出现多个不同号码才写【电话1】【电话2】。这样两个不同的号不会共用一个代号，还原时不会串号。

## 补充词表

规则认不出的写法——公司简称、客户英文代号、拼音——靠词表补，不用改代码。复制 `examples/config.example.json` 改成自己的：

```json
{
  "extra_orgs": ["星海集团", "星海实业有限公司"],
  "extra_customers": ["ACME", "NORTHWIND"],
  "extra_people": ["陈志远"],
  "extra_suppliers": ["某某电子"],
  "aliases": {
    "星海集团": ["星海", "星海实业", "星海控股"]
  },
  "keep": ["市仲裁委员会", "中国工商银行", "TÜV SÜD", "CNAS"]
}
```

| 字段 | 作用 |
| --- | --- |
| `extra_orgs` / `extra_customers` / `extra_suppliers` / `extra_people` | 必须遮、但规则认不出的词；客户编成【客户A】，供应商编成【供应商A】 |
| `aliases` | 同一家的多种写法共用一个代号，避免「星海」和「星海实业」被编成两个公司；只归到本来就有这家名字的那张表 |
| `keep` | 公共机构、银行、认证机构——遮了反而看不懂，直接放过 |

词表可以由系统名单定时同步（见上面），也可以手写；候选词一键合并进来的也会落在这几张表里。

## 已知限制

这是规则工具，不是语义理解。**它不保证遮干净**，以下情况会漏或误伤：

- 没有标签的人名（正文里直接写「张三代收」）、公司简称、拼音、英文名；
- 拆成两行的公司名、繁体字、全角数字、号码里加了别的分隔符；
- 15 位旧身份证、不完整的地址；
- 图片里的文字、手写签名、盖章、附件和嵌入对象；
- 批注原文、修订记录里「被删除」的文字（仍在文件 XML 里）；
- 反过来也会误伤：12–25 位的长编号可能被当成银行账号，像公司名但不是的短语可能被遮。

候选词也只覆盖「带公司后缀的机构名」和「带标签的人名」：没有后缀的简称、没有标签的姓名、拼音和英文名仍然会漏，候选列表里也不会有。候选是线索，不是保证——它只说明这些被规则认出来了但没进词表，不代表没出现在候选里的就安全。

任何格式都可能漏：PDF 的 OCR 有识别误差，扫描件版式会变，`.doc` 转换后排版也可能有细微差异。外发前一律人工复核。

## 外发前必须人工过一遍

- [ ] Word 里搜甲方/乙方真名、法人姓名、手机号，确认没有残留
- [ ] 删除全部批注，接受或拒绝全部修订
- [ ] 看页眉页脚、文本框、签名栏、附件和图片
- [ ] 确认映射表和候选词文件没有跟着文件一起发出去

这一步不能省。工具负责把机械的活干掉，最后一眼是人的责任。

## 常见问题

**会不会把合同传到网上？**
不会。代码里没有任何网络请求，`requests`、`urllib`、`http`、`socket` 一个都没有，断网也能跑。唯一的依赖是本地读写 docx 的库。候选词收集也只在本机做规则判断，不调模型、不发请求。

**候选词文件能发出去吗？**
不能。它和映射表一样，里面是没遮住的原始名称。权限自动设 600，确认完尽快用 `tools/merge_candidates.py` 并入词表，或者直接删掉。

**为什么不用大模型做脱敏？**
用云端模型脱敏，第一步就得把原件传上去，等于先泄露再补救。本地跑小模型也行，但合同里的关键实体是格式化的（身份证、信用代码、账号），正则加校验位比模型更准、更快、结果可复现。

**映射表丢了怎么办？**
还原不了。代号是单向映射，没有原文就没有对应关系。不需要还原时，确认脱敏件没问题后直接删掉映射表更安全。

**和市面上的脱敏软件比呢？**
商业软件通常做 PDF 涂黑和批量处理，界面完整；这个工具只做一件事：让中文合同能安全地发给 AI 审，并且能把结论还原回来。代码短，规则看得懂，词表可以自己改。

**扫描件怎么处理？**
PDF 里没有文字层的页面，会自动用本机 OCR 识别：macOS 用系统 Vision，Linux/Windows 用本地 RapidOCR（ONNX，模型随包，不联网）。识别后按行把原字擦掉、写入代号。识别完务必肉眼核对，尤其是金额和数字。

## 目录结构

```
redact.py                       脱敏主程序（docx 主流程 + 其它格式分发）
restore.py                      按映射表还原
formats.py                      pdf / xlsx / md / txt 的读写与还原
candidates.py                   候选词收集与敏感文件写入（权限 600）
tools/sync_terms.py             从 CSV/JSON 同步客户、供应商名单进词表
tools/merge_candidates.py       候选词一键并入词表（--dry-run / --only）
ocr.py                          macOS Vision + Linux/Windows RapidOCR
examples/config.example.json    补充词表示例
skills/contract-redact/         给 AI 助手用的技能：SKILL.md 只讲述触发/流程/禁忌，
                                references/ 按需加载（模式、格式、名单、限制）
tests/make_sample.py            生成虚构的 docx 样例
tests/check.py                  docx 验收：无残留 / 金额不动 / 还原逐字一致
tests/make_sample_multi.py      生成 md / xlsx / pdf / 扫描件 / doc 样例
tests/check_multi.py            多格式验收：逐格式跑一遍脱敏与还原（扫描件用 OCR 回读核对）
tests/check_sync.py             词表同步验收：新增并入、别名、手写项不丢、重复跑不重复
tests/check_candidates.py       候选闭环验收：发现 → 合并 → 再脱敏
tests/check_skill.py            技能自检：frontmatter、篇幅、相对链接、无本机路径
requirements.txt                按系统自动挑依赖（含跨平台 OCR）
```

## 开发

```bash
python tests/make_sample.py        # 生成虚构 docx 样例（不会动你的文件）
python tests/check.py              # docx：无残留 / 金额不动 / 还原逐字一致
python tests/make_sample_multi.py  # 生成 md / xlsx / pdf / doc 样例
python tests/check_multi.py        # 每种格式都跑一遍脱敏 + 还原
python tests/check_sync.py         # 词表同步：只增不减、别名合并、重复跑不重复
python tests/check_candidates.py   # 候选词：发现、预览、合并、再脱敏闭环
python tests/check_skill.py        # 技能文档自检（改过 SKILL.md / references 后跑）
```

三项核心断言：脱敏件里搜不到任何原文、金额与条款号原样保留、还原后与原文逐字一致；候选闭环另外验证权限 600 和「合并后不再产生候选」。

## 技能怎么装

`skills/contract-redact/` 是给 AI 助手看的那一层：`SKILL.md` 只讲「什么时候用、按什么顺序做、哪些不能做」，细节拆进 `references/`，按需加载，避免一上来塞满上下文。

```bash
# Claude Code / 支持 skills 的助手
mkdir -p ~/.claude/skills && cp -R skills/contract-redact ~/.claude/skills/

# Hermes
cp -R skills/contract-redact ~/.hermes/skills/
```

装完先自检一遍：`python tests/check_skill.py`。

## 许可

MIT，见 [LICENSE](LICENSE)。可以商用、可以改、可以嵌进自己的流程。
