#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""serve 子命令验收：本地 HTTP 服务的正常路径 + fail-closed。

覆盖：
1. /redact 可逆模式：返回 {text, session, hits}，原文不出去，金额不动；
2. 同一会话里同一实体复用同一代号，新实体往后编号；
3. /restore 按会话还原；未知会话 404 且不回显原文；
4. 掩码模式：不可逆，不留会话；
5. 参数错误（JSON/字段/mode/超长）返回 4xx；错误方法 405；未知路径 404；
6. 规则引擎加载失败 → 所有接口 503，响应里没有原文；
7. 处理过程抛异常 → 503（fail-closed），恢复后正常；
8. 会话落盘：口令加密、权限 600、文件里搜不到原文；重启后能还原；
9. serve CLI 的参数接线（端口、会话文件、口令）。
"""
from __future__ import annotations

import http.client
import io
import json
import os
import stat
import sys
import tempfile
import threading
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import redact  # noqa: E402
import redact_cli  # noqa: E402
import service  # noqa: E402

COMPANY = "星海精密制造有限公司"
COMPANY_B = "蓝谷新材料有限公司"
PERSON = "陈志远"
MOBILE = "13800000001"
BANK = "6222021234567890123"
AMOUNT = "1,234,567.89"
SECRETS = [COMPANY, COMPANY_B, PERSON, MOBILE, BANK]

TEXT = (
    "甲方：%s\n"
    "法定代表人：%s\n"
    "手机：%s\n"
    "收款账户：%s\n"
    "合同金额：人民币 %s 元，违约金 20%%。"
    % (COMPANY, PERSON, MOBILE, BANK, AMOUNT)
)

ERROR_BODIES: list[str] = []


class Running:
    """在后台线程里跑一个真实 HTTP 服务（端口由系统分配）。"""

    def __init__(self, engine=None, store=None):
        self.engine = engine if engine is not None else service.RuleEngine()
        self.store = store if store is not None else service.SessionStore()
        self.httpd = service.make_server("127.0.0.1", 0, engine=self.engine, store=self.store)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(5)


def request(port: int, method: str, path: str, obj=None, raw: bytes | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    body = raw
    if body is None and obj is not None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if body is not None:
        headers["Content-Length"] = str(len(body))
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        text = response.read().decode("utf-8", "replace")
        status = response.status
    finally:
        conn.close()
    try:
        payload = json.loads(text)
    except ValueError:
        payload = {"_raw": text}
    return status, payload, text


def post(port: int, path: str, obj) -> tuple[int, dict, str]:
    return request(port, "POST", path, obj=obj)


def write_temp_file(directory: str, text: str) -> str:
    """在测试临时目录里落一个文件：路径由系统生成，不拼外部输入。"""
    descriptor, path = tempfile.mkstemp(suffix=".json", dir=directory)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def read_binary(path: str) -> bytes:
    descriptor = os.open(path, os.O_RDONLY)
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read()


def mode_of(path: str) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def contains_secret(text: str) -> bool:
    return any(secret in text for secret in SECRETS)


def main() -> int:
    checks: list[tuple[str, bool]] = []
    tmp = tempfile.mkdtemp(prefix="contract-redact-serve-")

    # 请求日志会刷屏，测试里关掉（只影响测试进程）
    original_log = service.Handler.log_message
    service.Handler.log_message = lambda *_args, **_kwargs: None

    try:
        # ---- 1~5. 正常服务 ----
        with Running() as srv:
            port = srv.port

            status, body, raw = request(port, "GET", "/healthz")
            checks.append(("健康检查 200", status == 200 and body.get("status") == "ok"))

            status, body, raw = post(port, "/redact", {"text": TEXT, "mode": "reversible", "session": "s1"})
            checks.append(("可逆脱敏返回 200", status == 200))
            checks.append(("返回 session", body.get("session") == "s1"))
            checks.append(("返回 hits", (body.get("hits") or {}).get("company") == 1))
            checks.append(("返回 hits 含电话", (body.get("hits") or {}).get("phone") == 1))
            redacted_text = body.get("text") or ""
            checks.append(("脱敏文本无原文", not contains_secret(redacted_text)))
            checks.append(("脱敏文本有代号", "【公司A】" in redacted_text and "【姓名A】" in redacted_text))
            checks.append(("金额未动", AMOUNT in redacted_text))
            checks.append(("响应里没有原文", not contains_secret(raw)))

            # 同一会话：同一实体复用代号，新实体往后编号
            status, body, _raw = post(
                port,
                "/redact",
                {"text": "乙方：%s 联系人：%s" % (COMPANY, PERSON), "session": "s1"},
            )
            second = body.get("text") or ""
            checks.append(("同会话复用代号", "【公司A】" in second and "【姓名A】" in second))
            status, body, _raw = post(
                port,
                "/redact",
                {"text": "丙方：%s" % COMPANY_B, "session": "s1"},
            )
            checks.append(("新实体编号往后", "【公司B】" in (body.get("text") or "")))

            # 还原
            status, body, raw = post(port, "/restore", {"text": redacted_text, "session": "s1"})
            checks.append(("还原返回 200", status == 200))
            checks.append(("还原逐字一致", body.get("text") == TEXT))
            checks.append(("还原处数正确", body.get("replaced") == 4))

            status, body, raw = post(port, "/restore", {"text": "随便什么", "session": "不存在"})
            checks.append(("未知会话 404", status == 404))
            checks.append(("未知会话不返回文本", "text" not in body))
            ERROR_BODIES.append(raw)

            # 掩码模式
            status, body, raw = post(port, "/redact", {"text": TEXT, "mode": "mask"})
            masked = body.get("text") or ""
            checks.append(("掩码返回 200", status == 200))
            checks.append(("掩码后没有原文", not contains_secret(masked)))
            checks.append(("掩码打了星号", "*" in masked))
            checks.append(("掩码不建会话", body.get("session") is None))
            checks.append(("掩码响应无原文", not contains_secret(raw)))

            status, body, _raw = post(port, "/redact", {"text": MOBILE, "mode": "mask", "session": "m1"})
            checks.append(("掩码忽略传入的 session", status == 200 and body.get("session") is None))
            status, body, _raw = post(port, "/restore", {"text": body.get("text") or "", "session": "m1"})
            checks.append(("掩码的 session 不能还原", status == 404))

            status, body, _raw = post(port, "/redact", {"text": MOBILE, "mode": "mask-only"})
            checks.append(("mask-only 兼容写法可用", status == 200 and "*" in (body.get("text") or "")))

            # 参数错误
            status, body, raw = post(port, "/redact", {"mode": "reversible"})
            checks.append(("缺 text 返回 400", status == 400))
            status, body, raw = post(port, "/redact", {"text": 123})
            checks.append(("text 不是字符串返回 400", status == 400))
            status, body, raw = post(port, "/redact", {"text": "abc", "mode": "bogus"})
            checks.append(("未知 mode 返回 400", status == 400))
            status, body, raw = post(port, "/redact", {"text": "abc", "session": "x" * 200})
            checks.append(("session 过长返回 400", status == 400))
            status, body, raw = request(port, "POST", "/redact", raw=b"{not json")
            checks.append(("非法 JSON 返回 400", status == 400))
            status, body, raw = request(port, "POST", "/redact", raw=b"[1,2]")
            checks.append(("JSON 不是对象返回 400", status == 400))
            status, body, raw = post(port, "/redact", {"text": "a" * (service.MAX_TEXT_CHARS + 1)})
            checks.append(("text 过长返回 413", status == 413))
            ERROR_BODIES.append(raw)

            status, body, raw = request(port, "GET", "/redact")
            checks.append(("GET /redact 返回 405", status == 405))
            status, body, raw = request(port, "GET", "/nope")
            checks.append(("未知 GET 路径 404", status == 404))
            status, body, raw = post(port, "/nope", {"text": "abc"})
            checks.append(("未知 POST 路径 404", status == 404))

            # ---- 7. 处理异常：fail-closed ----
            original_find = redact.find_matches

            def boom(_text):
                raise RuntimeError("模拟规则引擎炸了")

            redact.find_matches = boom
            try:
                # 服务端会把异常原因写到自己的 stderr（本机日志），测试里收起来
                with redirect_stderr(io.StringIO()):
                    status, body, raw = post(port, "/redact", {"text": TEXT, "session": "boom"})
            finally:
                redact.find_matches = original_find
            checks.append(("处理异常返回 503", status == 503))
            checks.append(("处理异常不返回文本", "text" not in body))
            checks.append(("处理异常响应不含原文", not contains_secret(raw)))

            status, body, _raw = post(port, "/redact", {"text": TEXT, "session": "s1"})
            checks.append(("异常恢复后可继续用", status == 200))

        # ---- 6. 规则引擎加载失败：整体 fail-closed ----
        bad_config = write_temp_file(
            tmp, json.dumps({"extra_orgs": "这不是数组"}, ensure_ascii=False)
        )
        bad_engine = service.RuleEngine(config_path=bad_config)
        checks.append(("坏词表让引擎失败", not bad_engine.ok))
        checks.append(("失败原因可读", "extra_orgs" in bad_engine.error))

        with Running(engine=bad_engine) as srv:
            status, body, raw = request(srv.port, "GET", "/healthz")
            checks.append(("引擎失败时健康检查 503", status == 503))
            status, body, raw = post(srv.port, "/redact", {"text": TEXT, "session": "s1"})
            checks.append(("引擎失败时 /redact 返回 503", status == 503))
            checks.append(("引擎失败时不返回文本", "text" not in body))
            checks.append(("引擎失败时响应不含原文", not contains_secret(raw)))
            status, body, raw = post(srv.port, "/restore", {"text": "【公司A】", "session": "s1"})
            checks.append(("引擎失败时 /restore 返回 503", status == 503))
            checks.append(("引擎失败时还原也不返回文本", "text" not in body))

        # ---- 8. 会话落盘（加密） ----
        session_file = os.path.join(tmp, "sessions.enc")
        store = service.SessionStore(session_file, "pw-test-1")
        with Running(store=store) as srv:
            status, body, _raw = post(srv.port, "/redact", {"text": TEXT, "session": "persist"})
            checks.append(("落盘会话脱敏成功", status == 200))
            persisted_text = body.get("text") or ""
        checks.append(("会话文件已生成", os.path.isfile(session_file)))
        if os.path.isfile(session_file):
            checks.append(("会话文件权限 600", mode_of(session_file) == 0o600))
            blob = read_binary(session_file)
            checks.append(("会话文件不含原文", not any(s.encode("utf-8") in blob for s in SECRETS)))
            payload = json.loads(blob.decode("utf-8"))
            checks.append(("会话文件是加密格式", payload.get("cipher") == "hmac-ctr-sha256"))

        reopened = service.SessionStore(session_file, "pw-test-1")
        checks.append(("重启后能读到会话", reopened.mapping("persist") is not None))
        with Running(store=reopened) as srv:
            status, body, _raw = post(srv.port, "/restore", {"text": persisted_text, "session": "persist"})
            checks.append(("重启后仍可还原", status == 200 and body.get("text") == TEXT))

        try:
            service.SessionStore(session_file, "错口令")
            checks.append(("错口令被拒绝", False))
        except ValueError:
            checks.append(("错口令被拒绝", True))

        try:
            service.SessionStore(os.path.join(tmp, "no-pass.enc"), None)
            checks.append(("落盘必须给口令", False))
        except ValueError:
            checks.append(("落盘必须给口令", True))

        plain = write_temp_file(tmp, json.dumps({"sessions": {}}, ensure_ascii=False))
        try:
            service.SessionStore(plain, "pw-test-1")
            checks.append(("明文会话文件被拒绝", False))
        except ValueError:
            checks.append(("明文会话文件被拒绝", True))

        # ---- 9. serve CLI 接线 ----
        captured: dict = {}

        class FakeServer:
            server_address = ("127.0.0.1", 9999)

            def __init__(self):
                self.closed = False

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                self.closed = True

        def fake_make_server(host, port, *, engine=None, store=None):
            captured.update(host=host, port=port, engine=engine, store=store)
            return FakeServer()

        original_make = redact_cli.service.make_server
        redact_cli.service.make_server = fake_make_server
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = redact_cli.cmd_serve(
                    [
                        "--port", "9999",
                        "--session-file", os.path.join(tmp, "cli-sessions.enc"),
                        "--session-passphrase", "pw-test-2",
                    ]
                )
        finally:
            redact_cli.service.make_server = original_make
        checks.append(("serve CLI 正常停止返回 0", code == 0))
        checks.append(("端口参数传下去", captured.get("port") == 9999))
        checks.append(("引擎已加载", bool(getattr(captured.get("engine"), "ok", False))))
        checks.append(
            (
                "会话文件参数传下去",
                getattr(captured.get("store"), "path", "")
                == os.path.abspath(os.path.join(tmp, "cli-sessions.enc")),
            )
        )
        checks.append(("打印监听地址", "监听 http://127.0.0.1:9999" in out.getvalue()))

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = redact_cli.cmd_serve(["--session-file", os.path.join(tmp, "x.enc")])
        checks.append(("落盘没口令时拒绝启动", code == 2))
        checks.append(("拒绝启动有说明", "口令" in err.getvalue()))

        # ---- 总检查：所有错误响应都不含原文 ----
        checks.append(("所有错误响应都不含原文", not any(contains_secret(body) for body in ERROR_BODIES)))
    finally:
        service.Handler.log_message = original_log

    for name, ok in checks:
        print("%s %s" % ("✓" if ok else "✗", name))
    failed = [name for name, ok in checks if not ok]
    print("\n结果：%s" % ("全部通过" if not failed else "失败：" + "、".join(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
