from __future__ import annotations

import pytest

from app import chat


@pytest.fixture(autouse=True)
def reset_chat_state():
    def reset() -> None:
        chat._CONVERSATIONS.clear()
        chat._UNSEEN.clear()
        chat._VIEW_COOLDOWN.clear()
        chat._VIDEO_CACHE.clear()
        chat.clear_active()

    reset()
    yield
    reset()


def _conv(title: str, unread: int) -> dict:
    return {
        "index": 0,
        "title": title,
        "preview": "最后一条消息",
        "unread": unread,
        "is_group": False,
        "avatar": f"https://example.com/{title}.jpg?v=1",
        "time": "刚刚",
    }


def test_unread_stays_until_viewed(temp_db):
    out = chat.remember_conversations([_conv("测试会话", 2)])
    key = out[0]["key"]
    assert out[0]["unread"] == 2

    # AI 回复后抖音的未读数会清零，但悬浮球提示应保留到用户查看
    out = chat.remember_conversations([_conv("测试会话", 0)])
    assert out[0]["unread"] == 2
    assert chat.cache_summary()["unread_total"] == 2

    chat.mark_read(key)
    assert chat.cache_summary()["unread_total"] == 0

    # 刚查看过的会话，旧未读数不应立刻重新显示红点
    out = chat.remember_conversations([_conv("测试会话", 2)])
    assert out[0]["unread"] == 0

    chat._VIEW_COOLDOWN[key] = 0
    out = chat.remember_conversations([_conv("测试会话", 2)])
    assert out[0]["unread"] == 2


def test_conversation_key_ignores_avatar_query_string():
    left = chat.conversation_key({"avatar": "https://p3.douyinpic.com/a.jpg?x=1", "title": "A"})
    right = chat.conversation_key({"avatar": "https://p3.douyinpic.com/a.jpg?x=2", "title": "A"})
    assert left == right


def test_conversation_key_survives_cdn_host_rotation():
    left = chat.conversation_key({"avatar": "https://p3.douyinpic.com/img/tos/a.jpg", "title": "A"})
    right = chat.conversation_key({"avatar": "https://p9-sign.douyinpic.com/img/tos/a.jpg", "title": "A"})
    assert left == right


class _FakePage:
    async def wait_for_timeout(self, ms):
        return None


class _FakeBrowser:
    def __init__(self, rows, header):
        self._rows = rows
        self._header = header

    async def read_chat_transcript(self, page):
        return list(self._rows)

    async def chat_header_title(self, page):
        return self._header


@pytest.mark.asyncio
async def test_empty_conversation_opens_via_header():
    service = chat.ChatService()
    service.browser = _FakeBrowser([], "杨璨")
    messages, fresh = await service._read_fresh(_FakePage(), {"title": "杨璨", "preview": ""})
    assert fresh is True
    assert messages == []


@pytest.mark.asyncio
async def test_wrong_conversation_still_rejected():
    service = chat.ChatService()
    service.browser = _FakeBrowser([], "别人")
    messages, fresh = await service._read_fresh(_FakePage(), {"title": "杨璨", "preview": ""})
    assert fresh is False
    assert messages == []


def test_self_avatar_filled_for_card_messages():
    chat._SELF_AVATAR = ""
    rows = [
        {"index": 0, "top": 1, "text": "你好", "time": "", "avatar": "https://x/other.jpg", "mine": False},
        {"index": 1, "top": 2, "text": "分享视频", "time": "", "avatar": "", "mine": True},
        {"index": 2, "top": 3, "text": "在的", "time": "", "avatar": "https://x/me.jpg", "mine": True},
    ]
    messages = chat.normalize_transcript(rows, {"avatar": "https://x/other.jpg", "title": "T"})
    assert messages[0]["incoming"] is True
    assert messages[1]["incoming"] is False
    assert messages[1]["avatar"] == "https://x/me.jpg"
    assert messages[2]["avatar"] == "https://x/me.jpg"
