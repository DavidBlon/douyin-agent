from __future__ import annotations

import asyncio
import hashlib
import traceback
from typing import Any

from playwright.async_api import Page

from . import db, policy
from .agent import AgentRunner
from .browser import ActionError, BrowserManager, LoginRequired, RiskControl
from .config import AgentSettings, load_api_key
from .events import bus
from .llm import DeepSeekDecider, LLMError
from .sharer import share_one_to_contact

INBOX_URL = "https://www.douyin.com/user/self"


def _base_url(value: str) -> str:
    return (value or "").split("?", 1)[0]


def _conversation_key(conv: dict[str, Any]) -> str:
    seed = _base_url(str(conv.get("avatar") or "")) or str(conv.get("title") or "")
    return "dm:" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20]


class AutoReplier:
    def __init__(self, runner: AgentRunner) -> None:
        self.runner = runner
        self.browser: BrowserManager = runner.browser
        self.task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._dry_run_seen: set[str] = set()
        self.busy = False
        self.last_check = ""
        self.last_error = ""
        self.last_result: dict[str, Any] = {}

    @property
    def running(self) -> bool:
        return bool(self.task and not self.task.done())

    def status(self) -> dict[str, Any]:
        settings = db.load_settings()
        return {
            "enabled": settings.auto_reply,
            "running": self.running,
            "busy": self.busy,
            "last_check": self.last_check,
            "last_error": self.last_error,
            "last_result": self.last_result,
            "today": {
                "dm": db.count_replies_today("reply_dm"),
                "comment": db.count_replies_today("reply_comment"),
            },
        }

    async def apply_settings(self) -> None:
        self._dry_run_seen.clear()
        settings = db.load_settings()
        if settings.auto_reply:
            await self.start()
        else:
            await self.stop()

    async def start(self) -> None:
        if self.running:
            return
        self._stop = asyncio.Event()
        self._dry_run_seen.clear()
        self.task = asyncio.create_task(self._loop())
        await bus.emit("reply_status", "自动回复已启动", {"interval": db.load_settings().reply_interval_sec})

    async def stop(self) -> None:
        self._stop.set()
        task = self.task
        self.task = None
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await bus.emit("reply_status", "自动回复已停止")

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except LoginRequired as exc:
                self.last_error = str(exc)
                await bus.emit("reply_status", "自动回复等待登录", {"error": str(exc)[:200]}, level="warn")
            except RiskControl as exc:
                self.last_error = str(exc)
                await bus.emit("reply_status", "自动回复已停止：页面出现验证", {"error": str(exc)[:200]}, level="error")
                self._stop.set()
                break
            except LLMError as exc:
                self.last_error = str(exc)
                await bus.emit("reply_status", "自动回复模型调用失败", {"error": str(exc)[:200]}, level="error")
            except Exception as exc:
                self.last_error = str(exc)[:300]
                print("[replier] " + traceback.format_exc(), flush=True)
                await bus.emit("reply_status", "自动回复检查失败", {"error": str(exc)[:200]}, level="warn")
            delay = max(20, db.load_settings().reply_interval_sec)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def _new_inbox_page(self) -> Page:
        if not self.browser.context:
            raise ActionError("浏览器上下文不可用")
        page = await self.browser.new_page()
        page.set_default_timeout(15000)
        try:
            await page.goto(INBOX_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        await page.wait_for_timeout(3000)
        try:
            await page.wait_for_selector('[data-e2e="im-entry"]', state="visible", timeout=7000)
        except Exception:
            pass
        try:
            await page.evaluate(
                "() => document.querySelectorAll('video').forEach(v => { v.muted = true; if (v.pause) v.pause(); })"
            )
        except Exception:
            pass
        return page

    async def check_once(self) -> dict[str, Any]:
        settings = db.load_settings()
        if not settings.auto_reply or self.busy:
            return {"ok": True, "skipped": "disabled_or_busy"}
        if self.runner.running or self.runner.exclusive.locked():
            return {"ok": True, "skipped": "run_in_progress"}
        if not load_api_key():
            raise LLMError("还没有配置 API Key，自动回复无法运行")

        self.busy = True
        page: Page | None = None
        try:
            async with self.runner.exclusive:
                await self.browser.ensure_started()
                if not await self.browser.is_logged_in():
                    raise LoginRequired("浏览器还没有登录抖音，自动回复暂停")
                page = await self._new_inbox_page()

                self.last_check = db.now_iso()
                self.last_error = ""
                notifications: list[dict[str, Any]] = []
                if settings.auto_reply_dms and settings.dm_reply_scope != "existing":
                    notifications = await self.browser.read_notifications(page, "全部消息")

                dm_done = 0
                comment_done = 0
                if settings.auto_reply_dms:
                    dm_done = await self._reply_dms(page, settings, notifications)
                if settings.auto_reply_comments:
                    try:
                        await page.keyboard.press("Escape")
                        await page.wait_for_timeout(300)
                    except Exception:
                        pass
                    comment_done = await self._notice_comments(page, settings)

                self.last_result = {"dm": dm_done, "comment": comment_done}
                await bus.emit(
                    "reply_cycle",
                    f"自动回复检查完成：私信 {dm_done} 条，评论 {comment_done} 条",
                    {"dm": dm_done, "comment": comment_done},
                )
                return {"ok": True, "dm": dm_done, "comment": comment_done}
        finally:
            self.busy = False
            if page is not None and not page.is_closed():
                try:
                    await page.close()
                except Exception:
                    pass

    async def _reply_dms(
        self,
        page: Page,
        settings: AgentSettings,
        notifications: list[dict[str, Any]],
    ) -> int:
        mutual = {
            str(row.get("name") or "").strip(): row
            for row in notifications
            if row.get("mutual") and str(row.get("name") or "").strip()
        }
        urls = {
            str(row.get("name") or "").strip(): str(row.get("url") or "")
            for row in notifications
            if str(row.get("name") or "").strip()
        }
        conversations = await self.browser.list_conversations(page)
        if not conversations:
            await bus.emit(
                "reply",
                "私信：没有读到会话列表（收件箱可能没打开）",
                {"scope": settings.dm_reply_scope},
                level="warn",
            )
            return 0
        system_hints = ("对方转移了账号主体", "群聊 AI", "使用须知", "已向对方隐藏")
        seen_titles: set[str] = set()
        candidates: list[dict[str, Any]] = []
        for conv in conversations:
            name = str(conv.get("title") or "").strip()
            preview = str(conv.get("preview") or "").strip()
            if not name or not preview or name in seen_titles:
                continue
            seen_titles.add(name)
            if conv.get("is_group") or any(hint in preview for hint in system_hints):
                continue
            allowlisted = policy.is_allowlisted(name, urls.get(name, ""), settings.dm_allowlist)
            relation = "mutual" if name in mutual else ("allowlisted" if allowlisted else "other")
            if settings.dm_reply_scope == "mutual" and not allowlisted and relation != "mutual":
                continue
            if settings.dm_reply_scope == "allowlist" and not allowlisted:
                continue
            candidates.append(
                {
                    **conv,
                    "name": name,
                    "preview": preview,
                    "allowlisted": allowlisted,
                    "relation": relation,
                }
            )
        if not candidates:
            await bus.emit(
                "reply",
                f"私信：{len(conversations)} 个会话里没有符合范围的 1 对 1 会话",
                {"conversations": [c.get("title") for c in conversations[:8]], "scope": settings.dm_reply_scope},
                level="info",
            )
            return 0
        candidates.sort(key=lambda c: (0 if int(c.get("unread") or 0) > 0 else 1, int(c.get("index") or 0)))
        await bus.emit(
            "reply",
            f"检查到 {len(candidates)} 个 1 对 1 会话",
            {
                "conversations": [
                    {"title": c["name"], "unread": c.get("unread", 0)} for c in candidates[:6]
                ]
            },
        )

        decider = DeepSeekDecider(settings)
        handled = 0
        for conv in candidates[:4]:
            if self._stop.is_set():
                break
            name = conv["name"]
            target_key = _conversation_key(conv)
            if not await self.browser.open_conversation(page, int(conv.get("index") or 0)):
                continue
            messages = await self.browser.read_chat_messages(page)
            if not messages:
                continue
            latest = messages[0]
            latest_text = str(latest.get("text") or "").strip()
            if not latest_text:
                continue
            contact_avatar = _base_url(str(conv.get("avatar") or ""))
            latest_avatar = _base_url(str(latest.get("avatar") or ""))
            if latest_avatar:
                incoming = bool(contact_avatar) and latest_avatar == contact_avatar
            else:
                incoming = bool(conv["preview"]) and (
                    conv["preview"] in latest_text or latest_text in conv["preview"]
                )
            if not incoming:
                continue
            dry_key = f"{target_key}|{latest_text}"
            if settings.dry_run and dry_key in self._dry_run_seen:
                continue

            transcript = list(reversed(messages[:8]))
            for item in transcript:
                avatar = _base_url(str(item.get("avatar") or ""))
                item["outgoing"] = bool(avatar and contact_avatar and avatar != contact_avatar)
            context = {
                "contact": name,
                "relation": "互相关注" if conv["relation"] == "mutual" else ("白名单" if conv["allowlisted"] else "已有会话"),
                "messages": transcript,
            }
            decision, usage = await decider.reply("dm", context, db.recent_replies(6))
            if not decision.share and db.reply_exists("reply_dm", target_key, latest_text):
                continue
            if decision.share:
                share_result = await share_one_to_contact(
                    self.browser,
                    settings,
                    name,
                    decision.share_query,
                    latest_text,
                )
                share_status = str(share_result.get("status") or "failed")
                if not settings.dry_run:
                    db.add_reply(
                        kind="reply_dm",
                        target_key=target_key,
                        incoming=latest_text,
                        reply="[视频分享]",
                        status="sent" if share_status in ("sent", "uncertain") else "skipped",
                    )
                if share_status in ("sent", "uncertain"):
                    handled += 1
                await bus.emit(
                    "reply",
                    f"点播分享 {name}：{share_status}",
                    {
                        "contact": name,
                        "incoming": latest_text[:120],
                        "share_query": decision.share_query,
                        "status": share_status,
                        "detail": share_result.get("detail") or "",
                        "aweme_id": share_result.get("aweme_id") or "",
                    },
                    level="info" if share_status in ("sent", "uncertain") else "warn",
                )
                continue
            reply = policy.clean_text(decision.reply, settings.max_dm_chars)
            if not reply:
                db.add_reply(
                    kind="reply_dm",
                    target_key=target_key,
                    incoming=latest_text,
                    reply="",
                    status="skipped",
                )
                await bus.emit(
                    "reply",
                    f"私信不回复：{name}",
                    {
                        "contact": name,
                        "incoming": latest_text[:120],
                        "reason": decision.reason,
                        "confidence": decision.confidence,
                        "usage": usage,
                    },
                )
                continue

            check = policy.check_dm_reply(
                name, conv["relation"], conv["allowlisted"], reply, settings
            )
            if not check.allowed:
                db.add_reply(
                    kind="reply_dm",
                    target_key=target_key,
                    incoming=latest_text,
                    reply="",
                    status="blocked",
                )
                await self._record(
                    settings,
                    "reply_dm",
                    target_key,
                    name,
                    urls.get(name, ""),
                    latest_text,
                    reply,
                    "blocked",
                    check.reason,
                )
                await bus.emit("reply", f"私信回复被策略拦截：{check.reason}", {"contact": name}, level="warn")
                continue

            if settings.dry_run:
                self._dry_run_seen.add(dry_key)
                await self._record(
                    settings,
                    "reply_dm",
                    target_key,
                    name,
                    urls.get(name, ""),
                    latest_text,
                    reply,
                    "dry_run",
                    decision.reason,
                    "预演，未发送",
                )
                await bus.emit(
                    "reply",
                    f"预演私信回复 → {name}：{reply}",
                    {"contact": name, "incoming": latest_text[:120], "reply": reply, "dry_run": True},
                )
                continue

            try:
                result = await self.browser.send_chat_message(page, reply)
            except Exception as exc:
                await self._record(
                    settings,
                    "reply_dm",
                    target_key,
                    name,
                    urls.get(name, ""),
                    latest_text,
                    reply,
                    "failed",
                    str(exc)[:300],
                )
                await bus.emit("reply", f"私信回复失败：{name} · {exc}", {"contact": name}, level="error")
                continue

            status = str(result.get("status") or "uncertain")
            if status in ("sent", "uncertain"):
                db.add_reply(
                    kind="reply_dm",
                    target_key=target_key,
                    incoming=latest_text,
                    reply=reply,
                    status=status,
                )
                handled += 1
            await self._record(
                settings,
                "reply_dm",
                target_key,
                name,
                urls.get(name, ""),
                latest_text,
                reply,
                status,
                decision.reason,
                str(result.get("detail") or ""),
            )
            await bus.emit(
                "reply",
                f"已回复私信 {name}：{reply}",
                {"contact": name, "incoming": latest_text[:120], "reply": reply, "status": status},
                level="info" if status == "sent" else "warn",
            )
        return handled

    async def _notice_comments(self, page: Page, settings: AgentSettings) -> int:
        rows = await self.browser.read_notifications(page, "评论")
        actionable = [
            row
            for row in rows
            if any(key in str(row.get("text") or "") for key in ("回复", "评论了你的作品", "评论了你的评论"))
        ]
        if not actionable:
            return 0
        for row in actionable[:3]:
            await bus.emit(
                "reply",
                f"检测到评论互动：{row.get('name') or '未知用户'}",
                {
                    "name": row.get("name") or "",
                    "text": str(row.get("text") or "")[:200],
                    "note": "评论回复的定位与发送路径仍需真实通知校准，暂不自动发送",
                },
                level="warn",
            )
        await bus.emit(
            "reply",
            "评论回复已检测到，但发送路径尚未校准，本次不自动发送",
            {"count": len(actionable[:3])},
            level="warn",
        )
        return 0

    async def _record(
        self,
        settings: AgentSettings,
        kind: str,
        target_key: str,
        contact: str,
        target_url: str,
        incoming: str,
        reply: str,
        status: str,
        policy_reason: str = "",
        detail: str = "",
    ) -> None:
        action_id = db.add_action(
            kind=kind,
            video_id=target_key,
            target_name=contact,
            target_url=target_url,
            content=reply,
            status=status,
            policy=policy_reason,
            dry_run=settings.dry_run,
        )
        db.update_action(
            action_id,
            result=(detail or policy_reason or "")[:400],
            executed_at=db.now_iso(),
        )
