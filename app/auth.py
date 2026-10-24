"""本地 API 的最小令牌鉴权。

- 令牌来源：.env 的 ``LOCAL_API_TOKEN`` > ``data/local_token`` 文件（首次启动自动生成）。
- 校验方式：``Authorization: Bearer <token>`` 请求头，或 ``?token=<token>`` 查询参数
  （EventSource 和 WebSocket 无法自定义请求头，只能走查询参数）。
- 范围：``/api/*`` 全部要求令牌；``GET /api/health`` 豁免（start.ps1 的就绪探测），
  OPTIONS 豁免（CORS 预检）。``/media``、``/debug`` 和前端静态资源不在保护范围内。
"""
from __future__ import annotations

import hmac
import os
import secrets
import stat
from typing import Any
from urllib.parse import parse_qs

from starlette.responses import JSONResponse

from .config import DATA_DIR, env

TOKEN_FILE = DATA_DIR / "local_token"
_EXEMPT_PATHS = {"/api/health"}


def _write_token_file(token: str) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token, encoding="utf-8")
    try:
        os.chmod(TOKEN_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def get_or_create_token() -> str:
    """取当前生效令牌；文件不存在时生成并落盘。"""
    env_token = env.local_api_token.strip()
    if env_token:
        return env_token
    try:
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if token:
        return token
    token = secrets.token_urlsafe(24)
    _write_token_file(token)
    return token


def verify(token: str | None) -> bool:
    expected = get_or_create_token()
    if not token:
        return False
    return hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))


def _token_from_scope(scope: dict[str, Any]) -> str | None:
    headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers") or []
    }
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    query = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
    values = query.get("token")
    return values[0] if values else None


class TokenAuthMiddleware:
    """纯 ASGI 中间件，HTTP 与 WebSocket 升级请求都拦。"""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if not path.startswith("/api"):
            await self.app(scope, receive, send)
            return
        if path in _EXEMPT_PATHS or (scope["type"] == "http" and scope.get("method") == "OPTIONS"):
            await self.app(scope, receive, send)
            return
        if verify(_token_from_scope(scope)):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": "invalid token"})
            return
        response = JSONResponse({"detail": "缺少或错误的本地访问令牌"}, status_code=401)
        await response(scope, receive, send)
