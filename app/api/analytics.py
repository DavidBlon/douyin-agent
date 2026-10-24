"""数据看板路由：作品总览、普通/深度刷新、单作品历史。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import db
from ..analytics import analytics
from ..browser import BrowserError, LoginRequired

router = APIRouter()


class AnalyticsRefreshBody(BaseModel):
    detail: bool = False
    limit: int = Field(default=30, ge=1, le=100)


@router.get("/api/analytics/overview")
async def analytics_overview(limit: int = Query(default=30, ge=1, le=100)) -> dict[str, Any]:
    data = db.analytics_overview(limit)
    data["ok"] = True
    data["status"] = analytics.status()
    return data


@router.post("/api/analytics/refresh")
async def analytics_refresh(body: AnalyticsRefreshBody) -> dict[str, Any]:
    try:
        return await analytics.refresh(detail=body.detail, limit=body.limit)
    except LoginRequired as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BrowserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"刷新数据失败：{str(exc)[:200]}") from exc


@router.get("/api/analytics/works/{aweme_id}/history")
async def analytics_work_history(
    aweme_id: str,
    limit: int = Query(default=30, ge=1, le=120),
) -> dict[str, Any]:
    return {"ok": True, "items": db.work_history(aweme_id, limit)}
