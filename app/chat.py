from __future__ import annotations

import asyncio
import hashlib
import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Page

from . import crm, db
from .agent import runner
from .browser import ActionError, BrowserError, LoginRequired, RiskControl
from .events import bus

INBOX_URL = "https://www.douyin.com/user/self"
CACHE_FRESH_SEC = 35.0
CHAT_IDLE_SEC = 180.0
MONITOR_INTERVAL = 30.0
VIEW_COOLDOWN_SEC = 25.0

_CONVERSATIONS: list[dict[str, Any]] = []
_UPDATED_AT = ""
_UPDATED_MONO = 0.0
_LAST_ERROR = ""
_UNSEEN: dict[str, int] = {}
_VIEW_COOLDOWN: dict[str, float] = {}
_ACTIVE_KEY = ""
_VIDEO_CACHE: dict[str, tuple[float, str]] = {}


class ChatBusy(RuntimeError):
    pass


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _base_url(value: str) -> str:
    return (value or "").split("?", 1)[0].strip()


def _is_http(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def _to_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except Exception:
        return 0


def _avatar_seed(value: str) -> str:
    """头像 CDN 域名会轮换，只用 path 作为稳定种子，避免会话 key 漂移。"""
    url = _base_url(value)
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return parsed.path or url
    return url


def conversation_key(raw: dict[str, Any]) -> str:
    seed = _avatar_seed(str(raw.get("avatar") or "")) or str(raw.get("title") or "")
    return "dm:" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20]


def _public_conversation(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": item.get("key", ""),
        "index": item.get("index", 0),
        "title": item.get("title", ""),
        "preview": item.get("preview", ""),
        "unread": _to_int(item.get("unread")),
        "is_group": bool(item.get("is_group")),
        "avatar": item.get("avatar", ""),
        "time": item.get("time", ""),
    }


def remember_conversations(raws: list[dict[str, Any]]) -> list[dict[str, Any]]:
    global _UPDATED_AT, _UPDATED_MONO, _LAST_ERROR
    now = time.monotonic()
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(raws):
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        key = conversation_key(raw)
        raw_unread = _to_int(raw.get("unread"))
        if raw_unread > 0 and key != _ACTIVE_KEY:
            if key in _UNSEEN:
                _UNSEEN[key] = max(_UNSEEN[key], raw_unread)
            elif now >= _VIEW_COOLDOWN.get(key, 0.0):
                _UNSEEN[key] = raw_unread
        if key == _ACTIVE_KEY:
            _UNSEEN.pop(key, None)
            display_unread = 0
        else:
            display_unread = _UNSEEN.get(key, 0)
        out.append(
            {
                "key": key,
                "index": int(raw.get("index") if raw.get("index") is not None else i),
                "title": title,
                "preview": str(raw.get("preview") or "")[:120],
                "unread": display_unread,
                "is_group": bool(raw.get("is_group")),
                "avatar": str(raw.get("avatar") or ""),
                "time": str(raw.get("time") or "")[:24],
            }
        )
    _CONVERSATIONS[:] = out
    _UPDATED_AT = _iso_now()
    _UPDATED_MONO = now
    _LAST_ERROR = ""
    try:
        crm.sync_conversations(out)
    except Exception:
        pass
    return out


def cached_conversations() -> list[dict[str, Any]]:
    return [_public_conversation(item) for item in _CONVERSATIONS]


def cache_summary() -> dict[str, Any]:
    return {
        "ok": True,
        "unread_total": sum(_to_int(item.get("unread")) for item in _CONVERSATIONS),
        "conversation_count": len(_CONVERSATIONS),
        "updated_at": _UPDATED_AT,
        "age_sec": round(time.monotonic() - _UPDATED_MONO, 1) if _UPDATED_MONO else None,
        "error": _LAST_ERROR,
        "page_open": bool(_ACTIVE_KEY),
    }


def mark_read(key: str) -> None:
    if not key:
        return
    _UNSEEN.pop(key, None)
    _VIEW_COOLDOWN[key] = time.monotonic() + VIEW_COOLDOWN_SEC
    for item in _CONVERSATIONS:
        if item.get("key") == key:
            item["unread"] = 0


def set_active(key: str) -> None:
    global _ACTIVE_KEY
    _ACTIVE_KEY = key
    mark_read(key)


def clear_active() -> None:
    global _ACTIVE_KEY
    _ACTIVE_KEY = ""


def _normalize_media(raw: dict[str, Any]) -> list[dict[str, Any]]:
    images = []
    for item in raw.get("images") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if not _is_http(url):
            continue
        images.append(
            {
                "url": url,
                "width": _to_int(item.get("w")),
                "height": _to_int(item.get("h")),
            }
        )

    videos = [v for v in (raw.get("videos") or []) if isinstance(v, dict)]
    video_src = next((str(v.get("src") or "") for v in videos if _is_http(v.get("src"))), "")
    poster = next((str(v.get("poster") or "") for v in videos if _is_http(v.get("poster"))), "")
    page_url = str(raw.get("video_link") or "")
    is_video = bool(raw.get("is_video")) or bool(video_src) or _is_http(page_url) and "/video/" in page_url

    media: list[dict[str, Any]] = []
    if is_video:
        cover_item = None
        if images:
            cover_item = max(images, key=lambda x: int(x["width"]) * int(x["height"]))
        cover = str(cover_item["url"]) if cover_item else poster
        if cover_item:
            images = [item for item in images if item["url"] != cover]
        media.append(
            {
                "kind": "video",
                "url": page_url if _is_http(page_url) else "",
                "video_url": video_src if _is_http(video_src) else "",
                "cover": cover,
                "width": _to_int(cover_item.get("width")) if cover_item else _to_int(videos[0].get("w")) if videos else 0,
                "height": _to_int(cover_item.get("height")) if cover_item else _to_int(videos[0].get("h")) if videos else 0,
                "duration": _to_int(videos[0].get("duration")) if videos else 0,
            }
        )

    for item in images:
        width = _to_int(item.get("width"))
        height = _to_int(item.get("height"))
        media.append(
            {
                "kind": "emoji" if max(width, height) <= 48 else "image",
                "url": item["url"],
                "width": width,
                "height": height,
            }
        )
    return media


def _best_video_url(hits: list[dict[str, Any]]) -> str:
    candidates: list[tuple[int, int, str]] = []
    for hit in hits:
        url = str(hit.get("url") or "")
        content_type = str(hit.get("content_type") or "")
        if not _is_http(url):
            continue
        if "video/mp4" not in content_type and "mime_type=video_mp4" not in url:
            continue
        if "media-audio" in url or "audio-und" in url:
            continue
        match = re.search(r"[?&]br=(\d+)", url)
        bitrate = int(match.group(1)) if match else 0
        compatible = 0 if "hvc1" in url else 1
        candidates.append((compatible, bitrate, url))
    if not candidates:
        return ""
    candidates.sort(reverse=True)
    return candidates[0][2]


def normalize_transcript(
    raws: list[dict[str, Any]],
    conversation: dict[str, Any],
) -> list[dict[str, Any]]:
    ordered = sorted(
        [row for row in raws if isinstance(row, dict)],
        key=lambda row: (
            row.get("top") if isinstance(row.get("top"), (int, float)) else 10**9,
            row.get("index") if isinstance(row.get("index"), int) else 10**9,
        ),
    )
    contact_avatar = _base_url(str(conversation.get("avatar") or ""))
    is_group = bool(conversation.get("is_group"))
    messages: list[dict[str, Any]] = []
    for index, raw in enumerate(ordered):
        text = str(raw.get("text") or "").strip()
        media = _normalize_media(raw)
        if not text and not media:
            continue
        avatar = str(raw.get("avatar") or "")
        is_system = bool(raw.get("is_system"))
        if is_system:
            incoming = False
        elif is_group:
            incoming = not bool(raw.get("mine"))
        elif avatar and contact_avatar:
            incoming = _base_url(avatar) == contact_avatar
        else:
            incoming = not bool(raw.get("mine"))
        seed = "|".join(
            [
                text,
                str(raw.get("time") or ""),
                ",".join(str(item.get("url") or "") for item in media),
                str(raw.get("index") if raw.get("index") is not None else index),
            ]
        )
        messages.append(
            {
                "id": hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16],
                "text": text,
                "time": str(raw.get("time") or "")[:24],
                "incoming": incoming,
                "avatar": avatar,
                "media": media,
                "system": is_system,
            }
        )
    return _fill_self_avatar(messages[-80:])


_SELF_AVATAR = ""


def _fill_self_avatar(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """分享卡片等消息可能读不到头像，用同批里自己消息的头像补齐，避免显示占位字。"""
    global _SELF_AVATAR
    for item in messages:
        if not item.get("incoming") and not item.get("system") and item.get("avatar"):
            _SELF_AVATAR = str(item["avatar"])
            break
    if _SELF_AVATAR:
        for item in messages:
            if not item.get("incoming") and not item.get("system") and not item.get("avatar"):
                item["avatar"] = _SELF_AVATAR
    return messages


def _conversation_is_fresh(messages: list[dict[str, Any]], target: dict[str, Any]) -> bool:
    if not messages:
        return False
    newest = messages[-1]
    if newest.get("incoming"):
        return True
    text = str(newest.get("text") or "").strip()
    if newest.get("media") and not text:
        return True
    preview = str(target.get("preview") or "").strip()
    return bool(text and preview and (text in preview or preview in text))


class ChatService:
    def __init__(self) -> None:
        self.runner = runner
        self.browser = runner.browser
        self._page: Page | None = None
        self._current_key = ""
        self._last_activity = 0.0
        self._needs_fresh_page = False
        self._op_lock = asyncio.Lock()
        self._monitor: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._monitor and not self._monitor.done():
            return
        self._stop = asyncio.Event()
        self._monitor = asyncio.create_task(self._monitor_loop())
        asyncio.create_task(self._prewarm())

    async def _prewarm(self) -> None:
        """启动后提前把收件箱页拉起来，避免用户第一次打开会话时冷启动等待。"""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=8)
            return
        except asyncio.TimeoutError:
            pass
        async with self._op_lock:
            try:
                page = await self._ensure_page()
                if page is not None:
                    await self._list_raw(page)
            except Exception:
                pass

    async def stop(self) -> None:
        self._stop.set()
        task = self._monitor
        self._monitor = None
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await self.close()

    async def _monitor_loop(self) -> None:
        await asyncio.sleep(12)
        while not self._stop.is_set():
            try:
                if self._page and time.monotonic() - self._last_activity > CHAT_IDLE_SEC:
                    await self.close()
                if self.browser.context and not self.runner.running and not self.runner.exclusive.locked():
                    if time.monotonic() - _UPDATED_MONO > CACHE_FRESH_SEC + 10:
                        await self._background_refresh()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                global _LAST_ERROR
                _LAST_ERROR = str(exc)[:200]
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=MONITOR_INTERVAL)
            except asyncio.TimeoutError:
                pass

    async def _background_refresh(self) -> None:
        if self.browser.context is None or self._page is None or self._page.is_closed():
            return
        async with self._op_lock:
            page = self._page
            if page is None or page.is_closed():
                return
            raw = await self.browser.list_conversations(page)
            if raw:
                remember_conversations(raw)

    async def _acquire_browser(self) -> bool:
        self._last_activity = time.monotonic()
        if self.browser.context is not None:
            return False
        try:
            await asyncio.wait_for(self.runner.exclusive.acquire(), timeout=20)
        except asyncio.TimeoutError as exc:
            raise ChatBusy("浏览器正在启动，稍后再试") from exc
        return True

    def _release_browser(self, held: bool) -> None:
        if held:
            self.runner.exclusive.release()

    async def _ensure_page(self) -> Page | None:
        if self._page and not self._page.is_closed():
            try:
                await self._page.evaluate("1")
                if "douyin.com" in self._page.url:
                    return self._page
            except Exception:
                pass
            await self._close_page()
        await self.browser.ensure_started()
        if not await self.browser.is_logged_in():
            raise LoginRequired("浏览器还没有登录抖音")
        page = await self.browser.new_page(track=False)
        page.set_default_timeout(20000)
        await page.goto(INBOX_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1500)
        try:
            await page.evaluate(
                "() => document.querySelectorAll('video').forEach(v => { v.muted = true; if (v.pause) v.pause(); })"
            )
        except Exception:
            pass
        self._page = page
        self._current_key = ""
        clear_active()
        self._last_activity = time.monotonic()
        return page

    async def _close_page(self) -> None:
        page = self._page
        self._page = None
        self._current_key = ""
        self._needs_fresh_page = False
        clear_active()
        if page is not None and not page.is_closed():
            try:
                await page.close()
            except Exception:
                pass

    async def _dismiss_overlays(self, page: Page) -> None:
        for _ in range(2):
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            await page.wait_for_timeout(160)
        try:
            await page.mouse.move(6, 6)
        except Exception:
            pass
        await page.wait_for_timeout(150)

    async def close(self) -> None:
        async with self._op_lock:
            await self._close_page()

    async def leave(self) -> None:
        clear_active()

    async def _list_raw(self, page: Page) -> list[dict[str, Any]]:
        raw = await self.browser.list_conversations(page)
        if not raw:
            await page.wait_for_timeout(900)
            raw = await self.browser.list_conversations(page)
        if not raw:
            raise ActionError("没有读到私信会话列表，可能收件箱没有打开")
        remember_conversations(raw)
        self._last_activity = time.monotonic()
        return raw

    async def live_conversations(self) -> dict[str, Any]:
        async with self._op_lock:
            held = await self._acquire_browser()
            try:
                page = await self._ensure_page()
                if page is None:
                    raise ActionError("浏览器页面不可用")
                await self._list_raw(page)
            finally:
                self._release_browser(held)
        return {
            "ok": True,
            "conversations": cached_conversations(),
            **cache_summary(),
            "cached": False,
        }

    async def conversations(self, refresh: bool = False) -> dict[str, Any]:
        if not refresh and _CONVERSATIONS:
            return {
                "ok": True,
                "conversations": cached_conversations(),
                **cache_summary(),
                "cached": True,
            }
        if not refresh and self._page is None and self.browser.context is None:
            return {
                "ok": True,
                "conversations": cached_conversations(),
                **cache_summary(),
                "cached": True,
            }
        try:
            return await self.live_conversations()
        except Exception:
            if _CONVERSATIONS:
                return {
                    "ok": True,
                    "conversations": cached_conversations(),
                    **cache_summary(),
                    "cached": True,
                }
            raise

    async def _find(self, key: str) -> dict[str, Any] | None:
        for item in _CONVERSATIONS:
            if item.get("key") == key:
                return item
        return None

    async def _read_fresh(
        self, page: Page, target: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], bool]:
        title = str(target.get("title") or "")
        messages: list[dict[str, Any]] = []
        await page.wait_for_timeout(400)
        for attempt in range(6):
            rows = await self.browser.read_chat_transcript(page)
            messages = normalize_transcript(rows, target)
            if _conversation_is_fresh(messages, target):
                return messages, True
            # 页头标题已经能证明停在正确会话上；内容校验只用于检测“页面还没切过来”。
            header = await self.browser.chat_header_title(page)
            if header and (header == title or title in header):
                return messages, True
            if attempt < 5:
                await page.wait_for_timeout(500)
        return messages, False

    async def _open_target(
        self, page: Page, target: dict[str, Any]
    ) -> list[dict[str, Any]]:
        title = str(target.get("title") or "")
        opened = await self.browser.open_conversation_by_title(page, title)
        if not opened:
            opened = await self.browser.open_conversation(page, int(target.get("index") or 0))
            if opened:
                header = await self.browser.chat_header_title(page)
                opened = bool(header and (header == title or title in header))
        if not opened:
            raise ActionError("打开会话失败")
        messages, fresh = await self._read_fresh(page, target)
        if not fresh:
            header = await self.browser.chat_header_title(page)
            if not (header and (header == title or title in header)):
                raise ActionError("会话内容没有切换，请重试")
        return messages

    async def _open(self, key: str) -> tuple[dict[str, Any], list[dict[str, Any]], Page]:
        if self._needs_fresh_page:
            await self._close_page()
            self._needs_fresh_page = False
        page = await self._ensure_page()
        if page is None:
            raise ActionError("浏览器页面不可用")
        if self._current_key != key:
            await self._dismiss_overlays(page)
            await self._list_raw(page)
        target = await self._find(key)
        if target is None:
            await self._list_raw(page)
            target = await self._find(key)
        if target is None:
            raise ActionError("会话不存在或列表已更新")
        if self._current_key == key:
            set_active(key)
            messages, fresh = await self._read_fresh(page, target)
            if fresh:
                self._last_activity = time.monotonic()
                return target, messages, page
            # 页面还停在别的会话上：丢掉这张页，换干净页重开。
            await self._close_page()
            page = await self._ensure_page()
            if page is None:
                raise ActionError("浏览器页面不可用")
            await self._list_raw(page)
            target = await self._find(key) or target
        try:
            messages = await self._open_target(page, target)
        except Exception:
            self._needs_fresh_page = True
            raise
        self._current_key = key
        set_active(key)
        self._last_activity = time.monotonic()
        return target, messages, page

    async def open_chat(self, key: str) -> dict[str, Any]:
        async with self._op_lock:
            held = await self._acquire_browser()
            try:
                target, messages, _page = await self._open(key)
            finally:
                self._release_browser(held)
        return {
            "ok": True,
            "conversation": _public_conversation(target),
            "messages": messages,
        }

    async def messages(self, key: str) -> dict[str, Any]:
        if self._page is None or self._page.is_closed() or self._current_key != key:
            return await self.open_chat(key)
        async with self._op_lock:
            held = await self._acquire_browser()
            try:
                page = self._page
                if page is None or page.is_closed():
                    raise ActionError("聊天页面已关闭")
                target = await self._find(key)
                if target is None:
                    target, messages, _page = await self._open(key)
                else:
                    set_active(key)
                    messages, fresh = await self._read_fresh(page, target)
                    if not fresh:
                        target, messages, _page = await self._open(key)
                    self._last_activity = time.monotonic()
            finally:
                self._release_browser(held)
        return {
            "ok": True,
            "conversation": _public_conversation(target),
            "messages": messages,
        }

    async def send(self, key: str, text: str) -> dict[str, Any]:
        text = text.strip()
        if not text:
            raise ActionError("消息不能为空")
        if len(text) > 500:
            raise ActionError("消息太长，最多 500 个字符")
        async with self._op_lock:
            held = await self._acquire_browser()
            try:
                target, messages, page = await self._open(key)
                if target.get("is_group"):
                    raise ActionError("群聊暂不支持在这里发送")
                result = await self.browser.send_chat_message(page, text)
                await page.wait_for_timeout(600)
                rows = await self.browser.read_chat_transcript(page)
                messages = normalize_transcript(rows, target)
                set_active(key)
                target["preview"] = text[:120]
                target["time"] = datetime.now().strftime("%H:%M")
                target["unread"] = 0
            finally:
                self._release_browser(held)
        status = str(result.get("status") or "uncertain")
        action_id = db.add_action(
            kind="dm",
            video_id="",
            target_name=str(target.get("title") or ""),
            target_url="",
            content=text,
            status=status,
            policy="手动发送",
            dry_run=False,
        )
        db.update_action(action_id, result=str(result.get("detail") or "")[:400], executed_at=db.now_iso())
        try:
            crm.note_reply(str(target.get("key") or conversation_key(target)), text)
        except Exception:
            pass
        await bus.emit(
            "chat",
            f"已发送私信给 {target.get('title') or '对方'}",
            {
                "target": target.get("title") or "",
                "text": text[:200],
                "status": status,
                "action_id": action_id,
            },
            level="info" if status == "sent" else "warn",
        )
        return {
            "ok": True,
            "status": status,
            "detail": str(result.get("detail") or ""),
            "conversation": _public_conversation(target),
            "messages": messages,
            "action_id": action_id,
        }

    async def resolve_video(self, key: str, cover: str) -> dict[str, Any]:
        cover = cover.strip()
        if not _is_http(cover):
            raise ActionError("缺少视频封面")
        cover_base = cover.split("?", 1)[0]
        cached = _VIDEO_CACHE.get(cover_base)
        if cached and time.monotonic() - cached[0] < 600:
            return {"ok": True, "video_url": cached[1], "cover": cover, "page_url": "", "cached": True}

        hits: list[dict[str, Any]] = []

        def on_response(response: Any) -> None:
            try:
                request = response.request
                content_type = str(response.headers.get("content-type") or "")
                url = response.url
                if (
                    request.resource_type in ("media", "xhr", "fetch")
                    and (
                        "video" in content_type
                        or "audio" in content_type
                        or ".mp4" in url
                        or ".m3u8" in url
                    )
                ):
                    hits.append({"url": url, "content_type": content_type, "status": response.status})
            except Exception:
                pass

        async with self._op_lock:
            held = await self._acquire_browser()
            page: Page | None = None
            try:
                _target, _messages, page = await self._open(key)
                page.on("response", on_response)
                clicked = await page.evaluate(
                    """(base) => {
                      const target = String(base || '');
                      const cards = [...document.querySelectorAll('.MessageItemShareAwemeawemeContainer')];
                      const match = cards.find(img => {
                        const src = (img.currentSrc || img.src || '').split('?')[0];
                        return src === target || (target && src.endsWith(target.split('/').pop()));
                      });
                      if (!match) return false;
                      match.scrollIntoView({ block: 'center' });
                      const play = match.closest('.MessageItemShareAwemecontainer')?.querySelector('.MessageItemShareAwemeplayIcon');
                      (play || match).click();
                      return true;
                    }""",
                    cover_base,
                )
                if not clicked:
                    raise ActionError("当前会话里没有找到这个视频卡片")
                deadline = time.monotonic() + 10
                while not hits and time.monotonic() < deadline:
                    await asyncio.sleep(0.25)
                page.remove_listener("response", on_response)
                await page.wait_for_timeout(350)
                video_url = _best_video_url(hits)
                modal_id = ""
                try:
                    modal_id = (parse_qs(urlparse(page.url).query).get("modal_id") or [""])[0]
                except Exception:
                    pass
                page_url = f"https://www.douyin.com/video/{modal_id}" if modal_id else ""
                try:
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(450)
                except Exception:
                    pass
                self._needs_fresh_page = True
                if video_url:
                    _VIDEO_CACHE[cover_base] = (time.monotonic(), video_url)
                self._last_activity = time.monotonic()
                return {
                    "ok": True,
                    "video_url": video_url,
                    "cover": cover,
                    "page_url": page_url,
                    "cached": False,
                }
            finally:
                if page is not None:
                    try:
                        page.remove_listener("response", on_response)
                    except Exception:
                        pass
                self._release_browser(held)

chat = ChatService()
