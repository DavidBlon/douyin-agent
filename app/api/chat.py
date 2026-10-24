"""浮动私信面板路由：会话摘要/列表/消息、手动发送、点播视频。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..browser import BrowserError, LoginRequired, RiskControl
from ..chat import ChatBusy, cache_summary, chat as chat_service

router = APIRouter()


class ChatSendBody(BaseModel):
    key: str = Field(default="", max_length=80)
    text: str = Field(default="", max_length=500)


class ChatVideoBody(BaseModel):
    key: str = Field(default="", max_length=80)
    cover: str = Field(default="", max_length=1200)


def _chat_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LoginRequired):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, (ChatBusy, RiskControl)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, BrowserError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=f"私信操作失败：{str(exc)[:200]}")


@router.get("/api/chat/summary")
async def chat_summary() -> dict[str, Any]:
    return cache_summary()


@router.get("/api/chat/conversations")
async def chat_conversations(refresh: bool = Query(default=False)) -> dict[str, Any]:
    try:
        return await chat_service.conversations(refresh=refresh)
    except Exception as exc:
        raise _chat_error(exc) from exc


@router.get("/api/chat/messages")
async def chat_messages(key: str = Query(min_length=1, max_length=80)) -> dict[str, Any]:
    try:
        return await chat_service.messages(key)
    except Exception as exc:
        raise _chat_error(exc) from exc


@router.post("/api/chat/send")
async def chat_send(body: ChatSendBody) -> dict[str, Any]:
    key = body.key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="缺少会话")
    try:
        return await chat_service.send(key, body.text)
    except Exception as exc:
        raise _chat_error(exc) from exc


@router.post("/api/chat/close")
async def chat_close() -> dict[str, Any]:
    await chat_service.close()
    return {"ok": True}


@router.post("/api/chat/leave")
async def chat_leave() -> dict[str, Any]:
    await chat_service.leave()
    return {"ok": True}


@router.post("/api/chat/video")
async def chat_video(body: ChatVideoBody) -> dict[str, Any]:
    key = body.key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="缺少会话")
    try:
        return await chat_service.resolve_video(key, body.cover)
    except Exception as exc:
        raise _chat_error(exc) from exc
