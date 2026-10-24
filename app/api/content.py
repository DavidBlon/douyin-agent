"""内容中心路由：内容计划的增删改查、生成、封面、审批、上传、发布和创作者中心。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from .. import content, db
from ..browser import BrowserError, LoginRequired
from ..llm import LLMError
from ..planner import planner
from ..publisher import publisher

router = APIRouter()


class ContentGenerateBody(BaseModel):
    brief: str = Field(default="", max_length=1200)
    pillar: str = Field(default="", max_length=80)
    plan_date: str = Field(default="", max_length=20)
    slot: str = Field(default="", max_length=40)


class ContentUpdateBody(BaseModel):
    plan_date: str | None = Field(default=None, max_length=20)
    slot: str | None = Field(default=None, max_length=40)
    pillar: str | None = Field(default=None, max_length=80)
    topic: str | None = Field(default=None, max_length=300)
    angle: str | None = Field(default=None, max_length=500)
    script: str | None = Field(default=None, max_length=8000)
    caption: str | None = Field(default=None, max_length=2000)
    hashtags: list[str] | None = None
    cover_text: str | None = Field(default=None, max_length=120)
    cover_bg: str | None = Field(default=None, max_length=20)
    music_note: str | None = Field(default=None, max_length=500)
    source_notes: str | None = Field(default=None, max_length=1000)
    publish_at: str | None = Field(default=None, max_length=30)
    video_path: str | None = Field(default=None, max_length=500)


class ContentRejectBody(BaseModel):
    reason: str = Field(default="", max_length=300)


class ContentPublishBody(BaseModel):
    confirm: bool = False


@router.get("/api/content/plans")
async def content_plans(
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
    plan_status: str = Query(default="", alias="status"),
) -> dict[str, Any]:
    return {"ok": True, "items": content.list_plans(date_from, date_to, plan_status)}


@router.get("/api/content/plans/{plan_id}")
async def content_plan_detail(plan_id: int) -> dict[str, Any]:
    plan = content.get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="内容计划不存在")
    return {"ok": True, "plan": plan}


@router.post("/api/content/generate")
async def content_generate(body: ContentGenerateBody) -> dict[str, Any]:
    try:
        plan = await content.generate_plan(
            brief=body.brief,
            pillar=body.pillar,
            plan_date=body.plan_date,
            slot=body.slot,
        )
    except LLMError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"生成内容失败：{str(exc)[:200]}") from exc
    return {"ok": True, "plan": plan}


@router.put("/api/content/plans/{plan_id}")
async def content_plan_update(plan_id: int, body: ContentUpdateBody) -> dict[str, Any]:
    fields = {key: value for key, value in body.model_dump().items() if value is not None}
    plan = content.update_plan(plan_id, fields)
    if plan is None:
        raise HTTPException(status_code=404, detail="内容计划不存在")
    return {"ok": True, "plan": plan}


@router.post("/api/content/plans/{plan_id}/cover")
async def content_plan_cover(plan_id: int) -> dict[str, Any]:
    plan = db.get_content_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="内容计划不存在")
    try:
        path = await content.render_cover(plan)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"生成封面失败：{str(exc)[:200]}") from exc
    updated = content.update_plan(plan_id, {"cover_path": path})
    return {"ok": True, "plan": updated, "cover_url": f"/media/{path}"}


@router.post("/api/content/plans/{plan_id}/approve")
async def content_plan_approve(plan_id: int) -> dict[str, Any]:
    try:
        plan = await content.approve_plan(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "plan": plan}


@router.post("/api/content/plans/{plan_id}/reject")
async def content_plan_reject(plan_id: int, body: ContentRejectBody) -> dict[str, Any]:
    try:
        plan = await content.reject_plan(plan_id, body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "plan": plan}


@router.post("/api/content/plans/{plan_id}/upload")
async def content_plan_upload(
    plan_id: int,
    kind: str = Query(default="video"),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    plan = db.get_content_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="内容计划不存在")
    if kind not in ("video", "cover"):
        raise HTTPException(status_code=400, detail="kind 只能是 video 或 cover")
    data = await file.read()
    if len(data) > 400 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件太大（上限 400MB）")
    try:
        path = content.save_upload(plan_id, kind, file.filename or "", data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    fields = {"video_path": path} if kind == "video" else {"cover_path": path}
    updated = content.update_plan(plan_id, fields)
    return {"ok": True, "plan": updated, "path": path, "url": f"/media/{path}"}


@router.post("/api/planner/tick")
async def planner_tick() -> dict[str, Any]:
    try:
        return await planner.tick()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"自动排期失败：{str(exc)[:240]}") from exc


@router.post("/api/content/creator/show")
async def content_creator_show() -> dict[str, Any]:
    try:
        return await publisher.open_creator(keep_open=True, show=True)
    except (LoginRequired, BrowserError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"打开创作者中心失败：{str(exc)[:240]}") from exc


@router.post("/api/content/creator/open")
async def content_creator_open() -> dict[str, Any]:
    try:
        return await publisher.open_creator()
    except (LoginRequired, BrowserError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"打开创作者中心失败：{str(exc)[:240]}") from exc


@router.post("/api/content/plans/{plan_id}/publish")
async def content_plan_publish(plan_id: int, body: ContentPublishBody) -> dict[str, Any]:
    plan = db.get_content_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="内容计划不存在")
    if not body.confirm:
        try:
            prepared = await content.prepare_publish(plan)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "ok": True,
            "status": "ready",
            "detail": "检查通过；确认后会打开创作者中心上传并发布",
            "compliance": prepared["compliance"],
        }
    try:
        return await publisher.publish(plan, dry_run=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (LoginRequired, BrowserError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"发布失败：{str(exc)[:300]}") from exc
