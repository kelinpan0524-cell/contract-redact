# 安装 contract-redact

三种人三种装法，挑一条就行。共同前提只有一个：**Python 3.10 或更新版本**。

| 你是谁 | 怎么装 | 装完得到什么 |
| --- | --- | --- |
| 自己长期用、换电脑 | [pipx](#一自己用pipx推荐) | 系统里多一个 `contract-redact` 命令，隔离环境，不污染系统 Python |
| 同事 / 团队 | [clone 后跑 `./install.sh`](#二同事团队clone--installsh) | 仓库里的 `.venv`、命令行入口，以及软链到 AI 技能目录的技能 |
| 只想看看效果 | [不用装](#三只看效果不用装) | 看 README 里的前后对照，或用临时环境跑一次 |

装完先做一次验证：`contract-redact --help` 能看到 `redact` / `restore` / `check` / `serve` 四个子命令，就说明入口通了。

---

## 一、自己用：pipx（推荐）

pipx 会把工具装进一个独立的小环境，系统 Python 不受影响，卸载也干净。

### 1. 先装 pipx（已经装过就跳过）

```bash
# macOS
brew install pipx
pipx ensurepath

# Linux
python3 -m pip install --user pipx
python3 -m pipx ensurepath
```

`ensurepath` 之后重开一个终端，让 PATH 生效。

### 2. 装 contract-redact

```bash
pipx install git+https://github.com/kelinpan0524-cell/contract-redact
```

要处理**扫描件 PDF**（图片里的字要 OCR）时，再补一组 OCR 依赖：

```bash
# macOS：用系统 Vision，不下载模型
pipx inject contract-redact pyobjc-framework-Vision pyobjc-framework-Quartz

# Linux / Windows：RapidOCR，约 100MB，模型随包
pipx inject contract-redact rapidocr-onnxruntime
```

不装 OCR 也能正常用 docx / xlsx / 文字版 PDF / md / txt，只是扫描件里的字不会被处理，报告里会写明。

### 3. 验证

```bash
contract-redact --help
```

再用临时文件真跑一遍（不碰你自己的合同）：

```bash
cd "$(mktemp -d)"
printf '甲方：星海精密制造有限公司\n法定代表人：陈志远\n电话：13800000001\n含税总价 1,000,000.00 元\n' > 测试.md

contract-redact redact  测试.md -o 测试_脱敏.md --map 测试.map.json
contract-redact check   测试_脱敏.md                 # 退出码 0 = 没发现残留
contract-redact restore 测试_脱敏.md --map 测试.map.json -o 测试_还原.md
diff 测试.md 测试_还原.md && echo "还原一致"
```

三步都对，说明脱敏、自检、还原这条主链路是通的。

**想跑仓库里的完整验收 `tests/check.py`**：这个脚本要用仓库里的样例和虚拟环境，走下面方法二（`git clone` + `./install.sh`）最省事；pipx 装的是系统命令，和仓库测试是两套环境。

### 卸载

```bash
pipx uninstall contract-redact
```

---

## 二、同事、团队：clone 后跑 `./install.sh`

一条命令做完：建虚拟环境、装依赖、装命令行、链技能、自检。

```bash
git clone https://github.com/kelinpan0524-cell/contract-redact.git
cd contract-redact
./install.sh
```

它会依次做四件事：

1. 建 `.venv`（已经有就复用，不删你的东西）；
2. 装 `requirements.txt`（含当前平台的 OCR 依赖），再 `pip install -e .`，生成 `contract-redact` 命令行；
3. 把 `skills/contract-redact` **软链**到已经存在的 `~/.hermes/skills` 和 `~/.claude/skills`（哪个存在链哪个，两个都没有就跳过）；
4. 自检：`contract-redact --help`、一次真实的 md 脱敏 → 自检 → 还原、以及 `tests/check.py` 全流程。

### 参数

| 参数 | 作用 |
| --- | --- |
| `--list` | 只打印"将要做什么"，不动手（第一次建议先跑这个） |
| `--target DIR` | 技能链到指定目录，可写多个；目录不存在会自动创建 |
| `--no-skill` | 只装命令行和依赖，不碰技能目录 |
| `--python PATH` | 指定解释器（默认自动找 3.10 以上；机器上有多个 Python 时有用） |
| `-h`, `--help` | 帮助 |

几个例子：

```bash
./install.sh --list                        # 先看看会发生什么
./install.sh --target ~/.codex/skills      # 技能链到别处
./install.sh --no-skill                    # 只要命令行
./install.sh --python python3.12           # 指定解释器
```

如果目标目录里已经有一个**同名真目录**（不是软链），脚本会跳过并提示，不会覆盖你自己的文件。

### 验证

```bash
source .venv/bin/activate        # 或者不激活，直接用 .venv/bin/contract-redact
contract-redact --help
python tests/check.py            # 期望最后一行：结果：全部通过
```

`install.sh` 最后已经把这两条跑过一遍了，终端里看到「安装完成」就说明通过。之后日常用：

```bash
source .venv/bin/activate
contract-redact redact 合同.docx -o 合同_脱敏.docx
contract-redact check  合同_脱敏.docx
```

---

## 三、只看效果：不用装

- README 开头的「脱敏前 / 脱敏后」对照，先看效果是不是你要的；
- `examples/config.example.json` 是补充词表长什么样（客户简称、别名、keep 白名单）；
- 想真的跑一次又不留痕，用临时环境：

```bash
pipx run --spec git+https://github.com/kelinpan0524-cell/contract-redact contract-redact --help
```

第一次会下载依赖、建临时环境，跑完即弃；把最后的 `--help` 换成 `redact 你的文件 -o 输出` 就能试真实文件。验证命令就是上面这条 `--help`，能看到四个子命令即可。

如果本机 pipx 版本较老、不认这种「直接跑 git 地址」的写法，就退一步：先 `pipx install git+https://github.com/kelinpan0524-cell/contract-redact`，试完 `pipx uninstall contract-redact`。

什么都不装、只看文档也完全够用——工具的所有判断都写在 README 和 `skills/contract-redact/references/` 里。

---

## 装完以后的通用说明

### 退出码怎么看

| 子命令 | 0 | 1 | 2 |
| --- | --- | --- | --- |
| `redact` / `restore` | 成功 | — | 参数或文件有问题 |
| `check` | 没发现残留 | 自检跑不起来 | 有残留，或没能全部检查 |
| `serve` | 正常停止 | — | 启动失败 |

`check` 是外发前的最后一道闸：**退出码 0 才能发**。

### 格式与系统能力

- `.doc` 老格式：macOS 用系统自带的 `textutil`；Linux / Windows 要装 LibreOffice，否则会提示你先另存为 `.docx`。
- 扫描件 PDF 的 OCR：macOS 用系统 Vision（零下载）；其它系统用 RapidOCR（约 100MB，模型随包）。
- 全流程不联网、不调模型；唯一的网络相关代码是 `serve`，它只在本机 `127.0.0.1` 接请求。

### 隐私红线

脱敏产出的 `*.map.json` 映射表**就是原文**，权限自动设 600；它和 `*.candidates.json` 候选词文件都绝不能跟着脱敏件一起发出去。默认审计日志在 `~/.contract-redact/audit.log`，只记元数据和计数，不记原文。

### 卸载

```bash
# pipx 装的
pipx uninstall contract-redact

# clone + install.sh 装的：删虚拟环境和技能软链
rm -rf .venv
rm -f ~/.hermes/skills/contract-redact ~/.claude/skills/contract-redact
```
