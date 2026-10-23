from __future__ import annotations

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from . import content, crm, db
from .agent import runner
from .analytics import analytics
from .browser import NAV_BADGES_JS, BrowserError, LoginRequired, RiskControl
from .chat import ChatBusy, cache_summary, chat as chat_service
from .config import (
    DEBUG_DIR,
    MEDIA_DIR,
    WEB_DIST,
    AgentSettings,
    clear_api_key,
    ensure_dirs,
    load_api_key,
    save_api_key,
    settings_public,
)
from .events import bus
from .liveview import get_liveview, init_liveview
from .llm import DeepSeekDecider, LLMError
from .operator import operator
from .planner import planner
from .publisher import publisher
from .replier import AutoReplier
from .sharer import AutoSharer, share_one_to_contact


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    db.init_db()
    init_liveview(runner.browser, runner)
    await replier.apply_settings()
    await sharer.apply_settings()
    await chat_service.start()
    await publisher.start()
    await planner.start()
    await operator.start()
    yield
    try:
        await operator.stop()
    except Exception:
        pass
    try:
        await planner.stop()
    except Exception:
        pass
    try:
        await publisher.stop()
    except Exception:
        pass
    try:
        await chat_service.stop()
    except Exception:
        pass
    try:
        await replier.stop()
    except Exception:
        pass
    try:
        await sharer.stop()
    except Exception:
        pass
    try:
        await runner.browser.close()
    except Exception:
        pass


app = FastAPI(title="Douyin Agent", version="0.1.0", lifespan=lifespan)
replier = AutoReplier(runner)
sharer = AutoSharer(runner)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8787",
        "http://127.0.0.1:8787",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ApiKeyBody(BaseModel):
    api_key: str = Field(default="", max_length=300)


class RunBody(BaseModel):
    pass


class ShareOnDemandBody(BaseModel):
    target: str = Field(default="", max_length=120)
    query: str = Field(default="", max_length=120)
    request: str = Field(default="", max_length=300)


class FollowFanBody(BaseModel):
    name: str = Field(default="", max_length=120)
    profile_url: str = Field(default="", max_length=500)


class ChatSendBody(BaseModel):
    key: str = Field(default="", max_length=80)
    text: str = Field(default="", max_length=500)


class ChatVideoBody(BaseModel):
    key: str = Field(default="", max_length=80)
    cover: str = Field(default="", max_length=1200)


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


class AnalyticsRefreshBody(BaseModel):
    detail: bool = False
    limit: int = Field(default=30, ge=1, le=100)


class CrmUpdateBody(BaseModel):
    tags: list[str] | None = None
    stage: str | None = Field(default=None, max_length=30)
    note: str | None = Field(default=None, max_length=2000)
    score: int | None = Field(default=None, ge=0, le=100)


def _chat_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LoginRequired):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, (ChatBusy, RiskControl)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, BrowserError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=f"私信操作失败：{str(exc)[:200]}")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "service": "douyin-agent", "running": runner.running}


@app.get("/api/status")
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


@app.websocket("/api/live/ws")
async def live_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    view = get_liveview()
    queue = await view.subscribe()
    try:
        await websocket.send_text(json.dumps({"type": "hello", **view.status()}, ensure_ascii=False))
        while True:
            frame = await queue.get()
            await websocket.send_bytes(base64.b64decode(frame))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await view.unsubscribe(queue)


@app.get("/api/debug/nav")
async def debug_nav() -> dict[str, Any]:
    page = runner.browser.page
    if page is None or page.is_closed():
        return {"ok": False, "detail": "浏览器页面未就绪"}
    try:
        data = await page.evaluate(NAV_BADGES_JS)
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:200]}
    return {"ok": True, "url": page.url, "data": data}


@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    return settings_public(runner.settings, bool(load_api_key()))


@app.put("/api/settings")
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


@app.post("/api/settings/api-key")
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


@app.delete("/api/settings/api-key")
async def delete_api_key() -> dict[str, Any]:
    clear_api_key()
    await bus.emit("settings", "已清除 API Key", level="warn")
    return {"ok": True, "has_api_key": False}


@app.post("/api/llm/test")
async def test_llm() -> dict[str, Any]:
    try:
        decider = DeepSeekDecider(runner.settings)
    except LLMError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = await decider.ping()
    await bus.emit("llm_test", "模型连通性测试", result, level="info" if result.get("ok") else "error")
    return result


@app.post("/api/browser/open")
async def open_browser() -> dict[str, Any]:
    try:
        result = await runner.browser.open_login()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    await bus.emit("browser", "浏览器已打开，等待扫码登录", result)
    return result


@app.post("/api/browser/hide")
async def hide_browser() -> dict[str, Any]:
    if runner.browser.context:
        await runner.browser.set_window_visible(False)
    await bus.emit("browser", "浏览器已隐藏到后台", {})
    return await runner.browser.status()


@app.post("/api/browser/snapshot")
async def snapshot() -> dict[str, Any]:
    try:
        result = await runner.browser.snapshot("manual")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    await bus.emit("browser", "已保存 DOM 快照", result)
    return result


@app.post("/api/browser/close")
async def close_browser() -> dict[str, Any]:
    await runner.browser.close()
    await bus.emit("browser", "浏览器已关闭", level="warn")
    return {"ok": True}


@app.post("/api/run", status_code=202)
async def run_agent() -> dict[str, Any]:
    if runner.running:
        raise HTTPException(status_code=409, detail="已经有一轮在跑")
    if not load_api_key():
        raise HTTPException(status_code=400, detail="还没有配置 DeepSeek API Key")
    task = asyncio.create_task(runner.run_once())
    app.state.run_task = task
    return {"ok": True, "accepted": True}


@app.post("/api/stop")
async def stop_agent() -> dict[str, Any]:
    runner.stop()
    await bus.emit("run_state", "收到停止请求，当前动作完成后停下", level="warn")
    return {"ok": True, "running": runner.running}


@app.post("/api/replier/check")
async def replier_check() -> dict[str, Any]:
    if not db.load_settings().auto_reply:
        raise HTTPException(status_code=400, detail="先在设置里开启自动回复")
    if not replier.running:
        await replier.start()
    return await replier.check_once(manual=True)


@app.get("/api/chat/summary")
async def chat_summary() -> dict[str, Any]:
    return cache_summary()


@app.get("/api/chat/conversations")
async def chat_conversations(refresh: bool = Query(default=False)) -> dict[str, Any]:
    try:
        return await chat_service.conversations(refresh=refresh)
    except Exception as exc:
        raise _chat_error(exc) from exc


@app.get("/api/chat/messages")
async def chat_messages(key: str = Query(min_length=1, max_length=80)) -> dict[str, Any]:
    try:
        return await chat_service.messages(key)
    except Exception as exc:
        raise _chat_error(exc) from exc


@app.post("/api/chat/send")
async def chat_send(body: ChatSendBody) -> dict[str, Any]:
    key = body.key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="缺少会话")
    try:
        return await chat_service.send(key, body.text)
    except Exception as exc:
        raise _chat_error(exc) from exc


@app.post("/api/chat/close")
async def chat_close() -> dict[str, Any]:
    await chat_service.close()
    return {"ok": True}


@app.post("/api/chat/leave")
async def chat_leave() -> dict[str, Any]:
    await chat_service.leave()
    return {"ok": True}


@app.post("/api/chat/video")
async def chat_video(body: ChatVideoBody) -> dict[str, Any]:
    key = body.key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="缺少会话")
    try:
        return await chat_service.resolve_video(key, body.cover)
    except Exception as exc:
        raise _chat_error(exc) from exc


@app.post("/api/share/check")
async def share_check() -> dict[str, Any]:
    if sharer.busy:
        raise HTTPException(status_code=409, detail="定时分享正在扫描中")
    return await sharer.check_once(force=True)


@app.post("/api/share/on-demand")
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


@app.get("/api/fans")
async def fans(limit: int = Query(default=24, ge=1, le=60)) -> dict[str, Any]:
    if runner.running:
        raise HTTPException(status_code=409, detail="正在执行一轮，稍后再刷新粉丝")
    async with runner.exclusive:
        await runner.browser.ensure_started()
        try:
            return await runner.browser.list_fans(limit)
        except LoginRequired as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RiskControl as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BrowserError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"读取粉丝失败：{str(exc)[:200]}") from exc


@app.post("/api/fans/follow")
async def follow_fan(body: FollowFanBody) -> dict[str, Any]:
    name = body.name.strip()
    profile_url = body.profile_url.strip()
    if not name and not profile_url:
        raise HTTPException(status_code=400, detail="缺少粉丝账号")
    if runner.running:
        raise HTTPException(status_code=409, detail="正在执行一轮，稍后再回关")
    async with runner.exclusive:
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


@app.post("/api/fans/unfollow")
async def unfollow_fan(body: FollowFanBody) -> dict[str, Any]:
    name = body.name.strip()
    profile_url = body.profile_url.strip()
    if not profile_url:
        raise HTTPException(status_code=400, detail="缺少粉丝主页链接")
    if runner.running:
        raise HTTPException(status_code=409, detail="正在执行一轮，稍后再取关")
    async with runner.exclusive:
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


@app.get("/api/analytics/overview")
async def analytics_overview(limit: int = Query(default=30, ge=1, le=100)) -> dict[str, Any]:
    data = db.analytics_overview(limit)
    data["ok"] = True
    data["status"] = analytics.status()
    return data


@app.post("/api/analytics/refresh")
async def analytics_refresh(body: AnalyticsRefreshBody) -> dict[str, Any]:
    try:
        return await analytics.refresh(detail=body.detail, limit=body.limit)
    except LoginRequired as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BrowserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"刷新数据失败：{str(exc)[:200]}") from exc


@app.get("/api/analytics/works/{aweme_id}/history")
async def analytics_work_history(
    aweme_id: str,
    limit: int = Query(default=30, ge=1, le=120),
) -> dict[str, Any]:
    return {"ok": True, "items": db.work_history(aweme_id, limit)}


@app.get("/api/content/plans")
async def content_plans(
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
    plan_status: str = Query(default="", alias="status"),
) -> dict[str, Any]:
    return {"ok": True, "items": content.list_plans(date_from, date_to, plan_status)}


@app.get("/api/content/plans/{plan_id}")
async def content_plan_detail(plan_id: int) -> dict[str, Any]:
    plan = content.get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="内容计划不存在")
    return {"ok": True, "plan": plan}


@app.post("/api/content/generate")
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


@app.put("/api/content/plans/{plan_id}")
async def content_plan_update(plan_id: int, body: ContentUpdateBody) -> dict[str, Any]:
    fields = {key: value for key, value in body.model_dump().items() if value is not None}
    plan = content.update_plan(plan_id, fields)
    if plan is None:
        raise HTTPException(status_code=404, detail="内容计划不存在")
    return {"ok": True, "plan": plan}


@app.post("/api/content/plans/{plan_id}/cover")
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


@app.post("/api/content/plans/{plan_id}/approve")
async def content_plan_approve(plan_id: int) -> dict[str, Any]:
    try:
        plan = await content.approve_plan(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "plan": plan}


@app.post("/api/content/plans/{plan_id}/reject")
async def content_plan_reject(plan_id: int, body: ContentRejectBody) -> dict[str, Any]:
    try:
        plan = await content.reject_plan(plan_id, body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "plan": plan}


@app.post("/api/content/plans/{plan_id}/upload")
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


@app.post("/api/planner/tick")
async def planner_tick() -> dict[str, Any]:
    try:
        return await planner.tick()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"自动排期失败：{str(exc)[:240]}") from exc


@app.post("/api/content/creator/show")
async def content_creator_show() -> dict[str, Any]:
    try:
        return await publisher.open_creator(keep_open=True, show=True)
    except (LoginRequired, BrowserError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"打开创作者中心失败：{str(exc)[:240]}") from exc


@app.post("/api/content/creator/open")
async def content_creator_open() -> dict[str, Any]:
    try:
        return await publisher.open_creator()
    except (LoginRequired, BrowserError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"打开创作者中心失败：{str(exc)[:240]}") from exc


@app.post("/api/content/plans/{plan_id}/publish")
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


@app.get("/api/crm/contacts")
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


@app.get("/api/crm/contacts/{target_key}")
async def crm_contact(target_key: str) -> dict[str, Any]:
    contact = db.get_contact(target_key)
    if not contact:
        raise HTTPException(status_code=404, detail="联系人不存在")
    return {"ok": True, "contact": contact}


@app.put("/api/crm/contacts/{target_key}")
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


@app.post("/api/crm/contacts/{target_key}/analyze")
async def crm_contact_analyze(target_key: str) -> dict[str, Any]:
    try:
        return {"ok": True, **(await crm.analyze_contact(target_key))}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/actions")
async def actions(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=5, le=100),
    kind: str = Query(default=""),
    status: str = Query(default=""),
    day: str = Query(default=""),
) -> dict[str, Any]:
    return db.list_actions(page=page, size=size, kind=kind, status=status, day=day)


@app.get("/api/actions/{action_id}")
async def action_detail(action_id: int) -> dict[str, Any]:
    item = db.get_action(action_id)
    if not item:
        raise HTTPException(status_code=404, detail="动作不存在")
    return item


@app.get("/api/videos")
async def videos(limit: int = Query(default=30, ge=1, le=200)) -> dict[str, Any]:
    return {"items": db.list_videos(limit)}


@app.get("/api/events/recent")
async def events_recent(limit: int = Query(default=80, ge=1, le=300)) -> dict[str, Any]:
    return {"items": db.recent_events(limit)}


@app.get("/api/events")
async def events(request: Request):
    return EventSourceResponse(bus.stream(request), ping=15000)


ensure_dirs()
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")
app.mount("/debug", StaticFiles(directory=str(DEBUG_DIR)), name="debug")

if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
else:
    @app.get("/")
    async def root() -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "message": "前端还没有构建。开发时在 web/ 运行 npm run dev；生产时 npm run build 后重启。",
                "api": "/docs",
            }
        )
