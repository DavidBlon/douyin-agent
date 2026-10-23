from __future__ import annotations

import asyncio
import hashlib
import time
import traceback
from typing import Any

from playwright.async_api import Page

from . import chat, crm, db, policy
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


def _ordered_messages(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        [r for r in rows if isinstance(r, dict) and not r.get("is_system")],
        key=lambda r: (
            r.get("top") if isinstance(r.get("top"), (int, float)) else 10**9,
            r.get("index") if isinstance(r.get("index"), int) else 10**9,
        ),
    )
    return [r for r in ordered if str(r.get("text") or "").strip() or r.get("images") or r.get("videos")]


def _preview_matches(text: str, preview: str) -> bool:
    a = (text or "").strip()
    b = (preview or "").strip()
    if not a or not b:
        return False
    return a == b or a in b or b in a


async def _read_conversation(
    browser: BrowserManager,
    page: Page,
    conv: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    """Read messages and make sure they belong to this conversation, not the previous one."""
    contact_avatar = _base_url(str(conv.get("avatar") or ""))
    preview = str(conv.get("preview") or "")
    messages: list[dict[str, Any]] = []
    await page.wait_for_timeout(500)
    for attempt in range(6):
        messages = _ordered_messages(await browser.read_chat_transcript(page))
        if messages:
            latest = messages[-1]
            text = str(latest.get("text") or "").strip()
            avatar = _base_url(str(latest.get("avatar") or ""))
            incoming = bool(avatar and contact_avatar) and avatar == contact_avatar
            if incoming or bool(latest.get("mine")) or _preview_matches(text, preview):
                return messages, True
        if attempt < 5:
            await page.wait_for_timeout(700)
    if messages:
        latest = messages[-1]
        print(
            f"[dm] content check failed contact={conv.get('title')!r} preview={preview!r} "
            f"newest={str(latest.get('text') or '')[:60]!r} avatar={_base_url(str(latest.get('avatar') or ''))[:60]!r} "
            f"mine={latest.get('mine')} total={len(messages)}",
            flush=True,
        )
    return messages, False


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
        self._last_skip_signature: tuple[tuple[str, str], ...] = ()
        self._worker_pages: list[Page] = []
        self._wake = asyncio.Event()
        self._manual_until = 0.0
        self._quiet_until = 0.0

    @property
    def running(self) -> bool:
        return bool(self.task and not self.task.done())

    def status(self) -> dict[str, Any]:
        settings = db.load_settings()
        return {
            "enabled": bool(settings.auto_reply),
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
            delay = max(8, db.load_settings().reply_interval_sec)
            if time.monotonic() < self._manual_until:
                delay = 3
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

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

    async def _new_work_page(self) -> Page:
        page = await self._new_inbox_page()
        self._worker_pages.append(page)
        return page

    def queue_manual(self, detail: str = "") -> dict[str, Any]:
        self._manual_until = time.monotonic() + 300
        self._wake.set()
        return {
            "ok": True,
            "queued": True,
            "detail": detail or "浏览器正被其他任务占用，已排队，空闲后立即检查",
        }

    async def check_once(self, manual: bool = False) -> dict[str, Any]:
        settings = db.load_settings()
        if not settings.auto_reply:
            self.runner.reply_pending = False
            return {"ok": True, "skipped": "disabled"}
        if manual:
            dm_needed = settings.auto_reply_dms
            comment_needed = settings.auto_reply_comments
            self.runner.reply_pending = True
        else:
            badges = {"im": -1, "notice": -1, "avatar": -1}
            reader = getattr(self.browser, "nav_badges", None)
            if reader is not None:
                badges = await reader(self.browser.page)
            dm_needed = settings.auto_reply_dms and (badges.get("im") != 0 or badges.get("notice") == 1)
            comment_needed = settings.auto_reply_comments and badges.get("notice") != 0
            self.runner.reply_pending = bool(dm_needed or comment_needed)
            if not dm_needed and not comment_needed:
                self._quiet_until = 0.0
                return {"ok": True, "skipped": "no_badge"}
            if time.monotonic() < self._quiet_until:
                self.runner.reply_pending = False
                return {"ok": True, "skipped": "quiet"}
        if self.busy:
            if manual:
                return self.queue_manual("自动回复正在检查中，本轮结束后立即再查一次")
            return {"ok": True, "skipped": "busy"}
        if self.runner.running or self.runner.exclusive.locked():
            if manual:
                return self.queue_manual("浏览器正在执行推荐流或分享任务，已排队，任务结束后立即检查")
            return {"ok": True, "skipped": "run_in_progress"}
        if self.runner.run_pending:
            # 有一轮在排队等浏览器，自动回复让出，避免互相饿死。
            return {"ok": True, "skipped": "run_pending"}
        if getattr(self.runner, "share_pending", False):
            # 定时分享已到点正在排队，自动回复让出浏览器。
            return {"ok": True, "skipped": "share_pending"}
        if not load_api_key():
            raise LLMError("还没有配置 API Key，自动回复无法运行")

        self._manual_until = 0.0
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
                if dm_needed and settings.dm_reply_scope != "existing":
                    notifications = await self.browser.read_notifications(page, "全部消息")

                dm_done = 0
                comment_done = 0
                if dm_needed:
                    dm_done = await self._reply_dms(page, settings, notifications)
                if comment_needed:
                    try:
                        await page.keyboard.press("Escape")
                        await page.wait_for_timeout(300)
                    except Exception:
                        pass
                    comment_done = await self._notice_comments(page, settings)

                self.last_result = {"dm": dm_done, "comment": comment_done}
                if not manual and dm_done == 0 and comment_done == 0:
                    self._quiet_until = time.monotonic() + 45
                await bus.emit(
                    "reply_cycle",
                    f"自动回复检查完成：私信 {dm_done} 条，评论 {comment_done} 条",
                    {"dm": dm_done, "comment": comment_done},
                )
                return {"ok": True, "dm": dm_done, "comment": comment_done}
        finally:
            self.busy = False
            self.runner.reply_pending = False
            for extra in list(self._worker_pages):
                if extra is not page and not extra.is_closed():
                    try:
                        await extra.close()
                    except Exception:
                        pass
            self._worker_pages.clear()
            if page is not None and not page.is_closed():
                try:
                    await page.close()
                except Exception:
                    pass

    def _has_new_message(self, conv: dict[str, Any]) -> bool:
        # 只认消息行上的红点/未读数，避免把没有红点的会话也点开。
        return int(conv.get("unread") or 0) > 0

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
        for _ in range(2):
            if conversations:
                break
            await page.wait_for_timeout(1200)
            conversations = await self.browser.list_conversations(page)
        if not conversations:
            try:
                await page.goto(INBOX_URL, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(2500)
                conversations = await self.browser.list_conversations(page)
            except Exception:
                conversations = []
        if conversations:
            chat.remember_conversations(conversations)
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
            if name.isdigit() and len(name) >= 8:
                # 陌生人/系统会话常用一串数字当标题，打开通常失败，跳过避免空转。
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
        candidates = [c for c in candidates if self._has_new_message(c)]
        if not candidates:
            return 0
        for c in candidates:
            print(f"[reply] unread {c.get('name')!r} debug={c.get('unread_debug')!r}", flush=True)
        candidates.sort(key=lambda c: (0 if int(c.get("unread") or 0) > 0 else 1, int(c.get("index") or 0)))
        await bus.emit(
            "reply",
            f"发现 {len(candidates)} 个有新消息的会话",
            {
                "conversations": [
                    {"title": c["name"], "unread": c.get("unread", 0)} for c in candidates[:6]
                ]
            },
        )

        decider = DeepSeekDecider(settings)
        handled = 0
        skips: list[tuple[str, str]] = []
        active_page = page
        for idx, conv in enumerate(candidates[:4]):
            if self._stop.is_set() or self.runner.run_pending or getattr(self.runner, "share_pending", False):
                break
            name = conv["name"]
            target_key = _conversation_key(conv)
            if idx > 0:
                # 回复完上一个人后页面状态会变化，换一张干净收件箱页再开下一个人。
                if active_page is not page and not active_page.is_closed():
                    try:
                        await active_page.close()
                    except Exception:
                        pass
                    if active_page in self._worker_pages:
                        self._worker_pages.remove(active_page)
                try:
                    active_page = await self._new_work_page()
                    fresh = await self.browser.list_conversations(active_page)
                    if not fresh:
                        await active_page.wait_for_timeout(1000)
                        fresh = await self.browser.list_conversations(active_page)
                    if not fresh:
                        skips.append((name, "打开会话失败"))
                        continue
                except Exception:
                    skips.append((name, "打开会话失败"))
                    continue
            if not await self.browser.open_conversation_by_title(active_page, name):
                skips.append((name, "打开会话失败"))
                continue
            messages, fresh = await _read_conversation(self.browser, active_page, conv)
            if not messages:
                skips.append((name, "没有读到消息"))
                continue
            if not fresh:
                newest_text = str(messages[-1].get("text") or "").strip()
                if newest_text in ("打个招呼吧", "发个消息吧", "开始聊天吧"):
                    skips.append((name, "对方还没发消息"))
                else:
                    skips.append((name, "会话内容没有切换"))
                continue
            latest = messages[-1]
            latest_text = str(latest.get("text") or "").strip()
            if not latest_text:
                if latest.get("videos"):
                    latest_text = "[对方发来视频]"
                elif latest.get("images"):
                    latest_text = "[对方发来图片]"
                else:
                    skips.append((name, "最后一条没有文字"))
                    continue
            contact_avatar = _base_url(str(conv.get("avatar") or ""))
            latest_avatar = _base_url(str(latest.get("avatar") or ""))
            if latest_avatar:
                incoming = bool(contact_avatar) and latest_avatar == contact_avatar
            else:
                incoming = bool(conv["preview"]) and (
                    conv["preview"] in latest_text or latest_text in conv["preview"]
                )
            if not incoming and int(conv.get("unread") or 0) > 0:
                # 有红点说明对方确实发了新消息；可能是消息列表还没刷新，再读一次。
                await active_page.wait_for_timeout(1200)
                retry_messages, _ = await _read_conversation(self.browser, active_page, conv)
                if retry_messages:
                    latest = retry_messages[-1]
                    latest_text = str(latest.get("text") or "").strip() or latest_text
                    latest_avatar = _base_url(str(latest.get("avatar") or ""))
                    contact_avatar = _base_url(str(conv.get("avatar") or ""))
                    if latest_avatar:
                        incoming = bool(contact_avatar) and latest_avatar == contact_avatar
                    else:
                        incoming = bool(conv["preview"]) and (
                            conv["preview"] in latest_text or latest_text in conv["preview"]
                        )
            if not incoming:
                skips.append((name, "最后一条不是对方发的"))
                continue
            dry_key = f"{target_key}|{latest_text}"
            if settings.dry_run and dry_key in self._dry_run_seen:
                continue

            transcript = messages[-8:]
            try:
                crm.note_incoming(
                    target_key, name, str(conv.get("avatar") or ""), latest_text
                )
            except Exception:
                pass
            for item in transcript:
                avatar = _base_url(str(item.get("avatar") or ""))
                item["outgoing"] = bool(avatar and contact_avatar and avatar != contact_avatar)
            context = {
                "contact": name,
                "relation": "互相关注" if conv["relation"] == "mutual" else ("白名单" if conv["allowlisted"] else "已有会话"),
                "messages": [
                    {
                        "text": str(item.get("text") or "").strip(),
                        "time": str(item.get("time") or ""),
                        "outgoing": bool(item.get("outgoing")),
                        "images": item.get("images") or [],
                        "videos": item.get("videos") or [],
                    }
                    for item in transcript
                ],
            }
            decision, usage = await decider.reply("dm", context, db.recent_replies(6))
            if not decision.share and db.reply_exists(
                "reply_dm", target_key, latest_text, max_age_seconds=60
            ):
                skips.append((name, "同一句话刚回复过"))
                continue
            if decision.share:
                try:
                    share_result = await share_one_to_contact(
                        self.browser,
                        settings,
                        name,
                        decision.share_query,
                        latest_text,
                    )
                except Exception as exc:
                    share_result = {"status": "failed", "detail": str(exc)[:300]}
                share_status = str(share_result.get("status") or "failed")
                if share_status in ("sent", "uncertain"):
                    if not settings.dry_run:
                        db.add_reply(
                            kind="reply_dm",
                            target_key=target_key,
                            incoming=latest_text,
                            reply="[视频分享]",
                            status="sent",
                        )
                    handled += 1
                    try:
                        crm.note_reply(target_key, "[视频分享]")
                    except Exception:
                        pass
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
                        level="info",
                    )
                    continue
                # 分享失败也要回一句，不能把对方晾在“已读不回”上。
                fallback = policy.clean_text(decision.reply, settings.max_dm_chars)
                if not fallback:
                    fallback = "刚去给你找视频了，没找到合适的，换个更具体的说法再试试？"
                if settings.dry_run:
                    skips.append((name, "预演：分享未发送"))
                    continue
                try:
                    sent = await self.browser.send_chat_message(active_page, fallback)
                except Exception as exc:
                    skips.append((name, "分享和文字回复都失败"))
                    await bus.emit(
                        "reply",
                        f"点播分享失败：{name} · {exc}",
                        {"contact": name, "detail": share_result.get("detail") or ""},
                        level="error",
                    )
                    continue
                if str(sent.get("status") or "") in ("sent", "uncertain"):
                    db.add_reply(
                        kind="reply_dm",
                        target_key=target_key,
                        incoming=latest_text,
                        reply=fallback,
                        status=str(sent.get("status") or "uncertain"),
                    )
                    handled += 1
                    try:
                        crm.note_reply(target_key, fallback)
                    except Exception:
                        pass
                await self._record(
                    settings,
                    "reply_dm",
                    target_key,
                    name,
                    urls.get(name, ""),
                    latest_text,
                    fallback,
                    str(sent.get("status") or "uncertain"),
                    decision.reason,
                    f"分享失败，已改发文字：{share_result.get('detail') or ''}"[:300],
                )
                await bus.emit(
                    "reply",
                    f"点播分享失败，已回文字 {name}：{fallback}",
                    {
                        "contact": name,
                        "incoming": latest_text[:120],
                        "share_status": share_status,
                        "detail": share_result.get("detail") or "",
                        "reply": fallback,
                    },
                    level="warn",
                )
                continue
            reply = policy.clean_text(decision.reply, settings.max_dm_chars)
            if not reply:
                skips.append((name, "模型判断不需要回复"))
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
                skips.append((name, f"策略拦截：{check.reason}"))
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
                result = await self.browser.send_chat_message(active_page, reply)
            except Exception as exc:
                skips.append((name, "发送失败"))
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
                try:
                    crm.note_reply(target_key, reply)
                except Exception:
                    pass
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
        signature = tuple(sorted(set(skips)))
        if signature and signature != self._last_skip_signature:
            preview = "；".join(f"{n}（{r}）" for n, r in signature[:6])
            extra = f" 等 {len(signature)} 个会话" if len(signature) > 6 else ""
            await bus.emit(
                "reply",
                f"私信跳过：{preview}{extra}",
                {"skips": [{"name": n, "reason": r} for n, r in signature]},
                level="info",
            )
            self._last_skip_signature = signature
        elif not signature:
            self._last_skip_signature = ()
        return handled

    async def _notice_comments(self, page: Page, settings: AgentSettings) -> int:
        rows = await self.browser.read_notifications(page, "评论")
        actionable = [
            row
            for row in rows
            if row.get("kind") == "comment"
            and str(row.get("comment") or "").strip()
            and "评论了你的作品" in str(row.get("text") or "")
            and len(str(row.get("text") or "")) <= 400
        ]
        if not actionable:
            return 0

        async def load_works() -> list[dict[str, str]]:
            js = """() => [...document.querySelectorAll('[data-e2e="user-post-list"] a[href*="/note/"], [data-e2e="user-post-list"] a[href*="/video/"]')].map(a => {
              const img = a.querySelector('img');
              return {
                url: a.href || '',
                cover: ((img && img.src) || '').split('?')[0],
                title: ((img && img.alt) || '').slice(0, 300),
              };
            })"""
            try:
                works = await page.evaluate(js)
            except Exception:
                works = []
            if not works:
                try:
                    await page.goto("https://www.douyin.com/user/self", wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(2500)
                    works = await page.evaluate(js)
                except Exception:
                    works = []
            out: list[dict[str, str]] = []
            for item in works or []:
                if not isinstance(item, dict) or not item.get("url"):
                    continue
                url = str(item["url"])
                if url.startswith("//"):
                    url = "https:" + url
                out.append(
                    {
                        "url": url,
                        "cover": str(item.get("cover") or ""),
                        "title": str(item.get("title") or ""),
                    }
                )
            return out

        works = await load_works()
        decider = DeepSeekDecider(settings)
        handled = 0
        for row in actionable[:2]:
            name = str(row.get("name") or "").strip()
            comment = str(row.get("comment") or "").strip()
            notification = str(row.get("text") or "").strip()
            if not name or not comment:
                continue
            thumb = str(row.get("thumb") or "").split("?")[0]
            target = next((work for work in works if thumb and work.get("cover") == thumb), None)
            if target is None and len(works) == 1:
                target = works[0]
            if target is None:
                await bus.emit(
                    "reply",
                    f"评论跳过：{name}（找不到对应作品）",
                    {"contact": name, "comment": comment[:120]},
                    level="warn",
                )
                continue
            work_url = target["url"]
            if db.reply_exists("reply_comment", work_url, comment):
                continue
            await bus.emit(
                "reply",
                f"检测到评论：{name}",
                {
                    "contact": name,
                    "comment": comment[:200],
                    "work_url": work_url,
                    "work_title": target.get("title", "")[:160],
                },
            )
            context = {
                "title": target.get("title") or "",
                "video_author": settings.agent_name or "我",
                "notification": notification,
                "contact": name,
                "comment": comment,
                "our_comment": "",
            }
            try:
                decision, usage = await decider.reply("comment", context, db.recent_replies(6))
            except LLMError as exc:
                await bus.emit(
                    "reply",
                    f"评论回复模型调用失败：{name}",
                    {"contact": name, "error": str(exc)[:200]},
                    level="error",
                )
                continue
            reply = policy.clean_text(decision.reply, settings.max_comment_chars)
            if not reply:
                db.add_reply(
                    kind="reply_comment",
                    target_key=work_url,
                    incoming=comment,
                    reply="",
                    status="skipped",
                )
                await bus.emit(
                    "reply",
                    f"评论不回复：{name}",
                    {
                        "contact": name,
                        "comment": comment[:120],
                        "reason": decision.reason,
                        "confidence": decision.confidence,
                        "usage": usage,
                    },
                )
                continue
            check = policy.check_comment_reply(name, reply, settings)
            if not check.allowed:
                db.add_reply(
                    kind="reply_comment",
                    target_key=work_url,
                    incoming=comment,
                    reply="",
                    status="blocked",
                )
                await self._record(
                    settings,
                    "reply_comment",
                    work_url,
                    name,
                    work_url,
                    comment,
                    reply,
                    "blocked",
                    check.reason,
                )
                await bus.emit(
                    "reply",
                    f"评论回复被策略拦截：{check.reason}",
                    {"contact": name, "reply": reply},
                    level="warn",
                )
                continue
            if settings.dry_run:
                await self._record(
                    settings,
                    "reply_comment",
                    work_url,
                    name,
                    work_url,
                    comment,
                    reply,
                    "dry_run",
                    decision.reason,
                    "预演，未发送",
                )
                await bus.emit(
                    "reply",
                    f"预演评论回复 → {name}：{reply}",
                    {"contact": name, "comment": comment[:120], "reply": reply, "dry_run": True},
                )
                continue
            result = await self.browser.reply_comment_on_work(work_url, name, comment, reply)
            status = str(result.get("status") or "failed")
            if status in ("sent", "uncertain"):
                db.add_reply(
                    kind="reply_comment",
                    target_key=work_url,
                    incoming=comment,
                    reply=reply,
                    status=status,
                )
                handled += 1
            await self._record(
                settings,
                "reply_comment",
                work_url,
                name,
                work_url,
                comment,
                reply,
                status,
                decision.reason,
                str(result.get("detail") or ""),
            )
            await bus.emit(
                "reply",
                f"已回复评论 {name}：{reply}" if status == "sent" else f"评论回复{status}：{name}",
                {
                    "contact": name,
                    "comment": comment[:120],
                    "reply": reply,
                    "status": status,
                    "detail": result.get("detail") or "",
                    "screenshot": result.get("screenshot") or "",
                },
                level="info" if status == "sent" else "warn",
            )
        return handled

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
