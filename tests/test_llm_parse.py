from __future__ import annotations

import json

import pytest

from app.llm import Decision, _extract_json_text, _parse_decision
from app.config import AgentSettings
from app.prompts import build_share_messages

SAMPLE = (
    '<think>We need answer JSON only. Need judge sparse content "有些人不会再遇见", '
    "no author, no metrics/comments. Follow directions tech knowledge society real experience "
    "perhaps maybe emotional. No concrete obs</think>"
    '{"like": false, "comment": "", "dm": "", "reason": "信息太少，无法判断", "confidence": 0.2}'
)


def test_extract_json_behind_think_block():
    body = _extract_json_text(SAMPLE)
    assert json.loads(body)["like"] is False
    decision = _parse_decision(SAMPLE)
    assert decision.comment == ""
    assert decision.confidence == pytest.approx(0.2)


def test_extract_json_behind_unclosed_think_block():
    raw = '{"like": true, "reason": "相关", "confidence": 0.8}'
    assert json.loads(_extract_json_text(raw))["like"] is True

    raw_with_open_tag = (
        '<think>Need judge sparse content，暂不决定。\n'
        '{"like": true, "comment": "这条讲得具体", "reason": "相关", "confidence": 0.7}'
    )
    assert json.loads(_extract_json_text(raw_with_open_tag))["like"] is True


def test_extract_json_from_code_fence():
    raw = '```json\n{"like": false, "comment": "", "dm": "", "reason": "跳过", "confidence": 0.5}\n```'
    decision = _parse_decision(raw)
    assert decision.like is False
    assert decision.reason == "跳过"


def test_parse_decision_rejects_empty_or_non_json():
    with pytest.raises(ValueError):
        _parse_decision("<think>只有思考过程，没有正文")
    with pytest.raises(ValueError):
        _parse_decision("好的，我判断这条不点赞。")


def test_share_messages_include_cover_image():
    settings = AgentSettings(share_topics="美女")
    messages = build_share_messages(
        {"title": "只有文案", "description": "文案里没有关键词", "stats": {}},
        settings,
        "data:image/jpeg;base64,AAAA",
    )
    assert messages[0]["role"] == "system"
    content = messages[1]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[1] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}
    assert "封面" in messages[0]["content"]
