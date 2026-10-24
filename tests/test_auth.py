"""本地 API 令牌鉴权的单元测试：中间件行为和令牌管理。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from app import auth


def make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(auth.TokenAuthMiddleware)

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    @app.get("/api/status")
    async def status():
        return {"ok": True}

    @app.websocket("/api/live/ws")
    async def live_ws(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("hi")
        await websocket.close()

    @app.get("/")
    async def root():
        return {"ok": True}

    return app


@pytest.fixture()
def token(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "TOKEN_FILE", tmp_path / "local_token")
    monkeypatch.setattr(auth, "env", SimpleNamespace(local_api_token=""))
    return auth.get_or_create_token()


@pytest.fixture()
def client(token):
    return TestClient(make_app())


def test_missing_token_is_401_and_health_exempt(client):
    assert client.get("/api/health").status_code == 200
    resp = client.get("/api/status")
    assert resp.status_code == 401
    assert "令牌" in resp.json()["detail"]


def test_bearer_header_and_query_token(client, token):
    assert client.get("/api/status", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert client.get(f"/api/status?token={token}").status_code == 200
    assert client.get("/api/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/status?token=wrong").status_code == 401


def test_non_api_paths_not_protected(client):
    assert client.get("/").status_code == 200


def test_websocket_requires_query_token(client, token):
    import pytest as _pytest

    with _pytest.raises(Exception):
        with client.websocket_connect("/api/live/ws"):
            pass
    with client.websocket_connect(f"/api/live/ws?token={token}") as ws:
        assert ws.receive_text() == "hi"


def test_token_file_reuse_and_env_override(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "TOKEN_FILE", tmp_path / "local_token")
    monkeypatch.setattr(auth, "env", SimpleNamespace(local_api_token=""))
    first = auth.get_or_create_token()
    assert first and auth.TOKEN_FILE.exists()
    assert auth.get_or_create_token() == first
    monkeypatch.setattr(auth, "env", SimpleNamespace(local_api_token="env-token"))
    assert auth.get_or_create_token() == "env-token"
    assert auth.verify("env-token") and not auth.verify(first)
