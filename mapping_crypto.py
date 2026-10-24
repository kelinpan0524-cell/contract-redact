#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""映射表加密（纯标准库，不引入依赖）。

方案：scrypt 派生密钥 → HMAC-SHA256 计数器流加密 → HMAC-SHA256 认证（先加密后认证）。
口令错或文件被改，都会在解密时直接报错，不会静默读出错的内容。

文件格式（JSON）：
{
  "v": 2, "kdf": "scrypt", "cipher": "hmac-ctr-sha256",
  "n": 16384, "r": 8, "p": 1,
  "salt": "...", "nonce": "...", "ct": "...", "tag": "..."
}
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os

KDF_N, KDF_R, KDF_P = 16384, 8, 1


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def _derive(passphrase: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        passphrase.encode("utf-8"), salt=salt, n=KDF_N, r=KDF_R, p=KDF_P, dklen=32
    )


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    blocks = []
    counter = 0
    produced = 0
    while produced < length:
        block = hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        blocks.append(block)
        produced += len(block)
        counter += 1
    return b"".join(blocks)[:length]


def _xor(data: bytes, stream: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, stream))


def encrypt_mapping(mapping: dict, passphrase: str) -> dict:
    plaintext = json.dumps(mapping, ensure_ascii=False).encode("utf-8")
    salt = os.urandom(16)
    nonce = os.urandom(16)
    key = _derive(passphrase, salt)
    ciphertext = _xor(plaintext, _keystream(key, nonce, len(plaintext)))
    tag = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    return {
        "v": 2,
        "kdf": "scrypt",
        "cipher": "hmac-ctr-sha256",
        "n": KDF_N,
        "r": KDF_R,
        "p": KDF_P,
        "salt": _b64(salt),
        "nonce": _b64(nonce),
        "ct": _b64(ciphertext),
        "tag": _b64(tag),
    }


def is_encrypted(data) -> bool:
    return isinstance(data, dict) and data.get("v") == 2 and data.get("cipher") == "hmac-ctr-sha256"


def decrypt_mapping(payload: dict, passphrase: str) -> dict:
    if payload.get("n") != KDF_N or payload.get("r") != KDF_R or payload.get("p") != KDF_P:
        raise ValueError("映射表的加密参数不受支持")
    salt = _unb64(payload["salt"])
    nonce = _unb64(payload["nonce"])
    ciphertext = _unb64(payload["ct"])
    tag = _unb64(payload["tag"])
    key = _derive(passphrase, salt)
    expected = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, tag):
        raise ValueError("口令不对，或映射表被改过")
    plaintext = _xor(ciphertext, _keystream(key, nonce, len(ciphertext)))
    return json.loads(plaintext.decode("utf-8"))


def save_mapping(path: str, mapping: dict, passphrase: str | None = None) -> None:
    payload = encrypt_mapping(mapping, passphrase) if passphrase else mapping
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.chmod(path, 0o600)


def load_mapping(path: str, passphrase: str | None = None) -> dict:
    data = json.load(open(path, encoding="utf-8"))
    if not is_encrypted(data):
        return data
    if not passphrase:
        raise ValueError("这是一份加密映射表，需要口令（--passphrase 或环境变量 CONTRACT_REDACT_PASSPHRASE）")
    return decrypt_mapping(data, passphrase)
