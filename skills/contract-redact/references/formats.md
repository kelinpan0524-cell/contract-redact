# 格式与 OCR

| 格式 | 输出 | 说明 |
| --- | --- | --- |
| `.docx` | 同格式 | 正文、表格（含嵌套）、页眉页脚、文本框；段落样式保留 |
| `.doc` | `.docx` | macOS 用 `textutil`，Linux/Windows 用 LibreOffice；版式可能细微变化 |
| `.pdf` | 同格式 | 文字层真擦除（不是盖白块）再写代号；扫描件走 OCR 按行重写 |
| `.xlsx` / `.xlsm` | 同格式 | 单元格、批注、隐藏工作表；公式不动，图表和图片里的字不处理 |
| `.md` / `.txt` | 同格式 | 纯文本替换，编码跟随原文件 |

## 扫描件

页面没有文字层时自动 OCR：

- macOS：系统 Vision，零模型下载，约 0.4 秒/页；
- Linux / Windows：RapidOCR（ONNX，约 100MB），1–1.5 秒/页；
- `--ocr-engine vision|rapidocr` 可手动指定；两个都没装时该页跳过并写进报告。

代价：按行重写 → 版式、字距、字体都会变；数字可能认错。**扫描件交付前必须肉眼核对金额和账号。**

## 跨平台

| | macOS | Linux | Windows |
| --- | --- | --- | --- |
| 文档处理 | ✅ | ✅ | ✅ |
| 扫描件 OCR | Vision | RapidOCR | RapidOCR |
| `.doc` 转换 | `textutil` | LibreOffice | LibreOffice |

`pip install -r requirements.txt` 会按系统自动选依赖。PDF 写代号用 PyMuPDF 自带 CJK 字体，三端一致。

## 已知不覆盖

图片里的文字、手写签名、盖章、附件、嵌入对象、图表；脚注尾注的正文、批注原文、修订里被删掉的文字（只提醒，不擅自改文件）。
