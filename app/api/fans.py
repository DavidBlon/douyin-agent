"""粉丝路由：读取粉丝列表、单个回关/取关（带 60 秒列表缓存和动作记录）。"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import db
from ..agent import runner
from ..browser import BrowserError, LoginRequired, RiskControl
from ..events import bus

router = APIRouter()


class FollowFanBody(BaseModel):
    name: str = Field(default="", max_length=120)
    profile_url: str = Field(default="", max_length=500)


_FANS_CACHE: dict[str, Any] = {"at": 0.0, "limit": 0, "data": None}
_FANS_LOCK = asyncio.Lock()


@router.get("/api/fans")
async def fans(limit: int = Query(default=24, ge=1, le=60), refresh: bool = False) -> dict[str, Any]:
    now = time.monotonic()
    if (
        not refresh
        and _FANS_CACHE.get("data") is not None
        and int(_FANS_CACHE.get("limit") or 0) >= limit
        and now - float(_FANS_CACHE.get("at") or 0) < 60
    ):
        return {**_FANS_CACHE["data"], "cached": True}
    async with _FANS_LOCK:
        await runner.browser.ensure_started()
        try:
            data = await runner.browser.list_fans(limit)
        except LoginRequired as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RiskControl as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BrowserError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"读取粉丝失败：{str(exc)[:200]}") from exc
        _FANS_CACHE.update(at=now, limit=limit, data=data)
        return data


@router.post("/api/fans/follow")
async def follow_fan(body: FollowFanBody) -> dict[str, Any]:
    name = body.name.strip()
    profile_url = body.profile_url.strip()
    if not name and not profile_url:
        raise HTTPException(status_code=400, detail="缺少粉丝账号")
    async with _FANS_LOCK:
        await runner.browser.ensure_started()
        try:
            result = await runner.browser.follow_fan(name, profile_url)
        except LoginRequired as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RiskControl as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BrowserError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"回关失败：{str(exc)[:200]}") from exc
    result = dict(result or {})
    status = str(result.get("status") or "failed")
    _FANS_CACHE["data"] = None
    detail = str(result.get("detail") or "")
    target = str(result.get("name") or name)
    action_id = None
    if status in ("sent", "uncertain", "dry_run"):
        action_id = db.add_action(
            kind="follow",
            video_id="",
            target_name=target,
            target_url=str(result.get("profile_url") or profile_url),
            content="回关",
            status=status,
            policy="用户手动回关",
            dry_run=status == "dry_run",
        )
        db.update_action(action_id, result=detail[:400], executed_at=db.now_iso())
    await bus.emit(
        "follow",
        f"回关 {target}：{status}",
        {
            "target": target,
            "profile_url": result.get("profile_url") or profile_url,
            "status": status,
            "detail": detail,
            "action_id": action_id,
        },
        level="info" if status in ("sent", "already") else "warn",
    )
    result["action_id"] = action_id
    return result


@router.post("/api/fans/unfollow")
async def unfollow_fan(body: FollowFanBody) -> dict[str, Any]:
    name = body.name.strip()
    profile_url = body.profile_url.strip()
    if not profile_url:
        raise HTTPException(status_code=400, detail="缺少粉丝主页链接")
    async with _FANS_LOCK:
        await runner.browser.ensure_started()
        try:
            result = await runner.browser.unfollow_fan(name, profile_url)
        except LoginRequired as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RiskControl as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BrowserError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"取关失败：{str(exc)[:200]}") from exc
    result = dict(result or {})
    status = str(result.get("status") or "failed")
    _FANS_CACHE["data"] = None
    detail = str(result.get("detail") or "")
    target = str(result.get("name") or name)
    action_id = None
    if status in ("sent", "uncertain", "dry_run"):
        action_id = db.add_action(
            kind="unfollow",
            video_id="",
            target_name=target,
            target_url=str(result.get("profile_url") or profile_url),
            content="取关",
            status=status,
            policy="用户手动取关",
            dry_run=status == "dry_run",
        )
        db.update_action(action_id, result=detail[:400], executed_at=db.now_iso())
    await bus.emit(
        "unfollow",
        f"取关 {target}：{status}",
        {
            "target": target,
            "profile_url": result.get("profile_url") or profile_url,
            "status": status,
            "detail": detail,
            "action_id": action_id,
        },
        level="info" if status == "sent" else "warn",
    )
    result["action_id"] = action_id
    return result
