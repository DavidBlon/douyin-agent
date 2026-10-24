"""系统级路由：健康检查、聚合状态、启动/停止一轮、手动触发自动回复。"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException

from .. import crm, db
from ..agent import runner
from ..analytics import analytics
from ..config import load_api_key, settings_public
from ..events import bus
from ..liveview import get_liveview
from ..operator import operator
from ..planner import planner
from ..publisher import publisher
from ..services import replier, sharer

router = APIRouter()

# 持有运行任务的引用，避免任务被垃圾回收；完成后自动移出。
_RUN_TASKS: set[asyncio.Task] = set()


@router.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "service": "douyin-agent", "running": runner.running}


@router.get("/api/status")
async def status() -> dict[str, Any]:
    data = await runner.status()
    data["settings"] = settings_public(runner.settings, bool(load_api_key()))
    data["replier"] = replier.status()
    data["sharer"] = sharer.status()
    data["analytics"] = analytics.status()
    data["publisher"] = publisher.status()
    data["planner"] = planner.status()
    data["operator"] = operator.status()
    data["live"] = get_liveview().status()
    data["crm"] = crm.overview()
    return data


@router.post("/api/run", status_code=202)
async def run_agent() -> dict[str, Any]:
    if runner.running:
        raise HTTPException(status_code=409, detail="已经有一轮在跑")
    if not load_api_key():
        raise HTTPException(status_code=400, detail="还没有配置 DeepSeek API Key")
    task = asyncio.create_task(runner.run_once())
    _RUN_TASKS.add(task)
    task.add_done_callback(_RUN_TASKS.discard)
    return {"ok": True, "accepted": True}


@router.post("/api/stop")
async def stop_agent() -> dict[str, Any]:
    runner.stop()
    await bus.emit("run_state", "收到停止请求，当前动作完成后停下", level="warn")
    return {"ok": True, "running": runner.running}


@router.post("/api/replier/check")
async def replier_check() -> dict[str, Any]:
    if not db.load_settings().auto_reply:
        raise HTTPException(status_code=400, detail="先在设置里开启自动回复")
    if not replier.running:
        await replier.start()
    return await replier.check_once(manual=True)
