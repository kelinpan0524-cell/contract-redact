#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""contract-redact 统一入口：redact / restore / check / serve 四个子命令。

    python redact_cli.py redact  合同.docx -o 合同_脱敏.docx
    python redact_cli.py restore 合同_脱敏.docx --map 合同_脱敏.map.json -o 合同_还原.docx
    python redact_cli.py check   合同_脱敏.docx
    python redact_cli.py serve   --port 8791

脱敏和还原仍然是 redact.py / restore.py 原来的参数与行为，这里只是把它们
收进一个入口，并补上 hits 摘要、审计 JSON 和自检。每个子命令都支持 --help。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

import checker
import redact
import restore
import service

USAGE = """contract-redact 统一入口

用法：
  python redact_cli.py redact  <输入> [选项]        脱敏（等同 redact.py，另支持 --audit-json）
  python redact_cli.py restore <输入> --map ...    还原（等同 restore.py）
  python redact_cli.py check   <输入> [选项]       自检：脱敏件里还有没有残留
  python redact_cli.py serve   [选项]              本地 HTTP 服务（默认 127.0.0.1:8791）

每个子命令都支持 --help。退出码：
  redact / restore  与 redact.py / restore.py 一致（0 成功，2 参数或文件错误）
  check             0 = 没发现残留；2 = 有残留或没能全部检查；1 = 自检跑不起来
  serve             0 = 正常停止；2 = 启动失败
"""


# ---------------------------------------------------------------------------
# redact
# ---------------------------------------------------------------------------


def _extract_option(argv: list[str], name: str) -> tuple[list[str], str | None]:
    """把本模块独有的选项摘出来，其余参数原样交给 redact.parse_args。"""
    rest: list[str] = []
    value: str | None = None
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == name:
            if index + 1 >= len(argv):
                raise ValueError("%s 后面要跟一个文件路径" % name)
            value = argv[index + 1]
            index += 2
            continue
        if item.startswith(name + "="):
            value = item.split("=", 1)[1]
            index += 1
            continue
        rest.append(item)
        index += 1
    return rest, value


def format_hits(hits: dict[str, int]) -> str:
    """一行 hits 摘要：各类别替换处数。"""
    if not hits:
        return "hits: 无（规则没有命中任何目标）"
    ordered = [group for group in redact.GROUP_LABELS if group in hits]
    ordered += [group for group in hits if group not in redact.GROUP_LABELS]
    detail = "，".join(
        "%s %d" % (redact.GROUP_LABELS.get(group, group), hits[group]) for group in ordered
    )
    return "hits: %s（合计 %d 处）" % (detail, sum(hits.values()))


def write_audit_json(path: str, run: dict) -> str:
    """追加一行 JSON 审计记录：只有元数据和计数，不含原文。"""
    absolute = os.path.abspath(path)
    directory = os.path.dirname(absolute)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    entry = {
        key: run.get(key)
        for key in ("at", "source", "output", "mode", "rules", "hits", "entities", "mapping_sha256")
    }
    if not entry["at"]:
        entry["at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    descriptor = os.open(absolute, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    os.chmod(absolute, 0o600)
    return absolute


def cmd_redact(argv: list[str]) -> int:
    try:
        rest, audit_json = _extract_option(argv, "--audit-json")
    except ValueError as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 2
    try:
        code = redact.main(rest)
    except SystemExit as exc:
        return int(exc.code or 0)
    if code != 0:
        return code

    run = dict(redact.LAST_RUN)
    print(format_hits(run.get("hits") or {}))
    if audit_json:
        try:
            written = write_audit_json(audit_json, run)
        except OSError as exc:
            print("错误：审计 JSON 写入失败：%s" % exc, file=sys.stderr)
            return 1
        print("审计 JSON：%s（一行一次运行，权限 600，不含原文）" % written)
    return 0


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------


def cmd_restore(argv: list[str]) -> int:
    try:
        return restore.main(argv)
    except SystemExit as exc:
        return int(exc.code or 0)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def cmd_check(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="redact_cli.py check",
        description="自检：对已脱敏文件再跑一遍同一套规则，只报类别、数量和位置，不打印原文",
    )
    parser.add_argument("input", help="已脱敏的文件（.docx / .pdf / .xlsx / .md / .txt）")
    parser.add_argument("--config", dest="config_path", help="补充词表 JSON（与脱敏时保持一致）")
    parser.add_argument("--only", help="只检查这些类别（与脱敏时的 --only 一致）")
    parser.add_argument("--skip", help="跳过这些类别（与脱敏时的 --skip 一致）")
    parser.add_argument(
        "--ocr-engine",
        choices=["vision", "rapidocr"],
        help="PDF 图片文字用哪个 OCR；默认自动选可用的",
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="不 OCR PDF 里的图片（更快，但图片里的文字不检查）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="用 JSON 输出（同样只含计数和位置）",
    )
    args = parser.parse_args(argv)

    source = os.path.abspath(args.input)
    if not os.path.isfile(source):
        print("错误：找不到文件 %s" % source, file=sys.stderr)
        return 1
    ext = os.path.splitext(source)[1].lower()
    if ext not in checker.SUPPORTED_EXT:
        print(
            "错误：不支持的格式 %s（支持 .docx / .pdf / .xlsx / .md / .txt）"
            % (ext or "无扩展名"),
            file=sys.stderr,
        )
        return 1

    try:
        redact.set_enabled(args.only, args.skip)
    except SystemExit as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 1
    if args.config_path:
        try:
            redact.load_config(os.path.abspath(args.config_path))
        except Exception as exc:
            print("错误：配置文件读取失败：%s" % exc, file=sys.stderr)
            return 1

    try:
        result = checker.scan_file(
            source,
            ocr=not args.no_ocr,
            ocr_engine=args.ocr_engine,
        )
    except Exception as exc:
        print("错误：自检失败：%s" % exc, file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(checker.as_dict(result), ensure_ascii=False, indent=2))
    else:
        print(checker.format_report(result))

    if not result.clean:
        return 2
    if not result.verified:
        return 2
    return 0


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


def cmd_serve(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="redact_cli.py serve",
        description="本地 HTTP 脱敏服务（默认只监听 127.0.0.1，失败一律 fail-closed）",
    )
    parser.add_argument("--host", default=service.DEFAULT_HOST, help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=service.DEFAULT_PORT, help="端口（默认 8791，0 = 随机）")
    parser.add_argument("--config", dest="config_path", help="补充词表 JSON")
    parser.add_argument("--only", help="只脱敏这些类别，逗号分隔")
    parser.add_argument("--skip", help="跳过这些类别（与 --only 二选一）")
    parser.add_argument(
        "--session-file",
        help="会话映射落盘路径（必须同时给口令，文件加密存储，权限 600）",
    )
    parser.add_argument(
        "--session-passphrase",
        help="会话落盘口令（也可用环境变量 CONTRACT_REDACT_PASSPHRASE）",
    )
    args = parser.parse_args(argv)

    passphrase = args.session_passphrase or os.environ.get("CONTRACT_REDACT_PASSPHRASE")
    engine = service.RuleEngine(
        only=args.only,
        skip=args.skip,
        config_path=args.config_path,
    )
    if not engine.ok:
        print(
            "警告：规则引擎没有加载成功（%s）。服务照常启动，但 /redact 与 /restore 一律返回 503，"
            "不会返回任何原文。" % engine.error,
            file=sys.stderr,
            flush=True,
        )

    try:
        store = service.SessionStore(args.session_file, passphrase)
    except (OSError, ValueError) as exc:
        print("错误：会话存储不可用：%s" % exc, file=sys.stderr)
        return 2

    try:
        httpd = service.make_server(args.host, args.port, engine=engine, store=store)
    except OSError as exc:
        print("错误：无法监听 %s:%d（%s）" % (args.host, args.port, exc), file=sys.stderr)
        return 2

    host, port = httpd.server_address[:2]
    print("监听 http://%s:%d（POST /redact, POST /restore, GET /healthz）" % (host, port), flush=True)
    if args.session_file:
        print(
            "会话落盘：%s（口令加密，权限 600；当前 %d 个会话）"
            % (store.path, store.count()),
            flush=True,
        )
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            "注意：正在监听非本机地址，同一网络里的其他设备也能调用本服务，请确认网络环境可信。",
            file=sys.stderr,
            flush=True,
        )

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。", flush=True)
    finally:
        httpd.server_close()
    return 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    command, rest = args[0], args[1:]
    handlers = {
        "redact": cmd_redact,
        "restore": cmd_restore,
        "check": cmd_check,
        "serve": cmd_serve,
    }
    handler = handlers.get(command)
    if handler is None:
        print("错误：不认识的子命令 %r" % command, file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2
    try:
        return handler(rest)
    except SystemExit as exc:
        code = exc.code
        return int(code) if isinstance(code, int) else 0


if __name__ == "__main__":
    sys.exit(main())
