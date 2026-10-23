from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app import db
from app import replier as replier_mod
from app.config import AgentSettings
from app.llm import ReplyDecision


class FakeDecider:
    def __init__(self, settings):
        self.settings = settings

    async def reply(self, kind, context, recent):
        return ReplyDecision(reply="在的，怎么啦？", reason="对方在打招呼", confidence=0.9), {"total_tokens": 10}


class FakeBrowser:
    def __init__(self, conversations=None, messages=None):
        self.conversations = conversations or []
        self.messages = messages or [{"text": "你好", "avatar": "https://img.example/a.jpg"}]
        self.sent: list[str] = []
        self.opened = 0

    async def list_conversations(self, page):
        return self.conversations

    async def open_conversation(self, page, index):
        self.opened += 1
        return True

    async def open_conversation_by_title(self, page, title):
        self.opened += 1
        return True

    async def read_chat_messages(self, page):
        return self.messages

    async def read_chat_transcript(self, page):
        rows = []
        for index, item in enumerate(self.messages):
            rows.append(
                {
                    "index": index,
                    "top": 100 + index,
                    "text": item.get("text") or "",
                    "time": item.get("time") or "",
                    "avatar": item.get("avatar") or "",
                    "images": [],
                    "videos": [],
                    "is_system": False,
                }
            )
        return rows

    async def send_chat_message(self, page, text):
        self.sent.append(text)
        return {"status": "sent", "detail": "私信已发送"}


def make_runner(browser: FakeBrowser):
    return SimpleNamespace(browser=browser, running=False, run_pending=False, exclusive=asyncio.Lock())


class FakePage:
    async def wait_for_timeout(self, ms):
        return None


def conv(**overrides):
    data = {
        "index": 0,
        "title": "互关好友",
        "preview": "你好",
        "unread": 2,
        "is_group": False,
        "avatar": "https://img.example/a.jpg",
    }
    data.update(overrides)
    return data


def mutual_rows():
    return [{"name": "互关好友", "url": "https://www.douyin.com/user/abc", "text": "关注了你 互相关注", "mutual": True}]


@pytest.mark.asyncio
async def test_replier_dm_sends_for_mutual(temp_db, monkeypatch):
    monkeypatch.setattr(replier_mod, "DeepSeekDecider", FakeDecider)
    browser = FakeBrowser(conversations=[conv()])
    agent = replier_mod.AutoReplier(make_runner(browser))
    settings = AgentSettings(
        auto_reply=True,
        auto_reply_dms=True,
        auto_reply_comments=False,
        dm_reply_scope="mutual",
        dry_run=False,
    )
    db.save_settings(settings)
    handled = await agent._reply_dms(FakePage(), settings, mutual_rows())
    assert handled == 1
    assert browser.sent == ["在的，怎么啦？"]
    assert db.reply_exists("reply_dm", replier_mod._conversation_key(conv()), "你好")
    actions = db.list_actions(page=1, size=10)
    assert actions["items"][0]["kind"] == "reply_dm"
    assert actions["items"][0]["status"] == "sent"


@pytest.mark.asyncio
async def test_replier_dm_dry_run_does_not_send(temp_db, monkeypatch):
    monkeypatch.setattr(replier_mod, "DeepSeekDecider", FakeDecider)
    browser = FakeBrowser(conversations=[conv()])
    agent = replier_mod.AutoReplier(make_runner(browser))
    settings = AgentSettings(
        auto_reply=True,
        auto_reply_dms=True,
        dm_reply_scope="mutual",
        dry_run=True,
    )
    handled = await agent._reply_dms(FakePage(), settings, mutual_rows())
    assert handled == 0
    assert browser.sent == []
    actions = db.list_actions(page=1, size=10)
    assert actions["items"][0]["status"] == "dry_run"


@pytest.mark.asyncio
async def test_replier_skips_groups_and_strangers(temp_db, monkeypatch):
    monkeypatch.setattr(replier_mod, "DeepSeekDecider", FakeDecider)
    browser = FakeBrowser(
        conversations=[
            conv(title="粉丝群", is_group=True, preview="大家好"),
            conv(title="陌生人", preview="在吗"),
        ]
    )
    agent = replier_mod.AutoReplier(make_runner(browser))
    settings = AgentSettings(
        auto_reply=True,
        auto_reply_dms=True,
        dm_reply_scope="mutual",
        dry_run=False,
    )
    handled = await agent._reply_dms(FakePage(), settings, [])
    assert handled == 0
    assert browser.opened == 0
    assert browser.sent == []


def test_has_new_message_only_trusts_unread_badge(temp_db):
    agent = replier_mod.AutoReplier(make_runner(FakeBrowser()))
    item = conv(unread=0, preview="在吗")
    assert agent._has_new_message({**item, "unread": 2}) is True
    assert agent._has_new_message(item) is False
    assert agent._has_new_message({**item, "preview": "你在忙吗"}) is False


@pytest.mark.asyncio
async def test_replier_manual_check_queues_when_browser_busy(temp_db):
    db.save_settings(AgentSettings(auto_reply=True, dry_run=True))
    runner = make_runner(FakeBrowser())
    runner.running = True
    agent = replier_mod.AutoReplier(runner)

    result = await agent.check_once(manual=True)
    assert result["queued"] is True
    assert "已排队" in result["detail"]
    assert agent._manual_until > 0

    auto = await agent.check_once()
    assert auto["skipped"] == "run_in_progress"


@pytest.mark.asyncio
async def test_replier_manual_check_queues_while_cycle_busy(temp_db):
    db.save_settings(AgentSettings(auto_reply=True, dry_run=True))
    agent = replier_mod.AutoReplier(make_runner(FakeBrowser()))
    agent.busy = True

    result = await agent.check_once(manual=True)
    assert result["queued"] is True
    assert "正在检查" in result["detail"]
