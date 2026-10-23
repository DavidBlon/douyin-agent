from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import compliance, content, crm, db
from app.agent import AgentRunner
from app.analytics import parse_metric
from app.config import AgentSettings
from app import video as video_mod


def test_parse_metric_units():
    assert parse_metric("30") == 30
    assert parse_metric("1.2万") == 12000
    assert parse_metric("2.5w") == 25000
    assert parse_metric("1亿") == 100000000
    assert parse_metric("") == 0


def test_compliance_does_not_block_ordinary_words():
    assert not compliance.check_text("最后检查三件事，第一步先整理")["blocking"]
    assert not compliance.check_text("最近在学习 AI 写作")["blocking"]


def test_compliance_blocks_claims_and_contact():
    assert any(x["code"] == "absolute" for x in compliance.check_text("这是全网最好用的工具")["blocking"])
    assert any(x["code"] == "absolute" for x in compliance.check_text("行业第一品牌")["blocking"])
    assert any(x["code"] == "contact" for x in compliance.check_text("加微信聊")["blocking"])
    assert any(x["code"] == "phone" for x in compliance.check_text("电话 13800138000")["blocking"])


def test_works_and_analytics_overview(temp_db):
    db.upsert_work(
        {"aweme_id": "w1", "title": "第一条", "cover": "c", "url": "u", "kind": "video"}
    )
    db.save_work_stats("w1", {"plays": 30, "likes": 2, "comments": 1, "shares": 0})
    db.save_account_stats({"followers": 12, "following": 3, "likes": 2, "works": 1})
    data = db.analytics_overview()
    assert data["account"]["followers"] == 12
    assert data["totals"]["plays"] == 30
    assert data["totals"]["likes"] == 2
    assert data["work_count"] == 1
    assert data["works"][0]["aweme_id"] == "w1"
    assert db.work_history("w1")[0]["plays"] == 30
    assert db.latest_work_stats("w1")["likes"] == 2


def test_content_plan_lifecycle(temp_db):
    plan_id = db.create_content_plan(
        {
            "plan_date": "2026-10-24",
            "topic": "测试选题",
            "caption": "最后检查一次，第一步先整理。",
            "hashtags": ["测试"],
            "status": "draft",
        }
    )
    updated = content.update_plan(plan_id, {"cover_text": "测试封面", "hashtags": ["A", "B"]})
    assert updated and updated["cover_text"] == "测试封面"
    assert updated["hashtags"] == ["A", "B"]
    assert db.list_content_plans() [0]["id"] == plan_id


@pytest.mark.asyncio
async def test_content_approval_and_due_queue(temp_db):
    plan_id = db.create_content_plan(
        {
            "plan_date": "2026-10-24",
            "topic": "测试选题",
            "caption": "最后检查一次，第一步先整理。",
            "publish_at": "2020-01-01T00:00",
            "status": "review",
        }
    )
    plan = await content.approve_plan(plan_id)
    assert plan["status"] == "approved"
    due = db.due_content_plans("2026-10-24T00:00")
    assert any(p["id"] == plan_id for p in due)


@pytest.mark.asyncio
async def test_content_approval_blocks_bad_claim(temp_db):
    plan_id = db.create_content_plan(
        {
            "plan_date": "2026-10-24",
            "topic": "保证稳赚",
            "caption": "加微信，100% 有效。",
            "status": "review",
        }
    )
    plan = await content.approve_plan(plan_id)
    assert plan["status"] == "needs_review"
    assert plan["compliance"]["blocking"]


def test_crm_sync_and_heuristics(temp_db):
    db.save_settings(AgentSettings(crm_auto_tag=True))
    avatar = "https://img.example/a.jpg?x=1"
    added = crm.sync_conversations(
        [
            {"title": "合作方", "avatar": avatar, "is_group": False},
            {"title": "群聊", "avatar": "https://img.example/g.jpg", "is_group": True},
        ]
    )
    assert added == 1
    key = crm.contact_key(avatar, "合作方")
    contact = crm.note_incoming(key, "合作方", avatar, "想和你合作，报价多少？")
    assert contact is not None
    assert "合作" in contact["tags"]
    assert contact["stage"] == "cooperation"
    crm.note_reply(key, "我看看时间")
    assert db.get_contact(key)["last_reply"] == "我看看时间"
    updated = db.update_contact(key, tags=["合作", "重点"], stage="customer", note="已报价")
    assert updated["stage"] == "customer"
    assert updated["tags"] == ["合作", "重点"]


def test_crm_merges_duplicate_avatar_paths(temp_db):
    old_key = crm.contact_key("https://old-cdn.example/a.jpg", "合作方")
    db.upsert_contact(old_key, name="合作方", avatar="https://old-cdn.example/a.jpg")
    db.update_contact(old_key, tags=["老标签"], stage="interested", note="旧备注")
    new_key = crm.contact_key("https://new-cdn.example/a.jpg", "合作方")
    crm.sync_conversations(
        [{"title": "合作方", "avatar": "https://new-cdn.example/a.jpg", "is_group": False}]
    )
    contacts = db.list_contacts()
    assert len([c for c in contacts if c["name"] == "合作方"]) == 1
    merged = db.get_contact(new_key)
    assert merged is not None
    assert "老标签" in merged["tags"]
    assert merged["note"] == "旧备注"


def test_video_wrap_keeps_punctuation_off_line_start():
    wrapped = video_mod._wrap("一二三四五六七八九十，十一十二", width=10)
    for line in wrapped.splitlines():
        assert not line.startswith("，")
    assert len(video_mod.split_script("第一句。第二句！第三句？第四句。", 2)) == 2


@pytest.mark.asyncio
async def test_comment_mention_sends_body_once(temp_db):
    db.save_settings(
        AgentSettings(dry_run=False, share_target="目标", comment_mention=True)
    )
    calls: list[tuple[str, str]] = []

    class FakeBrowser:
        async def comment(self, text: str, mention: str = ""):
            calls.append((text, mention))
            return {"status": "sent", "detail": "ok", "screenshot": ""}

    runner = object.__new__(AgentRunner)
    runner.browser = FakeBrowser()
    runner.settings = db.load_settings()
    status = await runner._act(
        "comment",
        {"aweme_id": "v1", "author": "作者", "author_url": ""},
        "@目标 正文",
        mention="目标",
        send_text="正文",
    )
    assert status == "sent"
    assert calls == [("正文", "目标")]
