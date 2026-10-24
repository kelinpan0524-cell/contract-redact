#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OCR 统一入口：优先用 macOS 自带的 Vision，其它系统（Linux/Windows）退到 RapidOCR。

两个引擎对外接口一样：给一张图，返回每行文字和归一化坐标
（原点左下，和 PDF 坐标一致，方便直接换算到页面上）。

- Vision：macOS 系统框架，零模型下载，走神经网络引擎，最快，中文很好
- RapidOCR：ONNX Runtime + PP-OCR 模型，跨平台，首次加载稍慢，中文也不错
"""
from __future__ import annotations

import os
import tempfile

_ENGINE: str | None = None
_RAPID = None


def _try_vision() -> bool:
    try:
        import Quartz  # noqa: F401
        import Vision  # noqa: F401
    except Exception:
        return False
    return True


def _try_rapid() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except Exception:
        return False
    return True


def engine(force: str | None = None) -> str | None:
    """返回可用的引擎名：vision / rapidocr / None。"""
    global _ENGINE
    if force:
        if force == "vision" and _try_vision():
            return "vision"
        if force == "rapidocr" and _try_rapid():
            return "rapidocr"
        return None
    if _ENGINE:
        return _ENGINE
    if _try_vision():
        _ENGINE = "vision"
    elif _try_rapid():
        _ENGINE = "rapidocr"
    else:
        _ENGINE = None
    return _ENGINE


def available() -> bool:
    return engine() is not None


def engine_name() -> str:
    name = engine()
    return {"vision": "macOS Vision", "rapidocr": "RapidOCR"}.get(name or "", "无")


# ---------------------------------------------------------------- Vision


def _vision(path: str, fast: bool, languages):
    import Quartz
    import Vision
    from Foundation import NSURL

    url = NSURL.fileURLWithPath_(os.path.abspath(path))
    source = Quartz.CGImageSourceCreateWithURL(url, None)
    if source is None:
        raise RuntimeError("读不到图片：%s" % path)
    image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(1 if fast else 0)  # 0=accurate 1=fast
    request.setRecognitionLanguages_(list(languages))
    request.setUsesLanguageCorrection_(True)

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError("Vision OCR 失败：%s" % error)

    lines = []
    for observation in request.results() or []:
        candidates = observation.topCandidates_(1)
        if not candidates:
            continue
        box = observation.boundingBox()
        lines.append(
            (
                candidates[0].string(),
                float(box.origin.x),
                float(box.origin.y),
                float(box.size.width),
                float(box.size.height),
            )
        )
    return lines


# ---------------------------------------------------------------- RapidOCR


def _rapid(path: str, size):
    global _RAPID
    from rapidocr_onnxruntime import RapidOCR

    if _RAPID is None:
        _RAPID = RapidOCR()
    result, _elapsed = _RAPID(path)
    if not result:
        return []

    width, height = size if size else (0, 0)
    if not width or not height:
        import pymupdf

        pixmap = pymupdf.Pixmap(path)
        width, height = pixmap.width, pixmap.height

    lines = []
    for item in result:
        box, text = item[0], item[1]
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        lines.append(
            (
                text,
                x0 / width,
                1 - y1 / height,          # 翻成原点左下
                (x1 - x0) / width,
                (y1 - y0) / height,
            )
        )
    return lines


# ---------------------------------------------------------------- 对外


def ocr_image(path: str, fast: bool = False, languages=("zh-Hans", "en-US"), size=None, force=None):
    """返回 [(text, x, y, w, h)]，坐标归一化、原点左下。"""
    name = engine(force)
    if name == "vision":
        return _vision(path, fast, languages)
    if name == "rapidocr":
        return _rapid(path, size)
    raise RuntimeError(
        "没有可用的 OCR 引擎：macOS 装 pyobjc-framework-Vision，"
        "Linux/Windows 用 pip install rapidocr-onnxruntime"
    )


def ocr_pdf_page(page, dpi: int = 200, fast: bool = False, force=None):
    """把一页 PDF 渲成图再 OCR。"""
    pixmap = page.get_pixmap(dpi=dpi)
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        pixmap.save(path)
        return ocr_image(path, fast=fast, size=(pixmap.width, pixmap.height), force=force)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


if __name__ == "__main__":
    import sys
    import time

    print("引擎：", engine_name())
    for f in sys.argv[1:]:
        started = time.time()
        lines = ocr_image(f)
        print("%s：%d 行，%.2f 秒" % (f, len(lines), time.time() - started))
        for line in lines[:5]:
            print("   ", line[0])
