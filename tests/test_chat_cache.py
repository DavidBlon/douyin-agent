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


def test_unread_stays_until_viewed():
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
