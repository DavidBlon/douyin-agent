from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from . import content, db, video
from .events import bus


def _parse_hhmm(value: str, fallback: str) -> tuple[int, int]:
    raw = (value or fallback).strip()
    try:
        hour, minute = raw.split(":", 1)
        return max(0, min(23, int(hour))), max(0, min(59, int(minute)))
    except (ValueError, TypeError):
        hour, minute = fallback.split(":", 1)
        return int(hour), int(minute)


def next_publish_at(settings: Any, now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    hour, minute = _parse_hhmm(settings.publish_time, "18:00")
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now + timedelta(minutes=5):
        target += timedelta(days=1)
    return target


class ContentPlanner:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()
        self._last_attempt = 0.0
        self.last_check = ""
        self.last_error = ""
        self.last_result: dict[str, Any] = {}

    @property
    def running(self) -> bool:
        return bool(self._task and not self._task.done())

    def status(self) -> dict[str, Any]:
        settings = db.load_settings()
        return {
            "enabled": bool(settings.auto_content),
            "running": self.running,
            "busy": self._lock.locked(),
            "last_check": self.last_check,
            "last_error": self.last_error,
            "last_result": self.last_result,
        }

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        await asyncio.sleep(8)
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception as exc:
                self.last_error = str(exc)[:300]
                await bus.emit("content", f"自动排期失败：{exc}", {}, level="error")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass

    async def tick(self) -> dict[str, Any]:
        settings = db.load_settings()
        if not settings.auto_content:
            return {"ok": True, "skipped": "auto_disabled"}
        if self._lock.locked():
            return {"ok": True, "skipped": "busy"}
        now = datetime.now()
        hour, minute = _parse_hhmm(settings.content_generate_time, "07:30")
        if now.time() < now.replace(hour=hour, minute=minute, second=0, microsecond=0).time():
            return {"ok": True, "skipped": "before_generate_time"}
        target = next_publish_at(settings, now)
        target_date = target.date().isoformat()
        existing = db.list_content_plans(target_date, target_date)
        if any(p.get("status") != "rejected" for p in existing):
            return {"ok": True, "skipped": "already_planned", "date": target_date}
        loop_time = asyncio.get_running_loop().time()
        if loop_time - self._last_attempt < 1800:
            return {"ok": True, "skipped": "retry_cooldown"}
        async with self._lock:
            self._last_attempt = loop_time
            self.last_check = db.now_iso()
            self.last_error = ""
            brief = (
                f"全自动策划一条适合 {target_date} 发布的内容。"
                "延续账号已有作品、角色人设和关注方向，给一个具体、可拍摄、有信息增量的选题；"
                "不要重复最近发布过的选题，也不要突然换成通用知识号。"
            )
            pillars = [
                item.strip()
                for item in (settings.content_pillars or settings.topics or "").split("、")
                if item.strip()
            ]
            pillar = pillars[target.timetuple().tm_yday % len(pillars)] if pillars else ""
            plan = await content.generate_plan(
                brief=brief,
                pillar=pillar,
                plan_date=target_date,
                slot=target.strftime("%H:%M"),
            )
            plan_id = int(plan.get("id") or 0)
            if not plan_id:
                raise RuntimeError("内容计划创建失败")
            db.update_content_plan(plan_id, publish_at=target.isoformat(timespec="minutes"))
            video_rel = ""
            video_error = ""
            if settings.auto_video:
                try:
                    video_rel = await video.make_video(db.get_content_plan(plan_id) or plan, force=True)
                    db.update_content_plan(plan_id, video_path=video_rel, error="")
                except Exception as exc:
                    video_error = str(exc)[:300]
                    await bus.emit(
                        "content",
                        f"自动成片失败：{plan.get('topic') or plan_id} · {video_error}",
                        {"plan_id": plan_id},
                        level="warn",
                    )
            approved = await content.approve_plan(plan_id)
            if not video_rel:
                db.update_content_plan(
                    plan_id,
                    status="needs_review",
                    error=video_error or "自动成片未启用，需要视频后才能发布",
                )
                approved = db.get_content_plan(plan_id) or approved
            self.last_result = {
                "plan_id": plan_id,
                "topic": plan.get("topic") or "",
                "publish_at": target.isoformat(timespec="minutes"),
                "video": video_rel,
                "status": approved.get("status") if approved else "",
            }
            await bus.emit(
                "content",
                f"全自动内容已排期：{plan.get('topic') or plan_id} · {target.strftime('%m-%d %H:%M')}",
                self.last_result,
                level="info" if approved and approved.get("status") == "approved" else "warn",
            )
            return {"ok": True, **self.last_result}


planner = ContentPlanner()
