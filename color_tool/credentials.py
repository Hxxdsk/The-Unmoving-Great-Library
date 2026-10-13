"""用户账户绑定的 API Key 保存；普通偏好文件始终不含凭据。"""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path


class CredentialError(RuntimeError):
    pass


def _dpapi(data: bytes, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise CredentialError("当前系统未提供 Windows 凭据保护，Key 仅本次使用。")
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    protect = crypt.CryptProtectData
    unprotect = crypt.CryptUnprotectData
    protect.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.POINTER(Blob),
                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    unprotect.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    protect.restype = unprotect.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, output = Blob(len(data), buffer), Blob()
    if decrypt:
        success = unprotect(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    else:
        success = protect(ctypes.byref(source), "GreatLibrary Gemini Key", None, None, None, 1, ctypes.byref(output))
    if not success:
        raise CredentialError(f"Windows 凭据保护失败（{ctypes.get_last_error()}）。")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


class KeyStore:
    def __init__(self, path: Path, codec=_dpapi):
        self.path = path
        self.codec = codec
        self.validated_model = ""

    def load(self) -> str:
        if not self.path.exists():
            return ""
        try:
            payload = json.loads(self.codec(self.path.read_bytes(), True).decode("utf-8"))
            if (not isinstance(payload, dict) or not isinstance(payload.get("key"), str)
                    or not isinstance(payload.get("validated_model", ""), str)):
                raise ValueError("invalid credential record")
            self.validated_model = payload.get("validated_model", "")
            return payload["key"]
        except (OSError, ValueError) as exc:
            raise CredentialError("保存的 Key 无法读取，请在当前账户重新填写。") from exc

    def save(self, key: str, validated_model: str = ""):
        if not key.strip():
            return
        payload = json.dumps({"key": key.strip(), "validated_model": validated_model}).encode("utf-8")
        encoded = self.codec(payload, False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(encoded)
        temporary.replace(self.path)
        self.validated_model = validated_model

    def forget(self):
        self.path.unlink(missing_ok=True)
