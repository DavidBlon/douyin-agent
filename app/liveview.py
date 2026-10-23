"""把专用 Chrome 当前页的画面以 CDP 屏幕录制流推给前端，形成“AI 视角”直播。"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from playwright.async_api import CDPSession, Page

from .browser import BrowserManager

MAX_WIDTH = 540
MAX_HEIGHT = 960
QUALITY = 55
QUEUE_SIZE = 24


class LiveView:
    def __init__(self, browser: BrowserManager, runner: Any = None) -> None:
        self.browser = browser
        self.runner = runner
        self._clients: set[asyncio.Queue] = set()
        self._cdp: CDPSession | None = None
        self._page: Page | None = None
        self._lock = asyncio.Lock()
        self._supervisor: asyncio.Task | None = None
        self.last_error = ""

    def _pick_page(self) -> Page | None:
        page: Page | None = None
        running = bool(self.runner and getattr(self.runner, "running", False))
        if running:
            page = self.browser.page
        else:
            active = getattr(self.browser, "activity_page", None)
            activity_at = float(getattr(self.browser, "activity_at", 0.0) or 0.0)
            fresh = time.monotonic() - activity_at < 180
            if active is not None and fresh and not active.is_closed() and "douyin.com" in (active.url or ""):
                page = active
        if page is None or page.is_closed():
            page = self.browser.page
        if page is None or page.is_closed() or not (page.url or "").startswith("http"):
            return None
        return page

    @property
    def streaming(self) -> bool:
        return bool(self._cdp is not None and self._page is not None and not self._page.is_closed())

    def status(self) -> dict[str, Any]:
        return {
            "clients": len(self._clients),
            "streaming": self.streaming,
            "url": self._page.url if self.streaming and self._page else "",
            "last_error": self.last_error,
        }

    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._clients.add(queue)
        if self._supervisor is None or self._supervisor.done():
            self._supervisor = asyncio.create_task(self._supervise())
        await self._attach(self._pick_page())
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._clients.discard(queue)
        if not self._clients:
            await self._detach()

    async def _supervise(self) -> None:
        while self._clients:
            target = self._pick_page()
            if target is None:
                await self._detach()
            elif target is not self._page:
                await self._detach()
                await self._attach(target)
            elif not self.streaming:
                await self._attach(target)
            await asyncio.sleep(2)
        await self._detach()

    async def _attach(self, target: Page | None = None) -> None:
        async with self._lock:
            if self.streaming:
                return
            await self._detach_locked()
            page = target or self._pick_page()
            if page is None or page.is_closed() or not page.url.startswith("http"):
                return
            cdp: CDPSession | None = None
            try:
                cdp = await page.context.new_cdp_session(page)
                cdp.on("Page.screencastFrame", self._on_frame)
                await cdp.send(
                    "Page.startScreencast",
                    {
                        "format": "jpeg",
                        "quality": QUALITY,
                        "maxWidth": MAX_WIDTH,
                        "maxHeight": MAX_HEIGHT,
                        "everyNthFrame": 1,
                    },
                )
            except Exception as exc:
                self.last_error = str(exc)[:200]
                if cdp is not None:
                    try:
                        await cdp.detach()
                    except Exception:
                        pass
                return
            self._cdp = cdp
            self._page = page
            self.last_error = ""

    async def _detach(self) -> None:
        async with self._lock:
            await self._detach_locked()

    async def _detach_locked(self) -> None:
        cdp, self._cdp = self._cdp, None
        self._page = None
        if cdp is None:
            return
        try:
            await cdp.send("Page.stopScreencast")
        except Exception:
            pass
        try:
            await cdp.detach()
        except Exception:
            pass

    def _on_frame(self, params: dict) -> None:
        data = str(params.get("data") or "")
        session_id = params.get("sessionId")
        if not data:
            return
        for queue in list(self._clients):
            while queue.full():
                try:
                    queue.get_nowait()
                except Exception:
                    break
            try:
                queue.put_nowait(data)
            except Exception:
                continue
        if session_id is not None and self._cdp is not None:
            asyncio.create_task(self._ack(session_id))

    async def _ack(self, session_id: int) -> None:
        cdp = self._cdp
        if cdp is None:
            return
        try:
            await cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
        except Exception:
            pass


_liveview: LiveView | None = None


def init_liveview(browser: BrowserManager, runner: Any = None) -> LiveView:
    global _liveview
    _liveview = LiveView(browser, runner)
    return _liveview


def get_liveview() -> LiveView:
    if _liveview is None:
        raise RuntimeError("liveview 还没有初始化")
    return _liveview
