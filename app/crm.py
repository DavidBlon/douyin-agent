from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlparse

from . import db
from .config import load_api_key
from .events import bus
from .llm import DeepSeekDecider, LLMError

STAGES = ("new", "chatting", "interested", "cooperation", "customer", "negative", "archived")
_STAGE_RANK = {
    "new": 0,
    "chatting": 1,
    "interested": 2,
    "cooperation": 3,
    "customer": 4,
    "negative": 3,
    "archived": 0,
}


def _base_url(value: str) -> str:
    return (value or "").split("?", 1)[0]


def contact_key(avatar: str, name: str = "") -> str:
    seed = _base_url(avatar) or (name or "").strip()
    return "dm:" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20]


def _heuristic(text: str) -> tuple[list[str], str, int, str]:
    body = (text or "").strip()
    tags: list[str] = []
    stage = "chatting"
    score = 30
    note = ""

    def has(*words: str) -> bool:
        return any(word in body for word in words)

    if has("合作", "商务", "报价", "推广", "品牌方", "广告合作"):
        tags.append("合作")
        stage = "cooperation"
        score = max(score, 80)
        note = "对方提到合作/商务，建议人工确认需求与报价。"
    if has("多少钱", "怎么买", "购买", "下单", "价格", "怎么卖", "链接"):
        tags.append("购买意向")
        stage = "interested"
        score = max(score, 75)
        note = note or "对方有购买/获取意向。"
    if has("找个", "找一下", "分享", "视频", "作品", "素材"):
        tags.append("内容请求")
        score = max(score, 55)
        note = note or "对方在请求内容/视频。"
    if has("投诉", "骗", "垃圾", "无语", "差评", "举报", "退款"):
        tags.append("负面")
        stage = "negative"
        score = min(score, 20)
        note = note or "对方情绪偏负面，建议人工优先处理。"
    if has("微信", "加v", "加V", "二维码", "手机号", "电话", "qq", "QQ"):
        tags.append("引流风险")
        note = note or "对方在要站外联系方式，注意平台规则。"
    if has("喜欢", "关注", "支持", "好看", "不错"):
        tags.append("粉丝")
        score = max(score, 45)
    if has("你好", "在吗", "嗨", "哈喽", "早上好", "晚上好"):
        tags.append("闲聊")
        score = max(score, 35)
    if not tags:
        tags.append("闲聊")
    return list(dict.fromkeys(tags)), stage, score, note


def _avatar_path(value: str) -> str:
    try:
        return urlparse(value or "").path
    except ValueError:
        return ""


def sync_conversations(conversations: list[dict[str, Any]]) -> int:
    added = 0
    pairs: list[tuple[str, str, str]] = []
    current_keys: set[str] = set()
    for conv in conversations:
        if conv.get("is_group"):
            continue
        name = str(conv.get("title") or "").strip()
        avatar = str(conv.get("avatar") or "").strip()
        if not name:
            continue
        key = contact_key(avatar, name)
        pairs.append((name, avatar, key))
        current_keys.add(key)

    for name, avatar, key in pairs:
        existing = db.get_contact(key)
        if existing:
            if existing.get("avatar") != avatar or existing.get("name") != name:
                db.upsert_contact(key, name=name, avatar=avatar, seen=False)
        else:
            db.upsert_contact(key, name=name, avatar=avatar, seen=False)
            added += 1
        path = _avatar_path(avatar)
        if not path:
            continue
        current = db.get_contact(key) or {}
        for old in db.list_contacts(limit=500):
            old_key = str(old.get("target_key") or "")
            if old_key == key or old_key in current_keys:
                continue
            if _avatar_path(str(old.get("avatar") or "")) != path:
                continue
            db.upsert_contact(
                key,
                last_incoming=str(current.get("last_incoming") or old.get("last_incoming") or ""),
                last_reply=str(current.get("last_reply") or old.get("last_reply") or ""),
                seen=False,
            )
            db.update_contact(
                key,
                tags=list(dict.fromkeys([*(current.get("tags") or []), *(old.get("tags") or [])])),
                stage=(
                    str(current.get("stage") or "new")
                    if str(current.get("stage") or "new") not in ("new", "chatting")
                    else str(old.get("stage") or "new")
                ),
                note=str(current.get("note") or old.get("note") or ""),
                score=max(int(current.get("score") or 0), int(old.get("score") or 0)),
            )
            db.delete_contact(old_key)
            current = db.get_contact(key) or current
    return added


def note_incoming(key: str, name: str, avatar: str, text: str) -> dict[str, Any] | None:
    if not key or not (text or "").strip():
        return None
    db.upsert_contact(key, name=name, avatar=avatar, last_incoming=text[:1000])
    contact = db.get_contact(key)
    if not contact:
        return None
    settings = db.load_settings()
    if not settings.crm_auto_tag:
        return contact
    tags, stage, score, note = _heuristic(text)
    current_tags = list(contact.get("tags") or [])
    merged = list(dict.fromkeys([*current_tags, *tags]))
    current_stage = str(contact.get("stage") or "new")
    next_stage = stage if _STAGE_RANK.get(stage, 0) >= _STAGE_RANK.get(current_stage, 0) else current_stage
    if current_stage in ("customer", "archived"):
        next_stage = current_stage
    next_note = str(contact.get("note") or "").strip() or note
    return db.update_contact(
        key,
        tags=merged,
        stage=next_stage,
        score=max(int(contact.get("score") or 0), score),
        note=next_note,
    )


def note_reply(key: str, text: str) -> None:
    if not key:
        return
    db.upsert_contact(key, last_reply=(text or "")[:1000])


async def analyze_contact(key: str) -> dict[str, Any]:
    contact = db.get_contact(key)
    if not contact:
        raise ValueError("联系人不存在")
    if not load_api_key():
        raise LLMError("还没有配置 API Key")
    decider = DeepSeekDecider(db.load_settings())
    insight, usage = await decider.analyze_contact(contact)
    tags = list(dict.fromkeys([*(contact.get("tags") or []), *[t for t in insight.tags if t]]))
    stage = insight.stage if insight.stage in STAGES else "chatting"
    updated = db.update_contact(
        key,
        tags=tags,
        stage=stage,
        score=max(0, min(100, int(insight.score))),
        note=insight.note.strip() or contact.get("note") or "",
    )
    await bus.emit(
        "crm",
        f"已分析联系人：{contact.get('name')}",
        {"keys": key, "tags": tags, "stage": stage, "usage": usage},
    )
    return {"contact": updated, "usage": usage}


def overview() -> dict[str, Any]:
    return db.crm_overview()
