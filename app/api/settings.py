"""设置与浏览器控制路由：设置读写、API Key、模型连通性、专用浏览器开关。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..agent import runner
from ..config import (
    AgentSettings,
    clear_api_key,
    load_api_key,
    save_api_key,
    settings_public,
)
from ..events import bus
from ..llm import DeepSeekDecider, LLMError
from ..services import replier, sharer

router = APIRouter()


class ApiKeyBody(BaseModel):
    api_key: str = Field(default="", max_length=300)


@router.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    return settings_public(runner.settings, bool(load_api_key()))


@router.put("/api/settings")
async def put_settings(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        settings = AgentSettings.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"设置不合法：{exc}") from exc
    if settings.feed_url and not settings.feed_url.startswith("https://www.douyin.com"):
        raise HTTPException(status_code=422, detail="feed_url 只允许 www.douyin.com")
    saved = db.save_settings(settings)
    runner.refresh_settings()
    await replier.apply_settings()
    await sharer.apply_settings()
    if runner.browser.context:
        await runner.browser.set_window_visible(not saved.silent_browser)
    await bus.emit("settings", "设置已保存", {"dry_run": saved.dry_run})
    return settings_public(saved, bool(load_api_key()))


@router.post("/api/settings/api-key")
async def set_api_key(body: ApiKeyBody) -> dict[str, Any]:
    key = body.api_key.strip()
    if not key:
        clear_api_key()
        await bus.emit("settings", "已清除 API Key", level="warn")
        return {"ok": True, "has_api_key": False}
    if len(key) < 12:
        raise HTTPException(status_code=422, detail="API Key 看起来太短")
    save_api_key(key)
    await bus.emit("settings", "API Key 已保存到本机", {"masked": key[:4] + "…" + key[-4:]})
    return {"ok": True, "has_api_key": True}


@router.delete("/api/settings/api-key")
async def delete_api_key() -> dict[str, Any]:
    clear_api_key()
    await bus.emit("settings", "已清除 API Key", level="warn")
    return {"ok": True, "has_api_key": False}


@router.post("/api/llm/test")
async def test_llm() -> dict[str, Any]:
    try:
        decider = DeepSeekDecider(runner.settings)
    except LLMError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = await decider.ping()
    await bus.emit("llm_test", "模型连通性测试", result, level="info" if result.get("ok") else "error")
    return result


@router.post("/api/browser/open")
async def open_browser() -> dict[str, Any]:
    try:
        result = await runner.browser.open_login()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    await bus.emit("browser", "浏览器已打开，等待扫码登录", result)
    return result


@router.post("/api/browser/hide")
async def hide_browser() -> dict[str, Any]:
    if runner.browser.context:
        await runner.browser.set_window_visible(False)
    await bus.emit("browser", "浏览器已隐藏到后台", {})
    return await runner.browser.status()


@router.post("/api/browser/snapshot")
async def snapshot() -> dict[str, Any]:
    try:
        result = await runner.browser.snapshot("manual")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    await bus.emit("browser", "已保存 DOM 快照", result)
    return result


@router.post("/api/browser/close")
async def close_browser() -> dict[str, Any]:
    await runner.browser.close()
    await bus.emit("browser", "浏览器已关闭", level="warn")
    return {"ok": True}
