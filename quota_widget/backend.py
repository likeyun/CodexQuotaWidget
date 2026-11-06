"""Read Codex credentials without modifying them and serve usage on loopback.

The wham endpoint is an internal ChatGPT endpoint, so its response is deliberately
kept alongside a small, nullable presentation model. No credential is persisted,
logged, or returned by this module.
"""

from __future__ import annotations

import base64
import copy
import json
import math
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
BEIJING = timezone(timedelta(hours=8), name="Asia/Shanghai")
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MIN_REFRESH_SECONDS = 10.0


class UsageError(Exception):
    """An error containing only a fixed, safe message for the local client."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, repr=False)
class Credentials:
    access_token: str
    account_id: str

    def __repr__(self) -> str:
        return "Credentials(<redacted>)"


def auth_file_path(auth_path: str | Path | None = None) -> Path:
    """Resolve the credential file without reading or exposing its contents."""
    if auth_path is not None:
        return Path(os.path.expandvars(str(auth_path))).expanduser()
    if os.environ.get("CODEX_AUTH_FILE"):
        return Path(os.path.expandvars(os.environ["CODEX_AUTH_FILE"])).expanduser()
    if os.environ.get("CODEX_HOME"):
        return Path(os.path.expandvars(os.environ["CODEX_HOME"])).expanduser() / "auth.json"
    return Path.home() / ".codex" / "auth.json"


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _jwt_account_id(token: Any) -> str | None:
    """Extract an account hint, not verify or authenticate a JWT locally."""
    if not isinstance(token, str):
        return None
    try:
        part = token.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        if not isinstance(payload, dict):
            return None
        claims = payload.get("https://api.openai.com/auth", {})
        return _text(claims.get("chatgpt_account_id")) if isinstance(claims, dict) else None
    except (IndexError, ValueError, TypeError, UnicodeDecodeError):
        return None


def read_credentials(auth_path: str | Path | None = None) -> Credentials:
    """Read current tokens on every refresh; never refresh or rewrite auth.json."""
    token = _text(os.environ.get("CHATGPT_ACCESS_TOKEN"))
    account_id = _text(os.environ.get("CHATGPT_ACCOUNT_ID"))
    if token:
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        account_id = account_id or _jwt_account_id(token)
    else:
        try:
            path = auth_file_path(auth_path)
            if path.stat().st_size > MAX_RESPONSE_BYTES:
                raise UsageError("auth_invalid", "登录凭据文件格式无效，请重新登录 Codex。")
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            raise UsageError("auth_missing", "未找到 auth.json，请先在 Codex 中使用 ChatGPT 账号登录。") from None
        except (OSError, ValueError, UnicodeError):
            raise UsageError("auth_invalid", "无法读取登录凭据，请检查文件权限或重新登录 Codex。") from None
        if not isinstance(data, dict):
            raise UsageError("auth_invalid", "登录凭据文件格式无效，请重新登录 Codex。")
        tokens = data.get("tokens")
        tokens = tokens if isinstance(tokens, dict) else {}
        token = _text(tokens.get("access_token")) or _text(data.get("access_token"))
        if not token:
            if data.get("OPENAI_API_KEY") or data.get("auth_mode") == "apikey":
                raise UsageError("chatgpt_login_required", "当前使用 API Key 登录。读取订阅额度需要在 Codex 中使用 ChatGPT 账号登录。")
            raise UsageError("auth_token_missing", "凭据中没有 ChatGPT 访问令牌，请重新登录 Codex。")
        account_id = (
            account_id
            or _text(tokens.get("account_id"))
            or _text(data.get("account_id"))
            or _jwt_account_id(token)
            or _jwt_account_id(tokens.get("id_token"))
        )
    if not token or any(c in token for c in ("\r", "\n", "\0")):
        raise UsageError("auth_invalid", "访问令牌格式无效，请重新登录 Codex。")
    if not account_id:
        raise UsageError("auth_account_missing", "凭据缺少 ChatGPT 账号标识，请重新登录 Codex。")
    if any(c in account_id for c in ("\r", "\n", "\0")):
        raise UsageError("auth_invalid", "账号标识格式无效，请重新登录 Codex。")
    return Credentials(token, account_id)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the user's bearer token to a redirected origin.
        return None


def _finite_json_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite JSON number")
    return result


def _reject_json_constant(value: str):
    raise ValueError("Non-finite JSON constant")


def _https_proxy() -> str:
    # urllib reads Windows Internet Settings as well as *_PROXY environment
    # variables; libcurl alone does not discover the Windows system proxy.
    if urllib.request.proxy_bypass("chatgpt.com"):
        return ""
    proxies = urllib.request.getproxies()
    return proxies.get("https") or proxies.get("all") or ""


def _request_usage(credentials: Credentials) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {credentials.access_token}",
        "ChatGPT-Account-Id": credentials.account_id,
        "Accept": "application/json",
    }
    try:
        try:
            from curl_cffi import requests as curl_requests
        except ImportError:
            curl_requests = None
        if curl_requests is not None:
            response = curl_requests.get(
                USAGE_URL, headers=headers, impersonate="chrome",
                timeout=15, allow_redirects=False, proxy=_https_proxy(),
            )
            status = response.status_code
            body = response.content
        else:
            request = urllib.request.Request(USAGE_URL, headers=headers, method="GET")
            try:
                with urllib.request.build_opener(_NoRedirect()).open(request, timeout=15) as response:
                    status = response.status
                    body = response.read(MAX_RESPONSE_BYTES + 1)
            except urllib.error.HTTPError as response:
                status = response.code
                body = b""  # Error bodies may contain credentials; never expose them.
    except Exception:
        raise UsageError("network_error", "无法连接额度服务，请检查网络或代理设置。") from None
    if status == 401:
        raise UsageError("unauthorized", "登录凭据已过期或失效，请在 Codex 中重新登录后刷新。")
    if status == 403:
        raise UsageError("forbidden", "额度服务拒绝访问，请确认账号权限并检查网络。")
    if status == 429:
        raise UsageError("rate_limited", "额度服务请求过于频繁，请稍后重试。")
    if status != 200:
        raise UsageError("upstream_error", f"额度服务暂时不可用（HTTP {int(status)}），请稍后重试。")
    if len(body) > MAX_RESPONSE_BYTES:
        raise UsageError("upstream_invalid", "额度服务返回的数据过大。")
    try:
        data = json.loads(body, parse_float=_finite_json_float, parse_constant=_reject_json_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise UsageError("upstream_invalid", "额度服务未返回有效 JSON，请稍后重试。") from None
    if not isinstance(data, dict) or not any(key in data for key in ("rate_limit", "plan_type", "credits")):
        raise UsageError("upstream_schema_changed", "额度服务返回结构已变化，暂时无法读取。")
    return data


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _normalize_window(window: Any, now: float) -> dict[str, Any] | None:
    if not isinstance(window, dict):
        return None
    used = _number(window.get("used_percent"))
    used = min(100.0, max(0.0, used)) if used is not None else None
    duration = _number(window.get("limit_window_seconds"))
    reset_at = _number(window.get("reset_at"))
    reset_after = _number(window.get("reset_after_seconds"))
    if reset_at is None and reset_after is not None:
        reset_at = now + max(0, reset_after)
    reset_text = None
    if reset_at is not None:
        try:
            reset_text = datetime.fromtimestamp(reset_at, BEIJING).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OverflowError, OSError):
            reset_at = None
    return {
        "used_percent": used,
        "remaining_percent": round(100.0 - used, 4) if used is not None else None,
        "limit_window_seconds": int(duration) if duration is not None and duration >= 0 else None,
        "reset_at": int(reset_at) if reset_at is not None else None,
        "reset_at_beijing": reset_text,
        "reset_after_seconds": max(0, math.ceil(reset_at - now)) if reset_at is not None else None,
    }


def _redact(value: Any, access_token: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]" if str(key).lower() in {
                "access_token", "refresh_token", "id_token", "authorization", "api_key", "openai_api_key"
            } else _redact(item, access_token)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, access_token) for item in value]
    if isinstance(value, str) and access_token and access_token in value:
        return value.replace(access_token, "[redacted]")
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def normalize_usage(raw: dict[str, Any], now: float | None = None, access_token: str | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    limits = raw.get("rate_limit")
    limits = limits if isinstance(limits, dict) else {}
    credits = raw.get("credits")
    return {
        "ok": True,
        "stale": False,
        "fetched_at": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "plan_type": _text(raw.get("plan_type")) or "unknown",
        "windows": {
            "primary": _normalize_window(limits.get("primary_window"), now),
            "secondary": _normalize_window(limits.get("secondary_window"), now),
        },
        "credits": _redact(credits, access_token) if isinstance(credits, dict) else None,
        "raw": _redact(raw, access_token),
        "error": None,
    }


def _empty_result() -> dict[str, Any]:
    return {
        "ok": False, "stale": False, "fetched_at": None,
        "plan_type": "unknown", "windows": {"primary": None, "secondary": None},
        "credits": None, "raw": None, "error": None,
    }


class UsageService:
    """An in-memory single-flight cache shared by desktop and HTTP requests."""

    def __init__(self, auth_path: str | Path | None = None, cache_seconds: float = 60):
        self.auth_path = auth_path
        self.cache_seconds = max(MIN_REFRESH_SECONDS, float(cache_seconds))
        self._lock = threading.Lock()
        self._result: dict[str, Any] | None = None
        self._last_success: dict[str, Any] | None = None
        self._last_attempt: float | None = None

    def health(self) -> dict[str, Any]:
        return {"ok": True, "service": "codex-quota", "version": "1.0.0", "cache_seconds": self.cache_seconds}

    def get_usage(self, force: bool = False) -> dict[str, Any]:
        with self._lock:
            age = time.monotonic() - self._last_attempt if self._last_attempt is not None else math.inf
            interval = MIN_REFRESH_SECONDS if force else self.cache_seconds
            if self._result is None or age >= interval:
                try:
                    credentials = read_credentials(self.auth_path)
                    raw = _request_usage(credentials)
                    self._result = normalize_usage(raw, access_token=credentials.access_token)
                    self._last_success = copy.deepcopy(self._result)
                except UsageError as error:
                    self._result = copy.deepcopy(self._last_success) if self._last_success else _empty_result()
                    self._result.update(ok=False, stale=self._last_success is not None, error=error.as_dict())
                except Exception:
                    # Avoid tracebacks containing a request object's Authorization header.
                    self._result = copy.deepcopy(self._last_success) if self._last_success else _empty_result()
                    self._result.update(ok=False, stale=self._last_success is not None, error={
                        "code": "internal_error", "message": "暂时无法读取额度，请稍后刷新。"
                    })
                self._last_attempt = time.monotonic()
            result = copy.deepcopy(self._result)
        now = time.time()
        for window in result["windows"].values():
            if window is not None and window["reset_at"] is not None:
                window["reset_after_seconds"] = max(0, math.ceil(window["reset_at"] - now))
        return result


class _LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        # Windows SO_REUSEADDR permits a second listener on the same address.
        # Claim the port exclusively so the API never mixes with another app.
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def start_api(service: UsageService, port: int = 18765) -> tuple[ThreadingHTTPServer, threading.Thread]:
    """Start a loopback-only API, raising OSError when its port is unavailable."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "CodexQuota/1.0"
        sys_version = ""

        def log_message(self, format, *args):
            pass  # No request URLs or authentication data in logs.

        def _json(self, status: int, payload: dict[str, Any]):
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def _allowed(self) -> bool:
            bound_port = self.server.server_port
            hosts = {f"127.0.0.1:{bound_port}", f"localhost:{bound_port}"}
            if bound_port == 80:
                hosts.update({"127.0.0.1", "localhost"})
            host_values = self.headers.get_all("Host", [])
            if len(host_values) != 1 or host_values[0].lower() not in hosts:
                self._json(403, {"ok": False, "error": {"code": "host_forbidden", "message": "仅允许本机地址访问。"}})
                return False
            origins = self.headers.get_all("Origin", [])
            allowed_origins = {f"http://{host}" for host in hosts}
            if len(origins) > 1 or (origins and origins[0].lower() not in allowed_origins):
                self._json(403, {"ok": False, "error": {"code": "origin_forbidden", "message": "不允许跨站访问本机额度。"}})
                return False
            if self.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
                self._json(403, {"ok": False, "error": {"code": "origin_forbidden", "message": "不允许跨站访问本机额度。"}})
                return False
            return True

        def do_GET(self):
            if not self._allowed():
                return
            try:
                path = urlsplit(self.path).path
            except ValueError:
                self._json(400, {"ok": False, "error": {"code": "invalid_path", "message": "请求路径无效。"}})
                return
            if path == "/api/health":
                self._json(200, service.health())
            elif path == "/api/usage":
                result = service.get_usage()  # Query strings never bypass the cache.
                self._json(200 if result["ok"] or result["stale"] else 503, result)
            elif path == "/":
                self._json(200, {"service": "codex-quota", "endpoints": ["/api/health", "/api/usage"]})
            else:
                self._json(404, {"ok": False, "error": {"code": "not_found", "message": "接口不存在。"}})

        def _method_not_allowed(self):
            if self._allowed():
                self._json(405, {"ok": False, "error": {"code": "method_not_allowed", "message": "仅支持 GET 请求。"}})

        do_POST = _method_not_allowed
        do_PUT = _method_not_allowed
        do_DELETE = _method_not_allowed
        do_OPTIONS = _method_not_allowed

    server = _LocalHTTPServer(("127.0.0.1", int(port)), Handler)
    thread = threading.Thread(target=server.serve_forever, name="quota-local-api", daemon=True)
    thread.start()
    return server, thread
