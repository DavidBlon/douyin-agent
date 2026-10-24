"""分享路由：手动触发定时分享扫描、私信点播视频。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..agent import runner
from ..services import sharer
from ..sharer import share_one_to_contact

router = APIRouter()


class ShareOnDemandBody(BaseModel):
    target: str = Field(default="", max_length=120)
    query: str = Field(default="", max_length=120)
    request: str = Field(default="", max_length=300)


@router.post("/api/share/check")
async def share_check() -> dict[str, Any]:
    if sharer.busy:
        raise HTTPException(status_code=409, detail="定时分享正在扫描中")
    return await sharer.check_once(force=True)


@router.post("/api/share/on-demand")
async def share_on_demand(body: ShareOnDemandBody) -> dict[str, Any]:
    target = body.target.strip()
    if not target:
        raise HTTPException(status_code=400, detail="缺少接收账号")
    if sharer.busy:
        raise HTTPException(status_code=409, detail="分享任务正在运行")
    async with runner.exclusive:
        await runner.browser.ensure_started()
        if not await runner.browser.is_logged_in():
            raise HTTPException(status_code=400, detail="浏览器还没有登录抖音")
        return await share_one_to_contact(
            runner.browser,
            db.load_settings(),
            target,
            body.query.strip(),
            body.request.strip() or body.query.strip(),
        )
