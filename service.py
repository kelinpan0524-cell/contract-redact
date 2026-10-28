#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地 HTTP 脱敏服务：给别的程序（编辑器插件、脚本、内部工具）调用的最小接口。

只监听本机回环地址，不对外、不联网。接口：

    GET  /healthz          探活；规则引擎没加载成功时返回 503
    POST /redact           {"text": "...", "mode": "reversible"|"mask", "session": "可选"}
                           → {"text": "...", "session": "...", "hits": {...}}
    POST /restore          {"text": "...", "session": "..."}
                           → {"text": "...", "session": "...", "replaced": n}

失败一律 fail-closed：规则引擎没加载成功、处理过程抛异常、请求不合法时，
只返回错误码和一句说明，**绝不把请求里的原文回显出去**。

会话映射默认只在内存里；加 --session-file 后用口令加密落盘（scrypt + HMAC），
明文会话文件会被拒绝加载——会话映射等于原文。
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import mapping_crypto
import redact

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8791
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARS = 500_000
MAX_SESSION_CHARS = 128
MODES = {"reversible", "mask", "mask-only"}

# 规则引擎启动探针：只用来确认规则真能跑通，不含任何真实信息。
PROBE_TEXT = "甲方：验证测试科技有限公司 联系人：张三 手机：13800000000 邮箱：probe@example.cn"

TOKEN_RE = re.compile(r"【[^】]{1,16}】")
SESSION_VERSION = 1


class RuleEngine:
    """启动时把开关、词表和规则跑一遍探针；跑不通就进入失败态，之后只回 503。"""

    def __init__(self, only: str | None = None, skip: str | None = None, config_path: str | None = None):
        self.ok = False
        self.error = ""
        try:
            redact.set_enabled(only, skip)
            if config_path:
                redact.load_config(os.path.abspath(config_path))
            found = {match.group for match in redact.find_matches(PROBE_TEXT)}
            expected = {"phone"}
            if redact.ENABLED_GROUPS is not None:
                expected &= redact.ENABLED_GROUPS
            if expected and not expected <= found:
                raise RuntimeError("规则探针没有命中，规则引擎不可用")
            self.ok = True
        except (SystemExit, Exception) as exc:
            self.error = str(exc) or exc.__class__.__name__


def _new_session() -> str:
    return uuid.uuid4().hex


def _token_for(record: dict, group: str, value: str) -> str:
    """同一会话里同一实体复用同一代号；新实体按类别顺序编号。"""
    key = group + "\x00" + value
    codes = record["codes"]
    if key in codes:
        return codes[key]
    index = record["counters"].get(group, 0)
    record["counters"][group] = index + 1
    if group == "company":
        token = "【公司%s】" % redact.alpha_label(index)
    elif group == "person":
        token = "【姓名%s】" % redact.alpha_label(index)
    elif group == "customer":
        token = "【客户%s】" % redact.alpha_label(index)
    elif group == "supplier":
        token = "【供应商%s】" % redact.alpha_label(index)
    else:
        token = "【%s%d】" % (redact.TYPED_BASE[group], index + 1)
    codes[key] = token
    record["mapping"][token] = value
    return token


def _apply(text: str, token_for) -> tuple[str, dict[str, int]]:
    """按规则替换一遍，返回（新文本，各类别替换处数）。"""
    matches = redact.find_matches(text)
    if not matches:
        return text, {}
    hits: dict[str, int] = {}
    pieces: list[str] = []
    cursor = 0
    for match in matches:  # find_matches 已按位置排好且互不重叠
        hits[match.group] = hits.get(match.group, 0) + 1
        pieces.append(text[cursor:match.start])
        pieces.append(token_for(match.group, match.value))
        cursor = match.end
    pieces.append(text[cursor:])
    return "".join(pieces), hits


def _restore(text: str, mapping: dict[str, str]) -> tuple[str, int, int]:
    """把代号换回原文；返回（文本，替换处数，还不认识的代号个数）。"""
    replaced = 0
    for token in sorted(mapping, key=len, reverse=True):
        count = text.count(token)
        if count:
            text = text.replace(token, mapping[token])
            replaced += count
    unknown = sum(1 for token in TOKEN_RE.findall(text) if token not in mapping)
    return text, replaced, unknown


class SessionStore:
    """会话 → 代号映射。默认只在内存；给了文件就加密落盘，拒绝明文。"""

    def __init__(self, path: str | None = None, passphrase: str | None = None):
        self.path = os.path.abspath(path) if path else None
        self.passphrase = passphrase
        if self.path and not passphrase:
            raise ValueError(
                "会话落盘必须加密：请提供口令（--session-passphrase 或环境变量 CONTRACT_REDACT_PASSPHRASE）"
            )
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}
        if self.path and os.path.exists(self.path):
            self._load()

    # ------------------------------------------------------------ 落盘
    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as handle:
                blob = json.load(handle)
        except (OSError, ValueError) as exc:
            raise ValueError("会话文件读取失败：%s" % exc) from exc
        if not mapping_crypto.is_encrypted(blob):
            raise ValueError("会话文件不是加密格式，拒绝加载（明文会话等于原文）")
        try:
            payload = mapping_crypto.decrypt_mapping(blob, self.passphrase or "")
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError("会话文件解密失败（口令不对或文件被改过）：%s" % exc) from exc
        sessions = payload.get("sessions") if isinstance(payload, dict) else None
        if not isinstance(sessions, dict):
            raise ValueError("会话文件内容不对")
        for record in sessions.values():
            if not isinstance(record, dict) or not all(
                isinstance(record.get(key), dict) for key in ("codes", "mapping", "counters")
            ):
                raise ValueError("会话文件内容不对")
        self._sessions = sessions

    def _save_locked(self) -> None:
        if not self.path:
            return
        payload = {"v_session_store": SESSION_VERSION, "sessions": self._sessions}
        blob = mapping_crypto.encrypt_mapping(payload, self.passphrase or "")
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, mode=0o700, exist_ok=True)
        descriptor, tmp = tempfile.mkstemp(prefix=".contract-redact-sessions-", dir=directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(blob, handle, ensure_ascii=False)
                handle.write("\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------ 会话
    def update(self, session_id: str, work):
        """在锁内改这个会话；有新映射且要落盘时顺手存一次。"""
        with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                record = {"codes": {}, "mapping": {}, "counters": {}}
                self._sessions[session_id] = record
            before = len(record["mapping"])
            result = work(record)
            if self.path and len(record["mapping"]) != before:
                self._save_locked()
            return result

    def mapping(self, session_id: str) -> dict[str, str] | None:
        with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return None
            return dict(record["mapping"])

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)


class Handler(BaseHTTPRequestHandler):
    server_version = "contract-redact"
    sys_version = ""
    protocol_version = "HTTP/1.0"

    # ------------------------------------------------------------ 基础
    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass  # 客户端提前断开，不影响服务

    def _error(self, status: int, message: str) -> None:
        self._send(status, {"error": message})

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))
        sys.stderr.flush()

    def _engine(self):
        return getattr(self.server, "engine", None)

    def _store(self):
        return getattr(self.server, "store", None)

    def _engine_ready(self) -> bool:
        engine = self._engine()
        if engine is not None and engine.ok:
            return True
        # 引擎缺失或没加载成功：先回 503，连请求体都不读，避免任何原文被处理/回显
        self._error(503, "规则引擎未加载成功，拒绝处理（不会返回原文）")
        return False

    # ------------------------------------------------------------ 路由
    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/healthz":
            engine = self._engine()
            if engine is not None and engine.ok:
                self._send(200, {"status": "ok"})
            else:
                # 具体原因只写在启动时的本机 stderr 里，HTTP 响应不回显配置细节
                self._send(503, {"status": "unavailable", "error": "规则引擎未加载成功"})
            return
        if path in ("/redact", "/restore"):
            self._error(405, "这个接口只接受 POST")
            return
        self._error(404, "未知路径")

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path not in ("/redact", "/restore"):
            self._error(404, "未知路径")
            return
        if not self._engine_ready():
            return
        raw = self._read_body()
        if raw is None:
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._error(400, "请求体不是合法 JSON")
            return
        if not isinstance(payload, dict):
            self._error(400, "请求体必须是 JSON 对象")
            return
        try:
            if path == "/redact":
                status, body = self._handle_redact(payload)
            else:
                status, body = self._handle_restore(payload)
        except Exception as exc:
            # 只在本机日志里留原因，响应里不带原文、也不带异常细节
            sys.stderr.write("处理失败：%s: %s\n" % (exc.__class__.__name__, exc))
            sys.stderr.flush()
            self._error(503, "处理失败，未返回任何文本（fail-closed）")
            return
        self._send(status, body)

    def _read_body(self) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except (TypeError, ValueError):
            self._error(400, "缺少或不合法的 Content-Length")
            self.close_connection = True
            return None
        if length <= 0:
            self._error(400, "请求体为空")
            return None
        if length > MAX_BODY_BYTES:
            self._error(413, "请求体过大（上限 %d 字节）" % MAX_BODY_BYTES)
            self.close_connection = True
            return None
        return self.rfile.read(length)

    # ------------------------------------------------------------ 业务
    def _handle_redact(self, payload: dict) -> tuple[int, dict]:
        text = payload.get("text")
        if not isinstance(text, str) or not text:
            return 400, {"error": "text 必须是非空字符串"}
        if len(text) > MAX_TEXT_CHARS:
            return 413, {"error": "text 过长（上限 %d 字）" % MAX_TEXT_CHARS}

        mode = payload.get("mode") or "reversible"
        if not isinstance(mode, str) or mode not in MODES:
            return 400, {"error": "mode 只支持 reversible / mask（mask-only 等同 mask）"}
        if mode == "mask-only":
            mode = "mask"

        session = payload.get("session")
        if session is not None and not isinstance(session, str):
            return 400, {"error": "session 必须是字符串"}
        if isinstance(session, str) and len(session) > MAX_SESSION_CHARS:
            return 400, {"error": "session 过长（上限 %d 字）" % MAX_SESSION_CHARS}
        if isinstance(session, str) and not session.strip():
            session = None

        if mode == "mask":
            # 不可逆：不建会话、不留映射，密码/号码直接打星；session 一律返回 None
            out, hits = _apply(text, lambda group, value: redact.mask_value(value))
            return 200, {"text": out, "session": None, "hits": hits}

        session_id = session or _new_session()
        store = self._store()
        if store is None:
            raise RuntimeError("会话存储没有初始化")
        out, hits = store.update(
            session_id,
            lambda record: _apply(text, lambda group, value: _token_for(record, group, value)),
        )
        return 200, {"text": out, "session": session_id, "hits": hits}

    def _handle_restore(self, payload: dict) -> tuple[int, dict]:
        text = payload.get("text")
        if not isinstance(text, str) or not text:
            return 400, {"error": "text 必须是非空字符串"}
        if len(text) > MAX_TEXT_CHARS:
            return 413, {"error": "text 过长（上限 %d 字）" % MAX_TEXT_CHARS}
        session = payload.get("session")
        if not isinstance(session, str) or not session.strip():
            return 400, {"error": "session 必填"}
        if len(session) > MAX_SESSION_CHARS:
            return 400, {"error": "session 过长（上限 %d 字）" % MAX_SESSION_CHARS}

        store = self._store()
        if store is None:
            raise RuntimeError("会话存储没有初始化")
        mapping = store.mapping(session)
        if mapping is None:
            return 404, {"error": "会话不存在或没有可还原的映射"}
        out, replaced, unknown = _restore(text, mapping)
        return 200, {
            "text": out,
            "session": session,
            "replaced": replaced,
            "unknown_tokens": unknown,
        }


def make_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    engine: RuleEngine | None = None,
    store: SessionStore | None = None,
) -> ThreadingHTTPServer:
    """建一个可测试的服务器实例；port=0 时由系统分配空闲端口。"""
    engine = engine if engine is not None else RuleEngine()
    store = store if store is not None else SessionStore()
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    httpd.engine = engine
    httpd.store = store
    return httpd
