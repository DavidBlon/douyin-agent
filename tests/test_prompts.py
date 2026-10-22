from __future__ import annotations

from app.config import AgentSettings
from app.prompts import build_messages, build_reply_messages


def test_agent_name_is_injected_into_prompts(temp_db):
    settings = AgentSettings(agent_name="小助手")
    video = {"aweme_id": "v1", "author": "作者", "title": "标题"}
    messages = build_messages(video, settings, [], {}, [])
    assert "小助手" in messages[0]["content"]

    reply = build_reply_messages(
        "dm",
        {"contact": "测试", "relation": "existing", "messages": []},
        settings,
        [],
    )
    assert "小助手" in reply[0]["content"]


def test_agent_name_can_be_empty(temp_db):
    settings = AgentSettings(agent_name="")
    video = {"aweme_id": "v1", "author": "作者", "title": "标题"}
    messages = build_messages(video, settings, [], {}, [])
    assert "角色名字" not in messages[0]["content"]
