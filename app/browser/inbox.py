"""私信收件箱：红点检测、会话列表、打开会话、读取消息、发送私信。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.async_api import Page

from .. import selectors
from .core import _now_tag
from .errors import ActionError
from .js import (
    CHAT_MESSAGES_JS,
    CHAT_TRANSCRIPT_JS,
    CONVERSATIONS_JS,
    NAV_BADGES_JS,
)


class InboxMixin:
    """浮动收件箱和 1 对 1 会话的操作；replier/sharer/chat 都走这里。"""

    page: Page | None
    _shots: Path

    async def open_inbox(self, page: Page) -> bool:
        async def is_open() -> bool:
            try:
                return bool(
                    await page.evaluate(
                        """() => {
                            const el = document.querySelector('[data-e2e="im-dialog"]');
                            if (el) {
                                const r = el.getBoundingClientRect();
                                if (r.width > 100 && r.height > 100) return true;
                            }
                            return [...document.querySelectorAll('[data-e2e="conversation-item"]')].some(el => {
                                const r = el.getBoundingClientRect();
                                return r.width > 80 && r.height > 30;
                            });
                        }"""
                    )
                )
            except Exception:
                return False

        if await is_open():
            return True
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(250)
        except Exception:
            pass
        entry = page.locator('[data-e2e="im-entry"]')
        for i in range(await entry.count()):
            try:
                if await entry.nth(i).is_visible():
                    await entry.nth(i).click()
                    await page.wait_for_timeout(900)
                    if await is_open():
                        return True
                    break
            except Exception:
                continue
        try:
            await page.evaluate(
                """() => {
                    const el = [...document.querySelectorAll('[data-e2e="im-entry"]')].find(el => {
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    });
                    if (el) el.click();
                }"""
            )
            await page.wait_for_timeout(900)
        except Exception:
            pass
        return await is_open()

    async def nav_badges(self, page: Page | None = None) -> dict[str, int]:
        target = page or self.page
        if target is None or target.is_closed():
            return {"im": -1, "notice": -1, "avatar": -1}
        try:
            data = await target.evaluate(NAV_BADGES_JS)
        except Exception:
            return {"im": -1, "notice": -1, "avatar": -1}
        out = {"im": -1, "notice": -1, "avatar": -1}
        for key in out:
            try:
                out[key] = int((data or {}).get(key, -1))
            except Exception:
                out[key] = -1
        return out

    async def list_conversations(self, page: Page) -> list[dict[str, Any]]:
        if not await self.open_inbox(page):
            return []
        try:
            data = await page.evaluate(CONVERSATIONS_JS)
        except Exception:
            return []
        return [dict(x) for x in (data or []) if isinstance(x, dict)]

    async def _click_conversation_row(self, page: Page, item) -> bool:
        try:
            # 会话列表会实时刷新，等待“稳定”会偶发超时；直接滚到中心、等列表落定再按坐标点击。
            try:
                await page.mouse.move(8, 8)
            except Exception:
                pass
            try:
                await item.evaluate("el => el.scrollIntoView({ block: 'center' })")
            except Exception:
                pass
            await page.wait_for_timeout(500)
            box = await item.bounding_box()
            if not box:
                return False
            points: list[tuple[float, float]] = [
                (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            ]
            for sel in (".conversationConversationItemtitle", "img"):
                try:
                    loc = item.locator(sel).first
                    if await loc.count():
                        b = await loc.bounding_box()
                        if b:
                            points.append((b["x"] + b["width"] / 2, b["y"] + b["height"] / 2))
                except Exception:
                    continue
            for x, y in points:
                await page.evaluate(
                    "() => { const h = document.querySelector('#douyin-header'); if (h) h.style.pointerEvents = 'none'; }"
                )
                covered = False
                hit_info = ""
                try:
                    hit_info = await page.evaluate(
                        """(pt) => {
                            const hit = document.elementFromPoint(pt.x, pt.y);
                            if (!hit) return 'none';
                            const cls = typeof hit.className === 'string' ? hit.className : '';
                            return `${hit.tagName}.${cls.slice(0, 60)}`;
                        }""",
                        {"x": x, "y": y},
                    )
                    covered = not bool(
                        await item.evaluate(
                            """(el, pt) => {
                                const hit = document.elementFromPoint(pt.x, pt.y);
                                return !!hit && (el === hit || el.contains(hit));
                            }""",
                            {"x": x, "y": y},
                        )
                    )
                except Exception:
                    pass
                if covered:
                    print(f"[chat] row point covered by {hit_info}", flush=True)
                    continue
                # 顶部栏的悬浮层会盖住较低的会话行，点击期间临时让 header 穿透。
                try:
                    await page.mouse.click(x, y)
                finally:
                    try:
                        await page.evaluate(
                            "() => { const h = document.querySelector('#douyin-header'); if (h) h.style.pointerEvents = ''; }"
                        )
                    except Exception:
                        pass
                return True
            try:
                await page.evaluate(
                    "() => { const h = document.querySelector('#douyin-header'); if (h) h.style.pointerEvents = ''; }"
                )
            except Exception:
                pass
            print(
                f"[chat] conversation row covered at ({points[0][0]:.0f},{points[0][1]:.0f}) hit={hit_info}",
                flush=True,
            )
            return False
        except Exception as exc:
            print(f"[chat] click conversation row failed: {str(exc)[:240]!r}", flush=True)
            return False

    async def chat_header_title(self, page: Page) -> str:
        try:
            return (await page.locator(".StackLayoutStackChatHeadertitle").first.inner_text()).strip()
        except Exception:
            return ""

    async def chat_list_visible(self, page: Page) -> bool:
        items = page.locator('[data-e2e="conversation-item"]')
        try:
            count = await items.count()
        except Exception:
            return False
        for index in range(min(count, 6)):
            item = items.nth(index)
            try:
                box = await item.bounding_box()
                if not box or box["width"] < 40 or box["height"] < 20:
                    continue
                x = box["x"] + box["width"] / 2
                y = box["y"] + box["height"] / 2
                hit = await item.evaluate(
                    """(el, pt) => {
                        const node = document.elementFromPoint(pt.x, pt.y);
                        return !!node && (el === node || el.contains(node));
                    }""",
                    {"x": x, "y": y},
                )
                if hit:
                    return True
            except Exception:
                continue
        return False

    async def chat_back_to_list(self, page: Page) -> bool:
        """Douyin 私信是浮动聊天窗；切换会话前要先点左上角返回，露出会话列表。"""
        if await self.chat_list_visible(page):
            return True
        points: list[tuple[float, float]] = []
        try:
            info = await page.evaluate(
                """() => {
                    const title = document.querySelector('.StackLayoutStackChatHeadertitle');
                    if (!title) return null;
                    let panel = title;
                    for (let i = 0; i < 12 && panel.parentElement; i++) {
                        const r = panel.getBoundingClientRect();
                        if (r.width >= 260 && r.height >= 250) break;
                        panel = panel.parentElement;
                    }
                    const tr = title.getBoundingClientRect();
                    const pr = panel.getBoundingClientRect();
                    return {
                        panelLeft: pr.left, panelTop: pr.top, panelWidth: pr.width, panelHeight: pr.height,
                        titleLeft: tr.left, titleY: tr.top + tr.height / 2,
                    };
                }"""
            )
        except Exception:
            info = None
        if info:
            title_left = float(info["titleLeft"])
            title_y = float(info["titleY"])
            panel_left = float(info["panelLeft"])
            points.extend(
                [
                    (title_left - 50, title_y),
                    (title_left - 36, title_y),
                    (panel_left + 22, title_y),
                    (panel_left + 32, title_y),
                    (panel_left + 44, title_y),
                ]
            )
        for x, y in points[:6]:
            try:
                await page.mouse.click(x, y)
            except Exception:
                continue
            await page.wait_for_timeout(650)
            if await self.chat_list_visible(page):
                return True
        print(f"[chat] back-to-list failed points={points[:6]} info={info}", flush=True)
        return False

    async def open_conversation(self, page: Page, index: int) -> bool:
        loc = page.locator('[data-e2e="conversation-item"]').nth(index)
        if not await self._click_conversation_row(page, loc):
            return False
        await page.wait_for_timeout(1300)
        return True

    async def open_conversation_by_title(self, page: Page, title: str) -> bool:
        wanted = (title or "").strip()
        if not wanted:
            return False
        header_now = await self.chat_header_title(page)
        if header_now and (header_now == wanted or wanted in header_now):
            return True
        try:
            if await page.locator('[data-e2e="conversation-item"]').count() == 0:
                await self.open_inbox(page)
                await page.wait_for_timeout(700)
        except Exception:
            pass
        if not await self.chat_list_visible(page):
            recovered = await self.chat_back_to_list(page)
            if not recovered:
                try:
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(500)
                    await self.open_inbox(page)
                    await page.wait_for_timeout(800)
                    recovered = await self.chat_list_visible(page)
                    if not recovered:
                        recovered = await self.chat_back_to_list(page)
                except Exception:
                    recovered = False
            if not recovered:
                print(f"[chat] cannot reveal conversation list for {wanted!r}", flush=True)
                return False
        items = page.locator('[data-e2e="conversation-item"]')
        count = 0
        last_exact: list[int] = []
        last_loose: list[int] = []
        for _attempt in range(3):
            try:
                count = await items.count()
            except Exception:
                count = 0
            exact: list[int] = []
            loose: list[int] = []
            for index in range(count):
                try:
                    title_el = items.nth(index).locator('.conversationConversationItemtitle')
                    if not await title_el.count():
                        continue
                    name = (await title_el.inner_text()).strip()
                except Exception:
                    continue
                if name == wanted:
                    exact.append(index)
                elif wanted and (wanted in name or name in wanted):
                    loose.append(index)
            last_exact, last_loose = exact, loose
            for index in exact + loose:
                item = items.nth(index)
                if not await self._click_conversation_row(page, item):
                    continue
                for _ in range(16):
                    await page.wait_for_timeout(250)
                    header = await self.chat_header_title(page)
                    if header and (header == wanted or wanted in header):
                        await page.wait_for_timeout(350)
                        return True
            try:
                await page.mouse.move(8, 8)
                await page.wait_for_timeout(350)
            except Exception:
                pass
        header = await self.chat_header_title(page)
        print(
            f"[chat] open conversation failed title={wanted!r} count={count} "
            f"exact={last_exact} loose={last_loose} header={header!r} url={page.url[:120]!r}",
            flush=True,
        )
        try:
            await page.screenshot(path=str(self._shots / f"chat_open_fail_{_now_tag()}.png"))
        except Exception:
            pass
        return False

    async def read_chat_messages(self, page: Page) -> list[dict[str, Any]]:
        try:
            data = await page.evaluate(CHAT_MESSAGES_JS)
        except Exception:
            return []
        return [dict(x) for x in (data or []) if isinstance(x, dict) and str(x.get("text") or "").strip()]

    async def read_chat_transcript(self, page: Page) -> list[dict[str, Any]]:
        try:
            data = await page.evaluate(CHAT_TRANSCRIPT_JS)
        except Exception:
            return []
        return [dict(x) for x in (data or []) if isinstance(x, dict)]

    async def send_chat_message(self, page: Page, text: str) -> dict[str, Any]:
        box = None
        for _ in range(12):
            box = await self._find_visible_in(page, selectors.DM_INPUT, prefer_last=True)
            if box:
                break
            await page.wait_for_timeout(300)
        if not box:
            raise ActionError("没有找到私信输入框")
        draft = await self._editor_text(box)
        if draft:
            raise ActionError("私信输入框里已有未发送草稿，已停止，避免混入。")
        try:
            await box.click()
            await page.keyboard.insert_text(text)
            await page.wait_for_timeout(350)
        except Exception as exc:
            raise ActionError(f"填写私信失败：{exc}") from exc
        send = await self._find_visible_in(page, selectors.DM_SEND)
        if not send:
            raise ActionError("没有找到私信发送按钮")
        try:
            await send.click()
        except Exception as exc:
            raise ActionError(f"发送私信失败：{exc}") from exc
        await page.wait_for_timeout(1100)
        verified = False
        try:
            body = await page.evaluate("() => document.body ? document.body.innerText : ''")
            verified = text[:18] in body
        except Exception:
            pass
        return {
            "status": "sent" if verified else "uncertain",
            "detail": "私信已发送" if verified else "已点击私信发送，但未读到确认",
        }
