"""客户 CRM 路由：联系人列表/详情、备注标签更新、AI 分析。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import crm, db
from ..llm import LLMError

router = APIRouter()


class CrmUpdateBody(BaseModel):
    tags: list[str] | None = None
    stage: str | None = Field(default=None, max_length=30)
    note: str | None = Field(default=None, max_length=2000)
    score: int | None = Field(default=None, ge=0, le=100)


@router.get("/api/crm/contacts")
async def crm_contacts(
    q: str = Query(default=""),
    stage: str = Query(default=""),
    tag: str = Query(default=""),
) -> dict[str, Any]:
    return {
        "ok": True,
        "items": db.list_contacts(q=q, stage=stage, tag=tag),
        "overview": crm.overview(),
    }


@router.get("/api/crm/contacts/{target_key}")
async def crm_contact(target_key: str) -> dict[str, Any]:
    contact = db.get_contact(target_key)
    if not contact:
        raise HTTPException(status_code=404, detail="联系人不存在")
    return {"ok": True, "contact": contact}


@router.put("/api/crm/contacts/{target_key}")
async def crm_contact_update(target_key: str, body: CrmUpdateBody) -> dict[str, Any]:
    if body.stage is not None and body.stage not in crm.STAGES:
        raise HTTPException(status_code=400, detail="无效的跟进阶段")
    contact = db.update_contact(
        target_key,
        tags=body.tags,
        stage=body.stage,
        note=body.note,
        score=body.score,
    )
    if not contact:
        raise HTTPException(status_code=404, detail="联系人不存在")
    return {"ok": True, "contact": contact}


@router.post("/api/crm/contacts/{target_key}/analyze")
async def crm_contact_analyze(target_key: str) -> dict[str, Any]:
    try:
        return {"ok": True, **(await crm.analyze_contact(target_key))}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
