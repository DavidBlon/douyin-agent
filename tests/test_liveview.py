from __future__ import annotations

import asyncio
import time

from app import liveview as liveview_mod


class FakePage:
    def __init__(self):
        self.url = "https://www.douyin.com/?recommend=1"

    def is_closed(self):
        return False


class FakeBrowser:
    def __init__(self):
        self.page = None


def test_frame_fanout_drops_oldest_when_full():
    view = liveview_mod.LiveView(FakeBrowser())
    queue: asyncio.Queue = asyncio.Queue(maxsize=2)
    view._clients.add(queue)
    view._on_frame({"data": "ZjE=", "sessionId": 1})
    view._on_frame({"data": "ZjI=", "sessionId": 2})
    view._on_frame({"data": "ZjM=", "sessionId": 3})
    assert [queue.get_nowait(), queue.get_nowait()] == ["ZjI=", "ZjM="]
    assert queue.empty()


def test_status_reflects_clients():
    view = liveview_mod.LiveView(FakeBrowser())
    assert view.status()["clients"] == 0
    assert view.status()["streaming"] is False
    queue: asyncio.Queue = asyncio.Queue()
    view._clients.add(queue)
    assert view.status()["clients"] == 1


def test_attach_skips_without_page():
    async def run():
        view = liveview_mod.LiveView(FakeBrowser())
        await view._attach()
        assert view.streaming is False

    asyncio.run(run())


class _Page:
    def __init__(self, url: str):
        self.url = url

    def is_closed(self):
        return False


class _ActivityBrowser:
    def __init__(self):
        self.page = _Page("https://www.douyin.com/?recommend=1")
        self.activity_page = _Page("https://www.douyin.com/user/self")
        self.activity_at = time.monotonic()


class _Runner:
    def __init__(self, running: bool):
        self.running = running


def test_pick_page_follows_ai_worker_when_idle():
    browser = _ActivityBrowser()
    view = liveview_mod.LiveView(browser, _Runner(False))
    assert view._pick_page() is browser.activity_page


def test_pick_page_prefers_feed_while_running():
    browser = _ActivityBrowser()
    view = liveview_mod.LiveView(browser, _Runner(True))
    assert view._pick_page() is browser.page
