from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


_CACHE_TTL_SECONDS = 60.0
_cached_bundle: dict[str, Any] | None = None
_cached_at: float | None = None
_cached_db_mtime_ns: int | None = None

_KEYCHAIN_SERVICES = (
    "Chrome Safe Storage",
    "Google Chrome Safe Storage",
    "Chromium Safe Storage",
)

_RELEVANT_COOKIE_NAMES = {
    "XSRF-TOKEN",
    "doc_atoken",
    "wld_stoken",
    "tfstk",
    "cna",
    "isg",
    "HMACCOUNT",
    "dt_s",
    "dd_home_locale",
    "deviceid",
    "RECENT_OPEN_DOC_KEYS",
    "account",
    "pub_uid",
    "pub_org_id",
    "arms_uid",
    "ding_doc_unified_login",
    "wolai_client_id",
    "xlly_s",
    "portal_corp_id",
}


class DingtalkBrowserAuthError(RuntimeError):
    pass


def _default_cookie_db_path() -> Path:
    return Path.home() / "Library/Application Support/Google/Chrome/Default/Cookies"


def _load_keychain_secret() -> str:
    for service in _KEYCHAIN_SERVICES:
        command = ["security", "find-generic-password", "-ws", service]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    raise DingtalkBrowserAuthError("unable to read Chrome Safe Storage secret from macOS keychain")


def _derive_cookie_key(secret: str) -> bytes:
    return hashlib.pbkdf2_hmac("sha1", secret.encode("utf-8"), b"saltysalt", 1003, dklen=16)


def _decrypt_cookie_value(*, host_key: str, encrypted_value: bytes, key: bytes) -> str:
    if not encrypted_value:
        return ""
    if encrypted_value.startswith((b"v10", b"v11")):
        payload = encrypted_value[3:]
        result = subprocess.run(
            [
                "openssl",
                "enc",
                "-d",
                "-aes-128-cbc",
                "-K",
                key.hex(),
                "-iv",
                "20" * 16,
                "-nopad",
            ],
            input=payload,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise DingtalkBrowserAuthError(
                f"openssl failed to decrypt Chrome cookie for host {host_key}: {result.stderr.decode('utf-8', errors='ignore').strip()}"
            )
        plaintext = result.stdout
        if not plaintext:
            return ""
        pad = plaintext[-1]
        if 1 <= pad <= 16 and plaintext.endswith(bytes([pad]) * pad):
            plaintext = plaintext[:-pad]
        host_digest = hashlib.sha256(host_key.encode("utf-8")).digest()
        if plaintext.startswith(host_digest):
            plaintext = plaintext[len(host_digest) :]
        return plaintext.decode("utf-8", errors="ignore")
    return encrypted_value.decode("utf-8", errors="ignore")


def _copy_cookie_db(cookie_db_path: Path) -> Path:
    fd, temp_path = tempfile.mkstemp(prefix="dingtalk-cookies-", suffix=".sqlite")
    os.close(fd)
    temp_file = Path(temp_path)
    temp_file.write_bytes(cookie_db_path.read_bytes())
    return temp_file


def _load_dingtalk_cookies(cookie_db_path: Path) -> tuple[dict[str, str], list[str]]:
    if not cookie_db_path.exists():
        raise DingtalkBrowserAuthError(f"Chrome cookie db not found: {cookie_db_path}")

    temp_db = _copy_cookie_db(cookie_db_path)
    key = _derive_cookie_key(_load_keychain_secret())
    cookies: dict[str, str] = {}
    matched_hosts: set[str] = set()
    try:
        connection = sqlite3.connect(str(temp_db))
        rows = connection.execute(
            """
            SELECT host_key, name, encrypted_value
            FROM cookies
            WHERE host_key LIKE '%dingtalk.com%'
            ORDER BY host_key, name
            """
        ).fetchall()
        for host_key, name, encrypted_value in rows:
            if name not in _RELEVANT_COOKIE_NAMES:
                continue
            if not isinstance(host_key, str) or not isinstance(name, str):
                continue
            if not isinstance(encrypted_value, (bytes, bytearray)):
                continue
            try:
                decrypted = _decrypt_cookie_value(host_key=host_key, encrypted_value=bytes(encrypted_value), key=key)
            except DingtalkBrowserAuthError:
                continue
            if not decrypted:
                continue
            cookies[name] = decrypted
            matched_hosts.add(host_key)
    finally:
        try:
            connection.close()
        except Exception:
            pass
        temp_db.unlink(missing_ok=True)

    if not cookies:
        raise DingtalkBrowserAuthError("no usable dingtalk cookies could be decrypted from local Chrome profile")
    return cookies, sorted(matched_hosts)


def _host_suffixes(hostname: str) -> tuple[str, ...]:
    parts = [segment for segment in hostname.split(".") if segment]
    suffixes: list[str] = []
    for index in range(len(parts)):
        suffixes.append(".".join(parts[index:]))
    return tuple(dict.fromkeys(suffixes))


def resolve_dingtalk_browser_auth(
    *,
    source_url: str,
    cookie_db_path: Path | None = None,
) -> dict[str, Any]:
    global _cached_bundle, _cached_at, _cached_db_mtime_ns

    parsed = urlparse(source_url)
    hostname = parsed.hostname or ""
    if "dingtalk.com" not in hostname:
        return {
            "enabled": False,
            "auth_source": "not_applicable",
            "cookies": {},
            "headers": {},
            "matched_hosts": [],
        }

    cookie_db = cookie_db_path or _default_cookie_db_path()
    db_mtime_ns = cookie_db.stat().st_mtime_ns if cookie_db.exists() else -1
    now = time.time()
    if (
        _cached_bundle is not None
        and _cached_at is not None
        and _cached_db_mtime_ns == db_mtime_ns
        and now - _cached_at < _CACHE_TTL_SECONDS
    ):
        return dict(_cached_bundle)

    cookies, matched_hosts = _load_dingtalk_cookies(cookie_db)
    filtered_cookies: dict[str, str] = {}
    suffixes = _host_suffixes(hostname)
    for name, value in cookies.items():
        filtered_cookies[name] = value

    headers: dict[str, str] = {}
    xsrf_token = filtered_cookies.get("XSRF-TOKEN")
    if xsrf_token:
        headers["X-XSRF-Token"] = xsrf_token

    bundle = {
        "enabled": True,
        "auth_source": "local_chrome_cookie_db",
        "cookie_db_path": str(cookie_db),
        "cookie_db_mtime_ns": db_mtime_ns,
        "cookies": filtered_cookies,
        "headers": headers,
        "matched_hosts": matched_hosts,
        "hostname_suffixes": suffixes,
    }
    _cached_bundle = dict(bundle)
    _cached_at = now
    _cached_db_mtime_ns = db_mtime_ns
    return bundle
