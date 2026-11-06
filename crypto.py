"""CryptoNote 加密核心模块。

设计要点（安全相关）：
- 主密码永不落盘，只在内存中派生出对称密钥。
- 密钥派生用 scrypt（标准库 hashlib，无需任何 C/Rust 编译，ARM64 直接可用）。
- 加密用 AES-256-GCM（pycryptodome），认证加密，自带防篡改。
- 每条笔记独立随机 nonce；用笔记 id 作为 AAD 绑定密文，防止条目被整体调换。
- 单文件保险库（JSON），存 KDF 参数 + 一个校验块 + 各笔记密文。
"""

import json
import os
import secrets
import hashlib
import base64
import tempfile
from datetime import datetime, timezone

from Crypto.Cipher import AES

VERSION = 1

# scrypt 参数。n=2^15,r=8,p=1 约需 ~33MB 内存，maxmem 显式放宽到 128MB。
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32          # AES-256
SCRYPT_MAXMEM = 128 * 1024 * 1024

SALT_BYTES = 16
NONCE_BYTES = 12           # GCM 推荐 96-bit nonce

TITLE_MAX_LENGTH = 256     # 笔记标题最长字符数（界面 + Vault 双层校验）

# 解锁时用来校验主密码是否正确的固定标记（明文已知，靠 GCM 认证判断密码对错）。
_CHECK_PLAINTEXT = b"CryptoNote.vault.check.v1"
_CHECK_AAD = b"vault-check"


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class VaultError(Exception):
    """保险库相关的通用错误。"""


class WrongPassword(VaultError):
    """主密码错误，或文件被篡改导致认证失败。"""


def derive_key(password: str, salt: bytes) -> bytes:
    """用 scrypt 从主密码 + salt 派生 32 字节密钥。"""
    if not isinstance(password, str) or password == "":
        raise VaultError("主密码不能为空")
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=SCRYPT_MAXMEM,
    )


def _encrypt(key: bytes, plaintext: bytes, aad: bytes) -> dict:
    """AES-256-GCM 加密，返回含 nonce/ct/tag 的 dict。"""
    nonce = secrets.token_bytes(NONCE_BYTES)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    cipher.update(aad)
    ct, tag = cipher.encrypt_and_digest(plaintext)
    return {"nonce": _b64e(nonce), "ct": _b64e(ct), "tag": _b64e(tag)}


def _decrypt(key: bytes, blob: dict, aad: bytes) -> bytes:
    """AES-256-GCM 解密，认证失败抛 WrongPassword。"""
    try:
        nonce = _b64d(blob["nonce"])
        ct = _b64d(blob["ct"])
        tag = _b64d(blob["tag"])
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise VaultError("密文格式损坏") from exc
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    cipher.update(aad)
    try:
        return cipher.decrypt_and_verify(ct, tag)
    except ValueError as exc:
        raise WrongPassword("主密码错误或数据被篡改") from exc


def _is_blob(value) -> bool:
    """判断一个值是不是合法的密文块：含 nonce/ct/tag 三个字符串字段。"""
    return (
        isinstance(value, dict)
        and isinstance(value.get("nonce"), str)
        and isinstance(value.get("ct"), str)
        and isinstance(value.get("tag"), str)
    )


def _validate_rec(rec) -> dict:
    """校验一条笔记记录的结构：必须是 dict，且 title/body 都是合法密文块。

    结构损坏统一转 VaultError，绝不让 KeyError/TypeError 逃到调用方。
    """
    if not isinstance(rec, dict) or not _is_blob(rec.get("title")) or not _is_blob(rec.get("body")):
        raise VaultError("笔记数据损坏")
    return rec


def _check_title(title: str) -> None:
    """校验标题：必须是字符串且不超过上限。"""
    if not isinstance(title, str):
        raise VaultError("标题必须是字符串")
    if len(title) > TITLE_MAX_LENGTH:
        raise VaultError(f"标题最多 {TITLE_MAX_LENGTH} 个字符")


class Vault:
    """加密笔记库。

    典型用法：
        v = Vault.create(path, password)        # 新建
        v = Vault.open(path, password)          # 打开已有
        v.add_note(title, body)
        v.list_notes()                          # [{id,title,updated}, ...]
        v.get_body(note_id)
        v.update_note(note_id, title, body)
        v.delete_note(note_id)
    """

    def __init__(self, path: str, key: bytes, data: dict):
        self.path = path
        self._key = key
        self._data = data           # 已解密的内存态：{salt,check,notes:{id:{...}}}
        self._title_cache = {}      # 标题缓存：{note_id: title}，避免重复解密

    # ---------- 创建 / 打开 ----------

    @classmethod
    def create(cls, path: str, password: str) -> "Vault":
        if os.path.exists(path):
            raise VaultError("文件已存在，请用打开方式")
        salt = secrets.token_bytes(SALT_BYTES)
        key = derive_key(password, salt)
        data = {
            "salt": salt,
            "check": _encrypt(key, _CHECK_PLAINTEXT, _CHECK_AAD),
            "notes": {},
        }
        vault = cls(path, key, data)
        vault._save()
        return vault

    @classmethod
    def open(cls, path: str, password: str) -> "Vault":
        if not os.path.exists(path):
            raise VaultError("文件不存在")
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            raise VaultError("文件损坏：不是有效的保险库文件") from exc
        if not isinstance(raw, dict):
            raise VaultError("文件损坏：不是有效的保险库文件")
        if raw.get("version") != VERSION:
            raise VaultError(f"不支持的文件版本: {raw.get('version')}")
        try:
            salt = _b64d(raw["kdf"]["salt"])
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise VaultError("文件头损坏") from exc
        if "check" not in raw:
            raise VaultError("文件头损坏")
        notes = raw.get("notes", {})
        if not isinstance(notes, dict):
            raise VaultError("文件损坏：笔记数据无效")
        key = derive_key(password, salt)
        # 校验主密码：解开 check 块，失败说明密码错或文件被篡改。
        _decrypt(key, raw["check"], _CHECK_AAD)
        data = {
            "salt": salt,
            "check": raw["check"],
            "notes": dict(notes),
        }
        return cls(path, key, data)

    # ---------- 落盘 ----------

    def _serialize(self) -> dict:
        return {
            "version": VERSION,
            "kdf": {
                "algo": "scrypt",
                "salt": _b64e(self._data["salt"]),
                "n": SCRYPT_N,
                "r": SCRYPT_R,
                "p": SCRYPT_P,
                "dklen": SCRYPT_DKLEN,
            },
            "cipher": "AES-256-GCM",
            "check": self._data["check"],
            "notes": self._data["notes"],
        }

    def _save(self) -> None:
        """原子写：先写临时文件再替换，避免写一半损坏库文件。"""
        payload = json.dumps(self._serialize(), ensure_ascii=False, indent=2)
        d = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    # ---------- 笔记 CRUD ----------

    def list_notes(self) -> list:
        """返回笔记元信息列表（已解密标题），按更新时间倒序。"""
        out = []
        for nid, rec in self._data["notes"].items():
            try:
                _validate_rec(rec)
            except VaultError:
                # 记录结构损坏：不让列表整体崩掉，显示占位标题。
                out.append({"id": nid, "title": "（无法解密）", "updated": ""})
                continue
            # 先查缓存，避免重复解密标题
            if nid in self._title_cache:
                title = self._title_cache[nid]
            else:
                try:
                    title = _decrypt(self._key, rec["title"], nid.encode("utf-8")).decode("utf-8")
                    self._title_cache[nid] = title
                except (VaultError, UnicodeDecodeError):
                    title = "（无法解密）"
            updated = rec.get("updated", "")
            if not isinstance(updated, str):
                updated = ""
            out.append({"id": nid, "title": title, "updated": updated})
        out.sort(key=lambda x: x["updated"], reverse=True)
        return out

    def get_body(self, note_id: str) -> str:
        rec = self._data["notes"].get(note_id)
        if rec is None:
            raise VaultError("笔记不存在")
        _validate_rec(rec)
        try:
            return _decrypt(self._key, rec["body"], note_id.encode("utf-8")).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise VaultError("笔记数据损坏") from exc

    def add_note(self, title: str, body: str = "") -> str:
        _check_title(title)
        nid = secrets.token_hex(16)
        aad = nid.encode("utf-8")
        now = _now_iso()
        self._data["notes"][nid] = {
            "title": _encrypt(self._key, title.encode("utf-8"), aad),
            "body": _encrypt(self._key, body.encode("utf-8"), aad),
            "created": now,
            "updated": now,
        }
        self._title_cache[nid] = title  # 新笔记直接缓存标题
        self._save()
        return nid

    def update_note(self, note_id: str, title: str, body: str) -> None:
        rec = self._data["notes"].get(note_id)
        if rec is None:
            raise VaultError("笔记不存在")
        _validate_rec(rec)
        _check_title(title)
        aad = note_id.encode("utf-8")
        rec["title"] = _encrypt(self._key, title.encode("utf-8"), aad)
        rec["body"] = _encrypt(self._key, body.encode("utf-8"), aad)
        rec["updated"] = _now_iso()
        self._title_cache[note_id] = title  # 更新缓存
        self._save()

    def delete_note(self, note_id: str) -> None:
        if note_id not in self._data["notes"]:
            raise VaultError("笔记不存在")
        _validate_rec(self._data["notes"][note_id])
        del self._data["notes"][note_id]
        self._title_cache.pop(note_id, None)  # 删除缓存
        self._save()

    def change_password(self, new_password: str) -> None:
        """改主密码：换 salt 重新派生密钥，全部笔记用新密钥重加密。"""
        # 先把所有笔记解密出来（标题、正文）。任一笔记损坏都直接抛错，
        # 此时密钥尚未切换，原文件仍可用旧密码打开。
        plain = {}
        for nid, rec in self._data["notes"].items():
            _validate_rec(rec)
            try:
                body = _decrypt(self._key, rec["body"], nid.encode("utf-8")).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise VaultError("笔记数据损坏") from exc
            title = self._title_cache.get(nid)
            if not title:
                try:
                    title = _decrypt(self._key, rec["title"], nid.encode("utf-8")).decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise VaultError("笔记数据损坏") from exc
            plain[nid] = (body, title)

        new_salt = secrets.token_bytes(SALT_BYTES)
        new_key = derive_key(new_password, new_salt)
        self._key = new_key
        self._data["salt"] = new_salt
        self._data["check"] = _encrypt(new_key, _CHECK_PLAINTEXT, _CHECK_AAD)

        # 用新密钥重新加密所有笔记
        for nid, (body, title) in plain.items():
            aad = nid.encode("utf-8")
            self._data["notes"][nid]["title"] = _encrypt(new_key, title.encode("utf-8"), aad)
            self._data["notes"][nid]["body"] = _encrypt(new_key, body.encode("utf-8"), aad)
            self._title_cache[nid] = title  # 缓存仍然有效
        self._save()
