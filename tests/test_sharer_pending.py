from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app import db
from app import sharer as sharer_mod
from app.config import AgentSettings


class FakeBrowser:
    async def nav_badges(self, page=None):
        return {"im": 0, "notice": 0, "avatar": 0}


def make_runner():
    return SimpleNamespace(
        browser=FakeBrowser(),
        running=False,
        run_pending=False,
        share_pending=False,
        exclusive=asyncio.Lock(),
    )


@pytest.mark.asyncio
async def test_due_slot_holds_pending_when_browser_busy(temp_db, monkeypatch):
    db.save_settings(AgentSettings(auto_share=True, share_target="氛围感奶蛙", dry_run=True))
    runner = make_runner()
    await runner.exclusive.acquire()
    agent = sharer_mod.AutoSharer(runner)
    monkeypatch.setattr(sharer_mod.AutoSharer, "_due_slot", staticmethod(lambda s: "18:16"))

    result = await agent.check_once()

    assert result["skipped"] == "run_in_progress"
    assert agent.pending_slot == "18:16"
    assert runner.share_pending is True
    runner.exclusive.release()


@pytest.mark.asyncio
async def test_pending_slot_survives_due_window(temp_db, monkeypatch):
    db.save_settings(AgentSettings(auto_share=True, share_target="氛围感奶蛙", dry_run=True))
    runner = make_runner()
    await runner.exclusive.acquire()
    agent = sharer_mod.AutoSharer(runner)
    agent.pending_slot = "18:16"
    runner.share_pending = True
    monkeypatch.setattr(sharer_mod.AutoSharer, "_due_slot", staticmethod(lambda s: ""))

    result = await agent.check_once()

    assert result["skipped"] == "run_in_progress"
    assert agent.pending_slot == "18:16"
    runner.exclusive.release()


@pytest.mark.asyncio
async def test_already_ran_clears_pending(temp_db, monkeypatch):
    db.save_settings(AgentSettings(auto_share=True, share_target="氛围感奶蛙", dry_run=True))
    runner = make_runner()
    agent = sharer_mod.AutoSharer(runner)
    agent.pending_slot = "18:16"
    runner.share_pending = True
    db.mark_share_run(db.today_key(), "18:16")
    monkeypatch.setattr(sharer_mod.AutoSharer, "_due_slot", staticmethod(lambda s: ""))

    result = await agent.check_once()

    assert result["skipped"] == "already_ran"
    assert agent.pending_slot == ""
    assert runner.share_pending is False


class _FakeKeyboard:
    async def press(self, key):
        return None


class _FakePage:
    keyboard = _FakeKeyboard()

    async def wait_for_timeout(self, ms):
        return None


class _RetryBrowser:
    def __init__(self):
        self.calls = 0
        self.sent = []

    async def list_conversations(self, page):
        self.calls += 1
        if self.calls < 3:
            return []
        return [{"index": 0, "title": "氛围感奶蛙", "is_group": False}]

    async def open_conversation(self, page, index):
        return True

    async def open_conversation_by_title(self, page, title):
        return True

    async def send_chat_message(self, page, text):
        self.sent.append(text)
        return {"status": "sent"}


@pytest.mark.asyncio
async def test_send_intro_retries_until_conversation_appears(temp_db):
    browser = _RetryBrowser()
    runner = SimpleNamespace(browser=browser, running=False, run_pending=False, share_pending=False)
    agent = sharer_mod.AutoSharer(runner)

    ok = await agent._send_intro(_FakePage(), "氛围感奶蛙", "介绍")

    assert ok is True
    assert browser.calls >= 3
    assert browser.sent == ["介绍"]


class _VideoPage:
    def __init__(self, current: str):
        self.current = current
        self.url = "https://www.douyin.com/?recommend=1"

    async def evaluate(self, script):
        return self.current

    async def goto(self, url, **kwargs):
        self.url = url
        self.current = "222"

    async def wait_for_timeout(self, ms):
        return None


@pytest.mark.asyncio
async def test_ensure_current_video_reopens_matching_id(temp_db):
    runner = SimpleNamespace(browser=_RetryBrowser(), running=False, run_pending=False, share_pending=False)
    agent = sharer_mod.AutoSharer(runner)
    page = _VideoPage("111")

    assert await agent._ensure_current_video(page, "222") is True
    assert page.url.endswith("/video/222")


@pytest.mark.asyncio
async def test_hourly_auto_share_selected_without_badge(temp_db, monkeypatch):
    db.save_settings(
        AgentSettings(auto_share=True, auto_operate=True, share_target="氛围感奶蛙", dry_run=True)
    )
    runner = make_runner()
    await runner.exclusive.acquire()
    agent = sharer_mod.AutoSharer(runner)
    agent._last_auto = 0.0
    monkeypatch.setattr(sharer_mod.AutoSharer, "_due_slot", staticmethod(lambda s: ""))

    result = await agent.check_once()

    assert result["skipped"] == "run_in_progress"
    assert agent._last_auto > 0
    assert agent.pending_slot == ""
    runner.exclusive.release()


@pytest.mark.asyncio
async def test_hourly_auto_share_waits_for_red_dot(temp_db, monkeypatch):
    db.save_settings(
        AgentSettings(auto_share=True, auto_operate=True, share_target="氛围感奶蛙", dry_run=True)
    )
    runner = make_runner()
    agent = sharer_mod.AutoSharer(runner)
    agent._last_auto = 0.0
    monkeypatch.setattr(sharer_mod.AutoSharer, "_due_slot", staticmethod(lambda s: ""))

    async def busy_badge(page=None):
        return {"im": 1, "notice": 0, "avatar": 0}

    monkeypatch.setattr(runner.browser, "nav_badges", busy_badge)
    result = await agent.check_once()

    assert result["skipped"] == "badge_wait"
    assert agent.pending_slot == ""
