"""记录类只读路由：动作记录、已看视频、事件流（SSE）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from .. import db
from ..events import bus

router = APIRouter()


@router.get("/api/actions")
async def actions(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=5, le=100),
    kind: str = Query(default=""),
    status: str = Query(default=""),
    day: str = Query(default=""),
) -> dict[str, Any]:
    return db.list_actions(page=page, size=size, kind=kind, status=status, day=day)


@router.get("/api/actions/{action_id}")
async def action_detail(action_id: int) -> dict[str, Any]:
    item = db.get_action(action_id)
    if not item:
        raise HTTPException(status_code=404, detail="动作不存在")
    return item


@router.get("/api/videos")
async def videos(limit: int = Query(default=30, ge=1, le=200)) -> dict[str, Any]:
    return {"items": db.list_videos(limit)}


@router.get("/api/events/recent")
async def events_recent(limit: int = Query(default=80, ge=1, le=300)) -> dict[str, Any]:
    return {"items": db.recent_events(limit)}


@router.get("/api/events")
async def events(request: Request):
    return EventSourceResponse(bus.stream(request), ping=15000)
