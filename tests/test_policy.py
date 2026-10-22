from __future__ import annotations

from app import db, policy
from app.config import AgentSettings


def video(**overrides):
    data = {
        "aweme_id": "v1",
        "author": "作者甲",
        "author_url": "https://www.douyin.com/user/author1",
        "title": "如何拆解复杂问题",
        "description": "三个真实项目里的取舍，不是空泛鸡汤。",
    }
    data.update(overrides)
    return data


def sent(kind: str, v: dict, content: str = "", target: str = "") -> None:
    action_id = db.add_action(
        kind=kind,
        video_id=str(v.get("aweme_id") or ""),
        target_name=target or str(v.get("author") or ""),
        target_url=str(v.get("author_url") or ""),
        content=content,
        status="sent",
    )
    assert action_id > 0


def test_like_cap_and_duplicate(temp_db):
    s = AgentSettings(likes_per_day=1)
    v = video()
    ok = policy.check_like(v, s)
    assert ok.allowed
    sent("like", v)
    blocked = policy.check_like(v, s)
    assert not blocked.allowed
    assert "点过赞" in blocked.reason


def test_comment_blocks_links_ads_and_duplicates(temp_db):
    s = AgentSettings(comments_per_day=5)
    v = video()
    assert not policy.check_comment(v, "加微信聊", s).allowed
    assert not policy.check_comment(v, "看这个 https://x.com", s).allowed
    assert not policy.check_comment(v, "支持", s).allowed
    ok = policy.check_comment(v, "第三个例子的取舍很真实，尤其是回滚成本那段。", s)
    assert ok.allowed
    sent("comment", v, "第三个例子的取舍很真实，尤其是回滚成本那段。")
    dup = policy.check_comment(v, "另一个角度：如果重来一次还会这样选吗？", s)
    assert not dup.allowed
    assert "评论过" in dup.reason


def test_dm_requires_allowlist_and_cap(temp_db):
    s = AgentSettings(dms_per_day=1, dm_allowlist=["作者甲"])
    v = video()
    ok = policy.check_dm(v, "你提到的回滚成本那段，有没有更具体的数字？", s)
    assert ok.allowed
    sent("dm", v, "你提到的回滚成本那段，有没有更具体的数字？", "作者甲")
    again = policy.check_dm(v, "再追问一句，第三个例子有后续吗？", s)
    assert not again.allowed

    s2 = AgentSettings(dms_per_day=3, dm_allowlist=[])
    assert not policy.check_dm(v, "你好", s2).allowed
    assert "白名单" in policy.check_dm(v, "你好", s2).reason


def test_dm_allows_mutual_contact(temp_db):
    s = AgentSettings(dms_per_day=2, dm_allowlist=[], dm_allow_mutual=True)
    v = video(author_mutual=True)
    ok = policy.check_dm(v, "你上次提到的那个项目后来推进得怎么样？", s)
    assert ok.allowed

    s_off = AgentSettings(dms_per_day=2, dm_allowlist=[], dm_allow_mutual=False)
    blocked = policy.check_dm(v, "你上次提到的那个项目后来推进得怎么样？", s_off)
    assert not blocked.allowed
    assert "互关" in blocked.reason


def test_clean_text_clamps(temp_db):
    s = AgentSettings(max_comment_chars=10)
    body = policy.clean_text("  一二三四五六七八九十一二三  ", s.max_comment_chars)
    assert len(body) == 10


def test_comment_payload_with_mention(temp_db):
    body, content, mention = policy.build_comment_payload(
        "第三个例子的取舍很真实，尤其是回滚成本那段。", "氛围感奶蛙", 90
    )
    assert mention == "氛围感奶蛙"
    assert body.startswith("第三个例子")
    assert content == "@氛围感奶蛙 第三个例子的取舍很真实，尤其是回滚成本那段。"

    # 模型自己带了 @，不要重复
    body2, content2, _ = policy.build_comment_payload(
        "@氛围感奶蛙 这个转场很自然。", "氛围感奶蛙", 90
    )
    assert body2 == "这个转场很自然。"
    assert content2 == "@氛围感奶蛙 这个转场很自然。"

    # 昵称太长、放不下时退回不带 @
    body3, content3, mention3 = policy.build_comment_payload(
        "这段镜头节奏很舒服。", "一个非常非常非常长的接收账号昵称", 20
    )
    assert mention3 == ""
    assert content3 == body3
    assert len(content3) <= 20


def test_dm_auto_reply_scope_and_dedupe(temp_db):
    s_mutual = AgentSettings(auto_reply=True, auto_reply_dms=True, dm_reply_scope="mutual")
    text = "在的，怎么啦？"
    blocked = policy.check_dm_reply("陌生人", "other", False, text, s_mutual)
    assert not blocked.allowed
    assert "互关" in blocked.reason
    ok = policy.check_dm_reply("互关好友", "mutual", False, text, s_mutual)
    assert ok.allowed

    db.add_reply(kind="reply_dm", target_key="dm:test2", incoming="你好", reply=text)
    assert db.reply_exists("reply_dm", "dm:test2", "你好")

    s_existing = AgentSettings(auto_reply=True, auto_reply_dms=True, dm_reply_scope="existing")
    assert policy.check_dm_reply("陌生人", "other", False, text, s_existing).allowed


def test_comment_auto_reply_text_rules(temp_db):
    s = AgentSettings(auto_reply=True, auto_reply_comments=True)
    assert not policy.check_comment_reply("作者甲", "加微信聊", s).allowed
    ok = policy.check_comment_reply("作者甲", "这个补充很有用，感谢。", s)
    assert ok.allowed
    # 不同评论不设次数上限
    assert policy.check_comment_reply("作者甲", "另一个问题想请教一下。", s).allowed


def test_reply_exists_is_scoped(temp_db):
    db.add_reply(kind="reply_dm", target_key="dm:a", incoming="你好", reply="在的")
    assert db.reply_exists("reply_dm", "dm:a", "你好")
    assert not db.reply_exists("reply_dm", "dm:b", "你好")
    assert not db.reply_exists("reply_comment", "dm:a", "你好")
