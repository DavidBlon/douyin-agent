from __future__ import annotations

import asyncio
import random
from typing import Any

from . import db, policy
from .browser import (
    ActionError,
    BrowserManager,
    LoginRequired,
    RiskControl,
)
from .config import AgentSettings, load_api_key
from .events import bus
from .llm import Decision, DeepSeekDecider, LLMError


class AgentRunner:
    def __init__(self) -> None:
        db.init_db()
        self.settings: AgentSettings = db.load_settings()
        self.browser = BrowserManager(self.settings)
        self.running = False
        self.stop_requested = False
        self._stop_event = asyncio.Event()
        self.current_video: dict[str, Any] | None = None
        self.last_decision: dict[str, Any] | None = None
        self._lock = asyncio.Lock()
        self.exclusive = asyncio.Lock()
        self._comments_warned = False

    def refresh_settings(self) -> AgentSettings:
        self.settings = db.load_settings()
        self.browser.settings = self.settings
        return self.settings

    def stop(self) -> None:
        self.stop_requested = True
        self._stop_event.set()

    async def _sleep_gap(self) -> bool:
        delay = max(3, self.settings.min_gap_sec) + random.uniform(0, 4)
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
            return False
        except asyncio.TimeoutError:
            return True

    def _done_kinds(self, video_id: str) -> list[str]:
        return [
            kind
            for kind in ("like", "comment", "dm")
            if db.has_action(video_id, kind)
        ]

    async def _blocked(self, kind: str, video: dict[str, Any], content: str, reason: str) -> None:
        action_id = db.add_action(
            kind=kind,
            video_id=str(video.get("aweme_id") or ""),
            target_name=str(video.get("author") or ""),
            target_url=str(video.get("author_url") or ""),
            content=content,
            status="blocked",
            policy=reason,
            dry_run=self.settings.dry_run,
        )
        await bus.emit(
            "action",
            f"{kind} 被策略拦截：{reason}",
            {
                "id": action_id,
                "kind": kind,
                "status": "blocked",
                "policy": reason,
                "content": content,
                "target": video.get("author") or video.get("aweme_id") or "",
                "video_id": video.get("aweme_id") or "",
                "dry_run": self.settings.dry_run,
            },
            level="warn",
        )

    async def _act(self, kind: str, video: dict[str, Any], content: str = "", mention: str = "") -> str:
        vid = str(video.get("aweme_id") or "")
        author = str(video.get("author") or "")
        author_url = str(video.get("author_url") or "")
        action_id = db.add_action(
            kind=kind,
            video_id=vid,
            target_name=author,
            target_url=author_url,
            content=content,
            status="dry_run" if self.settings.dry_run else "running",
            dry_run=self.settings.dry_run,
        )
        await bus.emit(
            "action",
            f"准备执行 {kind}" + ("（预演）" if self.settings.dry_run else ""),
            {
                "id": action_id,
                "kind": kind,
                "status": "dry_run" if self.settings.dry_run else "running",
                "content": content,
                "target": author or vid,
                "video_id": vid,
                "target_url": author_url,
                "dry_run": self.settings.dry_run,
            },
        )
        if self.settings.dry_run:
            return "dry_run"
        try:
            if kind == "like":
                result = await self.browser.like()
            elif kind == "comment":
                result = await self.browser.comment(content, mention=mention)
            elif kind == "dm":
                result = await self.browser.dm(author_url, author, content)
            else:
                raise ActionError(f"未知动作 {kind}")
            status = str(result.get("status") or "uncertain")
            db.update_action(
                action_id,
                status=status,
                result=str(result.get("detail") or ""),
                screenshot=str(result.get("screenshot") or ""),
            )
            await bus.emit(
                "action_result",
                f"{kind} → {status}",
                {
                    "id": action_id,
                    "kind": kind,
                    "status": status,
                    "detail": result.get("detail") or "",
                    "screenshot": result.get("screenshot") or "",
                    "target": author or vid,
                    "content": content,
                },
                level="info" if status in ("sent", "skipped") else "warn",
            )
            return status
        except (LoginRequired, RiskControl):
            db.update_action(action_id, status="failed", result="登录失效或触发风控，已停止")
            raise
        except Exception as exc:
            db.update_action(action_id, status="failed", result=str(exc)[:400])
            await bus.emit(
                "action_result",
                f"{kind} 执行失败：{exc}",
                {"id": action_id, "kind": kind, "status": "failed", "detail": str(exc)[:400]},
                level="error",
            )
            return "failed"

    async def run_once(self) -> dict[str, Any]:
        if self._lock.locked():
            return {"ok": False, "error": "已经有一轮在跑"}
        async with self._lock, self.exclusive:
            self.running = True
            self.stop_requested = False
            self._stop_event = asyncio.Event()
            self.refresh_settings()
            started = db.now_iso()
            picked = 0
            acted = 0
            reason = "finished"
            try:
                if not load_api_key():
                    raise LLMError("还没有配置 DeepSeek API Key")
                decider = DeepSeekDecider(self.settings)
                await bus.emit(
                    "run_started",
                    "开始刷一轮",
                    {
                        "max_videos": self.settings.max_videos_per_run,
                        "dry_run": self.settings.dry_run,
                        "model": self.settings.model,
                    },
                )
                await self.browser.ensure_started()
                if not await self.browser.is_logged_in():
                    raise LoginRequired("浏览器还没有登录抖音")
                await self.browser.ensure_feed()
                try:
                    await self.browser.snapshot("run_start")
                except Exception:
                    pass
                await bus.emit("browser", "浏览器已连接，登录态正常")

                seen: set[str] = set()
                relation_checked: set[str] = set()
                for index in range(self.settings.max_videos_per_run):
                    if self.stop_requested:
                        reason = "stopped"
                        break
                    await self.browser.assert_safe()
                    try:
                        video = await self.browser.read_current(
                            include_comments=self.settings.auto_comment or self.settings.auto_dm
                        )
                    except ActionError as exc:
                        if not self._comments_warned and "评论" in str(exc):
                            self._comments_warned = True
                        raise
                    vid = str(video.get("aweme_id") or "")
                    author = str(video.get("author") or "")
                    author_url = str(video.get("author_url") or "")
                    if video.get("is_live"):
                        await bus.emit(
                            "info",
                            "跳过直播或非普通视频",
                            {"url": video.get("url") or ""},
                            level="warn",
                        )
                        if index < self.settings.max_videos_per_run - 1:
                            moved = await self.browser.next_video()
                            if not moved:
                                await bus.emit(
                                    "info",
                                    "直播页没有直接滑动，已刷新推荐流继续",
                                    level="warn",
                                )
                            continue
                        break
                    if vid in seen:
                        await bus.emit("info", "推荐流返回了已看过的视频，尝试滑到下一条", {"aweme_id": vid}, level="warn")
                        await self.browser.next_video()
                        continue
                    seen.add(vid)
                    db.upsert_video(video)
                    self.current_video = video
                    await bus.emit(
                        "video",
                        f"正在看：{video.get('author') or '未知作者'}",
                        {"video": video, "index": index + 1, "total": self.settings.max_videos_per_run},
                    )

                    done = self._done_kinds(vid)
                    counts = db.today_counts()
                    try:
                        decision, usage = await decider.decide(
                            video, db.recent_actions(10), counts, done
                        )
                    except LLMError:
                        raise
                    self.last_decision = {
                        **decision.model_dump(),
                        "usage": usage,
                        "video_id": vid,
                        "ts": db.now_iso(),
                    }
                    await bus.emit(
                        "decision",
                        "模型判断完成",
                        {**self.last_decision, "author": video.get("author") or ""},
                    )

                    comment_status = ""
                    if decision.like and "like" not in done:
                        check = policy.check_like(video, self.settings)
                        if check.allowed:
                            status = await self._act("like", video)
                            if status in ("sent", "uncertain", "dry_run"):
                                acted += 1
                                picked += 1
                        else:
                            await self._blocked("like", video, "", check.reason)

                    if decision.comment.strip() and "comment" not in done:
                        mention = self.settings.share_target.strip() if self.settings.comment_mention else ""
                        body, full_comment, mention = policy.build_comment_payload(
                            decision.comment, mention, self.settings.max_comment_chars
                        )
                        check = policy.check_comment(video, body, self.settings)
                        if check.allowed:
                            comment_status = await self._act(
                                "comment", video, full_comment, mention=mention
                            )
                            if comment_status in ("sent", "uncertain", "dry_run"):
                                acted += 1
                                picked += 1
                        else:
                            await self._blocked("comment", video, full_comment, check.reason)

                    if decision.dm.strip() and "dm" not in done:
                        body = policy.clean_text(decision.dm, self.settings.max_dm_chars)
                        if comment_status in ("sent", "uncertain", "dry_run"):
                            await self._blocked("dm", video, body, "同一视频已经公开评论，避免重复打扰")
                        else:
                            if (
                                self.settings.auto_dm
                                and self.settings.dm_allow_mutual
                                and author_url
                                and author_url not in relation_checked
                                and db.count_actions_today("dm") < self.settings.dms_per_day
                                and not policy.is_allowlisted(author, author_url, self.settings.dm_allowlist)
                            ):
                                relation_checked.add(author_url)
                                try:
                                    relation = await self.browser.author_relation(author_url, author)
                                    video["author_mutual"] = bool(relation.get("mutual"))
                                    await bus.emit(
                                        "info",
                                        f"互关核对：{'是互关' if video['author_mutual'] else '不是互关'}",
                                        {"author": author, "relation": relation},
                                    )
                                except Exception as exc:
                                    video["author_mutual"] = False
                                    await bus.emit(
                                        "info",
                                        "互关核对失败，按非互关处理",
                                        {"author": author, "error": str(exc)[:200]},
                                        level="warn",
                                    )
                            check = policy.check_dm(video, body, self.settings)
                            if check.allowed:
                                status = await self._act("dm", video, body)
                                if status in ("sent", "uncertain", "dry_run"):
                                    acted += 1
                                    picked += 1
                            else:
                                await self._blocked("dm", video, body, check.reason)

                    if index < self.settings.max_videos_per_run - 1:
                        if not await self._sleep_gap():
                            reason = "stopped"
                            break
                        moved = await self.browser.next_video()
                        if not moved:
                            await bus.emit(
                                "info",
                                "推荐流没有滑动，已刷新推荐流继续",
                                level="warn",
                            )

                await bus.emit(
                    "run_done",
                    "这一轮结束",
                    {
                        "reason": reason,
                        "picked": picked,
                        "acted": acted,
                        "counts": db.today_counts(),
                        "started": started,
                        "dry_run": self.settings.dry_run,
                    },
                )
                return {"ok": True, "picked": picked, "acted": acted, "reason": reason}
            except LoginRequired as exc:
                reason = "login"
                await bus.emit("error", str(exc), {"hint": "点“打开登录浏览器”扫码登录后再开始"}, level="error")
                return {"ok": False, "error": str(exc)}
            except RiskControl as exc:
                reason = "risk"
                await bus.emit("error", str(exc), {"hint": "已停止，不尝试绕过验证。请人工处理后再运行。"}, level="error")
                return {"ok": False, "error": str(exc)}
            except LLMError as exc:
                reason = "llm"
                await bus.emit("error", str(exc), {"hint": "检查 API Key、模型名和网络。"}, level="error")
                return {"ok": False, "error": str(exc)}
            except Exception as exc:
                reason = "error"
                await bus.emit("error", f"运行中断：{exc}", {"type": type(exc).__name__}, level="error")
                return {"ok": False, "error": str(exc)}
            finally:
                self.running = False
                try:
                    await bus.emit(
                        "run_state",
                        "运行状态已结束",
                        {"reason": reason, "counts": db.today_counts()},
                    )
                except Exception:
                    pass

    async def status(self) -> dict[str, Any]:
        browser = await self.browser.status()
        return {
            "running": self.running,
            "stop_requested": self.stop_requested,
            "browser": browser,
            "settings": self.settings.model_dump(),
            "has_api_key": bool(load_api_key()),
            "today": db.today_counts(),
            "current_video": self.current_video,
            "last_decision": self.last_decision,
        }


runner = AgentRunner()
