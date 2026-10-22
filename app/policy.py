from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import db
from .config import AgentSettings

LINK_RE = re.compile(r"(https?://|www\.|v\.douyin\.com|t\.cn|\.com\b|\.cn\b)", re.I)
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
GENERIC = {"赞", "好看", "支持", "学习了", "谢谢分享", "不错", "厉害", "牛", "666", "哈哈哈"}


@dataclass
class PolicyResult:
    allowed: bool
    reason: str = ""


def clean_text(value: str, max_chars: int) -> str:
    value = (value or "").replace("\u200b", "").replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    return value[:max_chars].strip()


def build_comment_payload(text: str, mention: str, max_chars: int) -> tuple[str, str, str]:
    """把评论正文和开头的 @ 接收账号拼在一起，正文单独返回给策略检查。"""
    mention = (mention or "").strip()
    if mention and len(mention) + 8 > max_chars:
        mention = ""
    raw = (text or "").strip()
    if mention and raw.startswith(f"@{mention}"):
        raw = raw[len(f"@{mention}"):].lstrip(" ：:，,、")
    prefix = f"@{mention} " if mention else ""
    body = clean_text(raw, max(10, max_chars - len(prefix)))
    if prefix and len(prefix) + len(body) > max_chars:
        body = body[: max(0, max_chars - len(prefix))].rstrip()
    content = f"{prefix}{body}".strip()
    return body, content, mention


def _blocked_words(settings: AgentSettings) -> list[str]:
    return [w.strip() for w in settings.comment_blocklist if w and w.strip()]


def _has_blocked(text: str, words: list[str]) -> str:
    lowered = text.lower()
    for word in words:
        if word.lower() in lowered:
            return word
    return ""


def is_allowlisted(author: str, author_url: str, allowlist: list[str]) -> bool:
    a = (author or "").strip().lstrip("@").lower()
    url = (author_url or "").strip().lower()
    for raw in allowlist:
        entry = (raw or "").strip().lstrip("@").lower()
        if not entry:
            continue
        if entry == a or (a and entry in a) or (a and a in entry):
            return True
        if entry.startswith("http") and (entry in url or (url and url.startswith(entry))):
            return True
        if "douyin.com/user/" in entry and url:
            tail = entry.rsplit("/", 1)[-1]
            if tail and tail in url:
                return True
    return False


def check_like(video: dict, settings: AgentSettings) -> PolicyResult:
    if not settings.auto_like:
        return PolicyResult(False, "自动点赞已关闭")
    vid = str(video.get("aweme_id") or "")
    if db.has_action(vid, "like"):
        return PolicyResult(False, "这个视频已经点过赞")
    used = db.count_actions_today("like")
    if used >= settings.likes_per_day:
        return PolicyResult(False, f"今日点赞额度 {settings.likes_per_day} 已用完")
    return PolicyResult(True)


def check_comment(video: dict, text: str, settings: AgentSettings) -> PolicyResult:
    if not settings.auto_comment:
        return PolicyResult(False, "自动评论已关闭")
    body = clean_text(text, settings.max_comment_chars + 1)
    if not body:
        return PolicyResult(False, "评论为空")
    if len(body) > settings.max_comment_chars:
        return PolicyResult(False, f"评论超过 {settings.max_comment_chars} 字")
    if body in GENERIC or len(re.sub(r"[\W_]+", "", body)) < 4:
        return PolicyResult(False, "评论太泛，没有具体内容")
    hit = _has_blocked(body, _blocked_words(settings))
    if hit:
        return PolicyResult(False, f"评论包含受限词「{hit}」")
    if LINK_RE.search(body):
        return PolicyResult(False, "评论里不能放链接")
    if PHONE_RE.search(body):
        return PolicyResult(False, "评论里不能放手机号")
    vid = str(video.get("aweme_id") or "")
    if db.has_action(vid, "comment"):
        return PolicyResult(False, "这个视频已经评论过")
    if db.comment_text_exists(body):
        return PolicyResult(False, "这条评论内容之前已经用过")
    used = db.count_actions_today("comment")
    if used >= settings.comments_per_day:
        return PolicyResult(False, f"今日评论额度 {settings.comments_per_day} 已用完")
    return PolicyResult(True)


def check_dm(video: dict, text: str, settings: AgentSettings) -> PolicyResult:
    if not settings.auto_dm:
        return PolicyResult(False, "自动私信已关闭")
    author = str(video.get("author") or "").strip()
    author_url = str(video.get("author_url") or "").strip()
    if not author and not author_url:
        return PolicyResult(False, "找不到私信对象")
    allowlisted = is_allowlisted(author, author_url, settings.dm_allowlist)
    mutual = bool(video.get("author_mutual"))
    if not allowlisted and not (settings.dm_allow_mutual and mutual):
        return PolicyResult(False, "对方不在私信白名单，也不是互关账号")
    body = clean_text(text, settings.max_dm_chars + 1)
    if not body:
        return PolicyResult(False, "私信为空")
    if len(body) > settings.max_dm_chars:
        return PolicyResult(False, f"私信超过 {settings.max_dm_chars} 字")
    hit = _has_blocked(body, _blocked_words(settings))
    if hit:
        return PolicyResult(False, f"私信包含受限词「{hit}」")
    if LINK_RE.search(body):
        return PolicyResult(False, "私信里不能放链接")
    if PHONE_RE.search(body):
        return PolicyResult(False, "私信里不能放手机号")
    vid = str(video.get("aweme_id") or "")
    if db.has_action(vid, "dm"):
        return PolicyResult(False, "这个视频已经私信过")
    if db.count_dm_today(author) >= settings.dm_per_contact_per_day:
        return PolicyResult(False, f"今天已经私信过「{author}」")
    used = db.count_actions_today("dm")
    if used >= settings.dms_per_day:
        return PolicyResult(False, f"今日私信额度 {settings.dms_per_day} 已用完")
    return PolicyResult(True)


def _check_reply_text(text: str, max_chars: int, min_chars: int, settings: AgentSettings) -> PolicyResult:
    body = clean_text(text, max_chars + 1)
    if not body:
        return PolicyResult(False, "回复为空")
    if len(body) > max_chars:
        return PolicyResult(False, f"回复超过 {max_chars} 字")
    if len(re.sub(r"[\W_]+", "", body)) < min_chars:
        return PolicyResult(False, "回复太短，没有实际内容")
    hit = _has_blocked(body, _blocked_words(settings))
    if hit:
        return PolicyResult(False, f"回复包含受限词「{hit}」")
    if LINK_RE.search(body):
        return PolicyResult(False, "回复里不能放链接")
    if PHONE_RE.search(body):
        return PolicyResult(False, "回复里不能放手机号")
    return PolicyResult(True)


def check_dm_reply(
    contact: str,
    relation: str,
    allowlisted: bool,
    text: str,
    settings: AgentSettings,
) -> PolicyResult:
    if not settings.auto_reply:
        return PolicyResult(False, "自动回复未开启")
    if not settings.auto_reply_dms:
        return PolicyResult(False, "私信自动回复已关闭")
    scope = settings.dm_reply_scope
    if scope == "allowlist" and not allowlisted:
        return PolicyResult(False, "对方不在私信白名单")
    if scope == "mutual" and not allowlisted and relation != "mutual":
        return PolicyResult(False, "对方既不在白名单，也不是互关账号")
    return _check_reply_text(text, settings.max_dm_chars, 2, settings)


def check_comment_reply(
    author: str,
    text: str,
    settings: AgentSettings,
) -> PolicyResult:
    if not settings.auto_reply:
        return PolicyResult(False, "自动回复未开启")
    if not settings.auto_reply_comments:
        return PolicyResult(False, "评论自动回复已关闭")
    return _check_reply_text(text, settings.max_comment_chars, 2, settings)


def check_publish(caption: str, media_path: str, settings: AgentSettings) -> PolicyResult:
    if not settings.auto_publish:
        return PolicyResult(False, "自动发布已关闭")
    if not media_path or not Path(media_path).exists():
        return PolicyResult(False, "没有找到发布素材")
    body = clean_text(caption, 500)
    if not body:
        return PolicyResult(False, "发布文案为空")
    hit = _has_blocked(body, _blocked_words(settings))
    if hit:
        return PolicyResult(False, f"发布文案包含受限词「{hit}」")
    if LINK_RE.search(body):
        return PolicyResult(False, "发布文案里不能放链接")
    used = db.count_actions_today("publish")
    if used >= settings.posts_per_day:
        return PolicyResult(False, f"今日发布额度 {settings.posts_per_day} 已用完")
    return PolicyResult(True)
