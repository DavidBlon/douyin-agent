from __future__ import annotations

import asyncio
import base64
from datetime import datetime
from typing import Any

import httpx
from playwright.async_api import Page

from . import db
from .agent import AgentRunner
from .browser import ActionError, BrowserManager, CURRENT_ID_JS, LoginRequired, READ_JS, RiskControl
from .config import AgentSettings, load_api_key
from .events import bus
from .llm import DeepSeekDecider, LLMError

FEED_URL = "https://www.douyin.com/?recommend=1&from_nav=1"


async def fetch_cover_data_url(url: str) -> str:
    url = (url or "").strip()
    if not url.startswith("http"):
        return ""
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"Referer": "https://www.douyin.com/"})
            if resp.status_code != 200:
                return ""
            data = resp.content
            if not data or len(data) > 8_000_000:
                return ""
            mime = str(resp.headers.get("content-type") or "image/jpeg").split(";", 1)[0]
            if not mime.startswith("image/"):
                mime = "image/jpeg"
            return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    except Exception:
        return ""


async def send_text_to_contact(browser: BrowserManager, page: Page, target: str, text: str) -> bool:
    conversations = await browser.list_conversations(page)
    conv = next(
        (
            c
            for c in conversations
            if not c.get("is_group") and str(c.get("title") or "").strip() == target
        ),
        None,
    )
    if not conv:
        return False
    if not await browser.open_conversation(page, int(conv.get("index") or 0)):
        return False
    try:
        await browser.send_chat_message(page, text)
        return True
    finally:
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
        except Exception:
            pass


async def send_link_to_contact(browser: BrowserManager, target: str, text: str) -> bool:
    """开一个干净页面发兜底链接，避免当前页被视频弹层/分享面板卡住。"""
    if not browser.context:
        return False
    page = await browser.new_page()
    page.set_default_timeout(15000)
    try:
        await page.goto(FEED_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1800)
        return await send_text_to_contact(browser, page, target, text)
    except Exception:
        return False
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def _scroll_feed(page: Page) -> None:
    try:
        before = str(await page.evaluate(CURRENT_ID_JS) or "")
    except Exception:
        before = ""
    try:
        await page.keyboard.press("ArrowDown")
        await page.wait_for_timeout(1500)
        after = str(await page.evaluate(CURRENT_ID_JS) or "")
        if after and after != before:
            return
    except Exception:
        pass
    try:
        arrow = page.locator('[data-e2e="video-switch-next-arrow"]')
        for i in range(await arrow.count()):
            if await arrow.nth(i).is_visible():
                await arrow.nth(i).click()
                await page.wait_for_timeout(1500)
                return
    except Exception:
        pass


async def _feed_share_one(
    browser: BrowserManager,
    settings: AgentSettings,
    target: str,
    query: str = "",
) -> dict[str, Any]:
    """找一条视频，发介绍 + 原生分享卡片给当前请求者；一次只要一条。"""
    target = (target or "").strip()
    if not target:
        return {"status": "failed", "detail": "缺少请求分享的账号"}
    if not browser.context:
        return {"status": "failed", "detail": "浏览器上下文不可用"}
    page = await browser.new_page()
    page.set_default_timeout(15000)
    decider = DeepSeekDecider(settings)
    try:
        await page.goto(FEED_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2800)
        try:
            await page.evaluate(
                "() => document.querySelectorAll('video').forEach(v => { v.muted = true; if (v.pause) v.pause(); })"
            )
        except Exception:
            pass
        seen: set[str] = set()
        for _ in range(10):
            try:
                data = await page.evaluate(READ_JS)
            except Exception:
                break
            if not data:
                break
            video = dict(data)
            aid = str(video.get("aweme_id") or "")
            if not aid or video.get("is_live") or aid in seen:
                await _scroll_feed(page)
                continue
            seen.add(aid)
            if db.has_action(aid, "share"):
                await _scroll_feed(page)
                continue
            cover_data = await fetch_cover_data_url(str(video.get("cover") or ""))
            decision, usage = await decider.share(
                video,
                cover_data,
                topics_override=query.strip() or settings.share_topics,
            )
            await bus.emit(
                "share_scan",
                f"点播扫描：{video.get('author') or '未知作者'} · {decision.reason or '不匹配'}",
                {
                    "aweme_id": aid,
                    "score": decision.score,
                    "reason": decision.reason,
                    "query": query,
                    "cover_used": bool(cover_data),
                    "usage": usage,
                },
            )
            if not decision.interesting or decision.score < settings.share_min_score:
                await _scroll_feed(page)
                continue

            title = str(video.get("title") or video.get("description") or "")[:200]
            intro = decision.intro.strip() or str(video.get("description") or "")[:200]
            message = f"【视频介绍】{title}\n{intro}".strip()
            if settings.dry_run:
                action_id = db.add_action(
                    kind="share",
                    video_id=aid,
                    target_name=target,
                    content=message,
                    status="dry_run",
                    policy=decision.reason,
                    dry_run=True,
                )
                db.update_action(action_id, result="预演，未分享", executed_at=db.now_iso())
                await bus.emit(
                    "share",
                    f"预演点播分享 → {target}：{title}",
                    {"aweme_id": aid, "target": target, "intro": message, "dry_run": True, "cover_used": bool(cover_data)},
                )
                return {"status": "dry_run", "detail": "预演，未发送", "aweme_id": aid}

            if not await send_text_to_contact(browser, page, target, message):
                return {"status": "failed", "detail": f"没有找到「{target}」的会话", "aweme_id": aid}
            try:
                result = await browser.share_current_video(page, target)
            except Exception as exc:
                result = {"status": "failed", "detail": str(exc)[:300]}
            status = str(result.get("status") or "uncertain")
            detail = str(result.get("detail") or "")
            if status == "failed":
                link = f"https://www.douyin.com/video/{aid}"
                if await send_text_to_contact(
                    browser, page, target, f"视频卡片没发出去，先给你链接：{link}"
                ):
                    status = "sent"
                    detail = "原生视频卡片失败，已补发视频链接"
            action_id = db.add_action(
                kind="share",
                video_id=aid,
                target_name=target,
                content=message,
                status=status,
                policy=decision.reason,
            )
            db.update_action(action_id, result=detail[:400], executed_at=db.now_iso())
            await bus.emit(
                "share",
                f"点播分享 → {target}：{title}",
                {
                    "aweme_id": aid,
                    "target": target,
                    "intro": message,
                    "status": status,
                    "detail": detail,
                    "cover_used": bool(cover_data),
                },
                level="info" if status == "sent" else "warn",
            )
            return {"status": status, "detail": detail, "aweme_id": aid}

        not_found = "我找了一圈，暂时没找到合适的视频，换个说法再试试？"
        await send_text_to_contact(browser, page, target, not_found)
        await bus.emit("share", f"点播分享未找到：{target}", {"target": target, "query": query}, level="warn")
        return {"status": "not_found", "detail": not_found}
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def _search_results(page: Page, query: str) -> tuple[list[dict[str, Any]], bool]:
    search_input = page.locator('[data-e2e="searchbar-input"]')
    if not await search_input.count():
        return [], False
    try:
        await search_input.first.click()
        await search_input.first.fill(query)
        await page.keyboard.press("Enter")
    except Exception:
        return [], False
    await page.wait_for_timeout(3500)
    try:
        body = await page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception:
        body = ""
    if "点击两个形状相同" in body or "安全验证" in body or "拖动滑块" in body:
        return [], True
    try:
        await page.evaluate(
            """() => {
                const els = [...document.querySelectorAll('div,span,a')].filter(el => {
                    const t = (el.innerText || '').trim();
                    const r = el.getBoundingClientRect();
                    return t === '视频' && r.width > 0 && r.height > 0 && r.y > 50 && r.y < 115 && r.x > 100 && r.x < 260;
                });
                if (els.length) els[0].click();
            }"""
        )
    except Exception:
        pass
    await page.wait_for_timeout(2800)
    try:
        cards = await page.evaluate(
            """() => {
                const seen = new Set();
                const out = [];
                for (const a of document.querySelectorAll('a[href*="/video/"]')) {
                    const r = a.getBoundingClientRect();
                    if (r.width < 80 || r.height < 60 || r.bottom <= 0 || r.top >= innerHeight) continue;
                    const m = a.href.match(/\\/video\\/(\\d+)/);
                    if (!m || seen.has(m[1])) continue;
                    seen.add(m[1]);
                    const text = (a.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 500);
                    const imgs = [...a.querySelectorAll('img')]
                        .map(i => i.currentSrc || i.src)
                        .filter(s => s && !s.includes('aweme-avatar') && !s.includes('avatar'));
                    out.push({id: m[1], href: a.href, text, cover: imgs[0] || ''});
                }
                return out.slice(0, 12);
            }"""
        )
    except Exception:
        cards = []
    return [dict(c) for c in (cards or []) if isinstance(c, dict)], False


def _first_topic(topics: str) -> str:
    import re

    parts = [p for p in re.split(r"[，,、\\s]+", (topics or "").strip()) if p]
    return parts[0] if parts else "视频"


async def share_one_to_contact(
    browser: BrowserManager,
    settings: AgentSettings,
    target: str,
    query: str = "",
    request_text: str = "",
) -> dict[str, Any]:
    """搜索一条视频，发介绍 + 原生分享卡片给当前请求者；一次只发一条。"""
    target = (target or "").strip()
    if not target:
        return {"status": "failed", "detail": "缺少请求分享的账号"}
    if not browser.context:
        return {"status": "failed", "detail": "浏览器上下文不可用"}
    search_query = (query or "").strip() or _first_topic(settings.share_topics)
    request_key = (request_text or query or search_query).strip()
    page = await browser.new_page()
    page.set_default_timeout(15000)
    decider = DeepSeekDecider(settings)
    try:
        await page.goto(FEED_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2800)
        cards, blocked = await _search_results(page, search_query)
        if blocked:
            await send_text_to_contact(
                browser, page, target, "抖音搜索需要你先在专用浏览器里完成一次验证，验证后再让我找。"
            )
            await bus.emit("share", f"点播搜索被验证拦截：{target}", {"query": search_query}, level="warn")
            return {"status": "search_blocked", "detail": "搜索需要手动完成验证"}

        excluded = db.shared_video_ids_for_request(request_key, target)
        for card in cards:
            aid = str(card.get("id") or "")
            if not aid or aid in excluded or db.has_action(aid, "share"):
                continue
            text = str(card.get("text") or "")
            author = ""
            for token in text.split(" "):
                if token.startswith("@"):
                    author = token[1:]
                    break
            video = {
                "aweme_id": aid,
                "title": text[:200],
                "description": text,
                "author": author,
                "cover": str(card.get("cover") or ""),
                "stats": {},
            }
            cover_data = await fetch_cover_data_url(str(video.get("cover") or ""))
            decision, usage = await decider.share(
                video,
                cover_data,
                topics_override=query.strip() or settings.share_topics,
            )
            await bus.emit(
                "share_scan",
                f"点播搜索：{author or '未知作者'} · {decision.reason or '不匹配'}",
                {
                    "aweme_id": aid,
                    "score": decision.score,
                    "reason": decision.reason,
                    "query": search_query,
                    "cover_used": bool(cover_data),
                    "usage": usage,
                },
            )
            if not decision.interesting or decision.score < settings.share_min_score:
                continue

            title = str(video.get("title") or "")[:200]
            intro = decision.intro.strip() or str(video.get("description") or "")[:200]
            message = f"【视频介绍】{title}\n{intro}".strip()
            if settings.dry_run:
                action_id = db.add_action(
                    kind="share",
                    video_id=aid,
                    target_name=target,
                    content=message,
                    status="dry_run",
                    policy=decision.reason,
                    dry_run=True,
                )
                db.update_action(action_id, result="预演，未分享", executed_at=db.now_iso())
                await bus.emit(
                    "share",
                    f"预演点播分享 → {target}：{title}",
                    {"aweme_id": aid, "target": target, "intro": message, "dry_run": True, "cover_used": bool(cover_data)},
                )
                return {"status": "dry_run", "detail": "预演，未发送", "aweme_id": aid}

            # 介绍必须先发；发完 IM 面板会盖住搜索页，回 feed 重新搜一次再打开结果。
            if not await send_text_to_contact(browser, page, target, message):
                return {"status": "failed", "detail": f"没有找到「{target}」的会话", "aweme_id": aid}
            await page.goto(FEED_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2200)
            cards2, blocked2 = await _search_results(page, search_query)
            opened = False
            has_share = False
            if not blocked2:
                opened = await page.evaluate(
                    """(id) => {
                        const a = [...document.querySelectorAll('a[href*="/video/"]')].find(a => a.href.includes(id));
                        if (!a) return false;
                        a.scrollIntoView({block: 'center'});
                        a.click();
                        return true;
                    }""",
                    aid,
                )
                for _ in range(32):
                    await page.wait_for_timeout(250)
                    try:
                        share_btns = page.locator('[data-e2e="video-player-share"]')
                        for i in range(await share_btns.count()):
                            if await share_btns.nth(i).is_visible():
                                has_share = True
                                break
                    except Exception:
                        pass
                    if has_share:
                        break

            link = f"https://www.douyin.com/video/{aid}"
            if blocked2 or not opened or not has_share:
                sent = await send_link_to_contact(browser, target, f"视频卡片没发出去，先给你链接：{link}")
                status = "sent" if sent else "failed"
                detail = "搜索结果播放器不可用，已补发视频链接" if sent else "视频卡片和链接都发送失败"
            else:
                try:
                    result = await browser.share_current_video(page, target)
                except Exception as exc:
                    result = {"status": "failed", "detail": str(exc)[:300]}
                status = str(result.get("status") or "uncertain")
                detail = str(result.get("detail") or "")
                if status == "failed":
                    if await send_link_to_contact(browser, target, f"视频卡片没发出去，先给你链接：{link}"):
                        status = "sent"
                        detail = "原生视频卡片失败，已补发视频链接"
            action_id = db.add_action(
                kind="share",
                video_id=aid,
                target_name=target,
                content=message,
                status=status,
                policy=decision.reason,
            )
            db.update_action(action_id, result=detail[:400], executed_at=db.now_iso())
            if status in ("sent", "uncertain"):
                db.add_share_history(request_key, target, aid)
            await bus.emit(
                "share",
                f"点播分享 → {target}：{title}",
                {
                    "aweme_id": aid,
                    "target": target,
                    "intro": message,
                    "status": status,
                    "detail": detail,
                    "cover_used": bool(cover_data),
                    "query": search_query,
                },
                level="info" if status == "sent" else "warn",
            )
            return {"status": status, "detail": detail, "aweme_id": aid}

        # 搜索结果没有合适的，退回推荐流再找一次（不改变“一条”规则）。
        fallback = await _feed_share_one(browser, settings, target, query)
        if fallback.get("status") in ("sent", "uncertain", "dry_run", "not_found"):
            return fallback
        await send_text_to_contact(
            browser, page, target, "我搜了一圈，暂时没找到合适的视频，换个更具体的说法再试试？"
        )
        await bus.emit("share", f"点播未找到：{target}", {"query": search_query}, level="warn")
        return {"status": "not_found", "detail": "没有找到合适的视频"}
    finally:
        try:
            await page.close()
        except Exception:
            pass


class AutoSharer:
    def __init__(self, runner: AgentRunner) -> None:
        self.runner = runner
        self.browser: BrowserManager = runner.browser
        self.task: asyncio.Task | None = None
        self._stop = asyncio.Event()
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
            "enabled": settings.auto_share,
            "running": self.running,
            "busy": self.busy,
            "target": settings.share_target,
            "times": [settings.share_time_1, settings.share_time_2],
            "last_check": self.last_check,
            "last_error": self.last_error,
            "last_result": self.last_result,
            "today": db.count_actions_today("share"),
        }

    async def apply_settings(self) -> None:
        settings = db.load_settings()
        if settings.auto_share:
            await self.start()
        else:
            await self.stop()

    async def start(self) -> None:
        if self.running:
            return
        self._stop = asyncio.Event()
        self.task = asyncio.create_task(self._loop())
        settings = db.load_settings()
        await bus.emit("share_status", "定时分享已启动", {"times": [settings.share_time_1, settings.share_time_2]})

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
        await bus.emit("share_status", "定时分享已停止")

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except LoginRequired as exc:
                self.last_error = str(exc)
                await bus.emit("share_status", "定时分享等待登录", {"error": str(exc)[:200]}, level="warn")
            except RiskControl as exc:
                self.last_error = str(exc)
                await bus.emit("share_status", "定时分享已停止：页面出现验证", {"error": str(exc)[:200]}, level="error")
                self._stop.set()
                break
            except Exception as exc:
                self.last_error = str(exc)[:300]
                await bus.emit("share_status", "定时分享失败", {"error": str(exc)[:200]}, level="warn")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=25)
            except asyncio.TimeoutError:
                pass

    @staticmethod
    def _due_slot(settings: AgentSettings) -> str:
        now = datetime.now()
        for slot in (settings.share_time_1, settings.share_time_2):
            try:
                hour, minute = [int(x) for x in slot.split(":", 1)]
                target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            except Exception:
                continue
            diff = (now - target).total_seconds()
            if 0 <= diff <= 300:
                return slot
        return ""

    async def check_once(self, force: bool = False) -> dict[str, Any]:
        settings = db.load_settings()
        if not settings.auto_share and not force:
            return {"ok": True, "skipped": "disabled"}
        target = settings.share_target.strip()
        if not target:
            raise ActionError("还没有设置分享接收账号")
        slot = "manual" if force else self._due_slot(settings)
        if not slot:
            return {"ok": True, "skipped": "not_due"}
        if not force and db.share_ran(db.today_key(), slot):
            return {"ok": True, "skipped": "already_ran"}
        if self.busy:
            return {"ok": True, "skipped": "busy"}
        if self.runner.running or self.runner.exclusive.locked():
            return {"ok": True, "skipped": "run_in_progress"}
        if not load_api_key():
            raise LLMError("还没有配置 API Key，定时分享无法运行")

        self.busy = True
        page: Page | None = None
        try:
            async with self.runner.exclusive:
                await self.browser.ensure_started()
                if not await self.browser.is_logged_in():
                    raise LoginRequired("浏览器还没有登录抖音，定时分享暂停")
                if not self.browser.context:
                    raise ActionError("浏览器上下文不可用")
                page = await self.browser.new_page()
                page.set_default_timeout(15000)
                await page.goto(FEED_URL, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(2800)
                try:
                    await page.evaluate(
                        "() => document.querySelectorAll('video').forEach(v => { v.muted = true; if (v.pause) v.pause(); })"
                    )
                except Exception:
                    pass
                self.last_check = db.now_iso()
                self.last_error = ""
                result = await self._scan_and_share(page, settings, target)
                self.last_result = result
                if not force:
                    db.mark_share_run(db.today_key(), slot)
                await bus.emit(
                    "share_cycle",
                    f"定时分享检查完成：分享 {result.get('shared', 0)} 条",
                    {**result, "target": target, "slot": slot},
                )
                return {"ok": True, **result}
        finally:
            self.busy = False
            if page is not None and not page.is_closed():
                try:
                    await page.close()
                except Exception:
                    pass

    async def _cover_data_url(self, url: str) -> str:
        url = (url or "").strip()
        if not url.startswith("http"):
            return ""
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(url, headers={"Referer": "https://www.douyin.com/"})
                if resp.status_code != 200:
                    return ""
                data = resp.content
                if not data or len(data) > 8_000_000:
                    return ""
                mime = str(resp.headers.get("content-type") or "image/jpeg").split(";", 1)[0]
                if not mime.startswith("image/"):
                    mime = "image/jpeg"
                return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        except Exception:
            return ""

    async def _scan_and_share(
        self,
        page: Page,
        settings: AgentSettings,
        target: str,
    ) -> dict[str, Any]:
        decider = DeepSeekDecider(settings)
        shared = 0
        accepted = 0
        scanned = 0
        seen: set[str] = set()
        for attempt in range(10):
            if self._stop.is_set():
                break
            try:
                data = await page.evaluate(READ_JS)
            except Exception:
                break
            if not data:
                break
            video = dict(data)
            aid = str(video.get("aweme_id") or "")
            scanned += 1
            if not aid or video.get("is_live") or aid in seen:
                await self._scroll(page)
                continue
            seen.add(aid)
            if db.has_action(aid, "share"):
                await self._scroll(page)
                continue
            if not (video.get("title") or video.get("description")):
                await self._scroll(page)
                continue
            cover_data = await self._cover_data_url(str(video.get("cover") or ""))
            decision, usage = await decider.share(video, cover_data)
            if not decision.interesting or decision.score < settings.share_min_score:
                await bus.emit(
                    "share_scan",
                    f"不分享：{video.get('author') or '未知作者'} · {decision.reason or '不匹配'}",
                    {
                        "aweme_id": aid,
                        "score": decision.score,
                        "reason": decision.reason,
                        "cover_used": bool(cover_data),
                        "usage": usage,
                    },
                )
                await self._scroll(page)
                continue

            title = str(video.get("title") or video.get("description") or "")[:200]
            intro = decision.intro.strip() or str(video.get("description") or "")[:200]
            message = f"【视频介绍】{title}\n{intro}".strip()
            accepted += 1
            if settings.dry_run:
                action_id = db.add_action(
                    kind="share",
                    video_id=aid,
                    target_name=target,
                    content=message,
                    status="dry_run",
                    policy=decision.reason,
                    dry_run=True,
                )
                db.update_action(action_id, result="预演，未分享", executed_at=db.now_iso())
                await bus.emit(
                    "share",
                    f"预演分享 → {target}：{title}",
                    {"aweme_id": aid, "target": target, "intro": message, "dry_run": True, "cover_used": bool(cover_data)},
                )
            else:
                sent_intro = await self._send_intro(page, target, message)
                if not sent_intro:
                    await bus.emit(
                        "share",
                        f"分享跳过：没有找到接收账号「{target}」的会话",
                        {"aweme_id": aid, "target": target},
                        level="warn",
                    )
                    await self._scroll(page)
                    continue
                try:
                    result = await self.browser.share_current_video(page, target)
                except Exception as exc:
                    result = {"status": "failed", "detail": str(exc)[:300]}
                status = str(result.get("status") or "uncertain")
                detail = str(result.get("detail") or "")
                if status == "failed":
                    link = f"https://www.douyin.com/video/{aid}"
                    fallback = f"视频卡片没发出去，先给你链接：{link}"
                    if await self._send_intro(page, target, fallback):
                        status = "sent"
                        detail = "原生视频卡片失败，已补发视频链接"
                action_id = db.add_action(
                    kind="share",
                    video_id=aid,
                    target_name=target,
                    content=message,
                    status=status,
                    policy=decision.reason,
                )
                db.update_action(
                    action_id,
                    result=detail[:400],
                    executed_at=db.now_iso(),
                )
                if status in ("sent", "uncertain"):
                    shared += 1
                await bus.emit(
                    "share",
                    f"分享视频 → {target}：{title}",
                    {
                        "aweme_id": aid,
                        "target": target,
                        "intro": message,
                        "status": status,
                        "detail": detail,
                        "cover_used": bool(cover_data),
                    },
                    level="info" if status == "sent" else "warn",
                )
            if accepted >= settings.share_max_per_run:
                break
            await self._scroll(page)
        return {"shared": shared, "scanned": scanned, "target": target}

    async def _send_intro(self, page: Page, target: str, message: str) -> bool:
        conversations = await self.browser.list_conversations(page)
        conv = next(
            (
                c
                for c in conversations
                if not c.get("is_group") and str(c.get("title") or "").strip() == target
            ),
            None,
        )
        if not conv:
            return False
        if not await self.browser.open_conversation(page, int(conv.get("index") or 0)):
            return False
        try:
            await self.browser.send_chat_message(page, message)
            return True
        except Exception as exc:
            await bus.emit("share", f"发送视频介绍失败：{exc}", {"target": target}, level="error")
            return False
        finally:
            try:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(300)
            except Exception:
                pass

    async def _scroll(self, page: Page) -> None:
        try:
            before = str(await page.evaluate(CURRENT_ID_JS) or "")
        except Exception:
            before = ""
        try:
            await page.keyboard.press("ArrowDown")
            await page.wait_for_timeout(1500)
            after = str(await page.evaluate(CURRENT_ID_JS) or "")
            if after and after != before:
                return
        except Exception:
            pass
        try:
            arrow = page.locator('[data-e2e="video-switch-next-arrow"]')
            for i in range(await arrow.count()):
                if await arrow.nth(i).is_visible():
                    await arrow.nth(i).click()
                    await page.wait_for_timeout(1500)
                    return
        except Exception:
            pass
