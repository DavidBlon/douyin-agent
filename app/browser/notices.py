"""通知铃铛：读取通知列表、点开评论通知、消掉红点。"""
from __future__ import annotations

import re
from typing import Any

from playwright.async_api import Page

from .js import NOTIFICATION_ROWS_JS


class NoticesMixin:
    """通知下拉的读取与点击；replier 用它定位要回复的评论。"""

    async def read_notifications(self, page: Page, filter_text: str = "全部消息") -> list[dict[str, Any]]:
        trigger = page.locator('[data-e2e="something-button"]').filter(has_text="通知")
        clicked = False
        for i in range(await trigger.count()):
            try:
                if await trigger.nth(i).is_visible():
                    await trigger.nth(i).click()
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            return []
        await page.wait_for_timeout(900)
        if filter_text and filter_text != "全部消息":
            current = page.locator('[data-e2e="cur-filter"]')
            try:
                if await current.count():
                    await current.first.click()
                    await page.wait_for_timeout(450)
                    await page.evaluate(
                        """(label) => {
                            const list = document.querySelector('[data-e2e="cur-dropdown-list"]');
                            if (!list) return false;
                            const opt = [...list.querySelectorAll('div')].find(el => (el.textContent || '').trim() === label);
                            if (!opt) return false;
                            opt.click();
                            return true;
                        }""",
                        filter_text,
                    )
                    await page.wait_for_timeout(1100)
            except Exception:
                pass
        try:
            data = await page.evaluate(NOTIFICATION_ROWS_JS)
        except Exception:
            data = []
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
        except Exception:
            pass
        return [dict(x) for x in (data or []) if isinstance(x, dict)]

    async def click_notification(self, page: Page, name: str, comment: str) -> dict[str, Any]:
        """Open the bell dropdown and click the matching comment notification row."""
        trigger = page.locator('[data-e2e="something-button"]').filter(has_text="通知")
        clicked = False
        for i in range(await trigger.count()):
            try:
                if await trigger.nth(i).is_visible():
                    await trigger.nth(i).click()
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            return {}
        await page.wait_for_timeout(1100)
        marked = await page.evaluate(
            """([name, comment]) => {
                const clean = s => (s || '').replace(/\s+/g, ' ').trim();
                const root = document.querySelector('[data-e2e="listDlgTest-container"]');
                if (!root) return false;
                const cards = [...root.querySelectorAll('[data-e2e="user-name-card"]')];
                for (const card of cards) {
                    const label = clean(card.innerText).replace(/^@/, '');
                    if (label !== name) continue;
                    let row = card;
                    let text = '';
                    for (let i = 0; i < 8 && row && row.parentElement; i++) {
                        row = row.parentElement;
                        text = clean(row.innerText);
                        if (text.includes('回复') || text.includes('评论')) break;
                        if (text.length > 600) break;
                    }
                    if (!text.includes(comment)) continue;
                    if (!/回复了你的评论|评论了你的作品/.test(text)) continue;
                    row.setAttribute('data-dy-notice-click', '1');
                    return true;
                }
                return false;
            }""",
            [name, comment],
        )
        if not marked:
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            return {}
        row = page.locator('[data-dy-notice-click="1"]').first
        try:
            box = await row.bounding_box()
            if not box:
                return {}
            await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        except Exception:
            return {}
        await page.wait_for_timeout(1800)
        match = re.search(r"modal_id=(\d+)", page.url) or re.search(r"/video/(\d+)", page.url)
        return {"url": page.url, "modal_id": match.group(1) if match else ""}

    async def consume_notification(self, page: Page, name: str, comment: str) -> None:
        """点开对应通知条目再关掉，让铃铛上的红点消失。"""
        try:
            await self.click_notification(page, name, comment)
        except Exception:
            return
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(400)
        except Exception:
            pass
