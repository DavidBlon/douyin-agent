"""推荐流域：读取当前视频、翻页、点赞、评论、@ 提及、作者关系核对和主动私信。"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Page

from .. import selectors
from ..config import AgentSettings
from .core import _now_tag
from .errors import ActionError, LoginRequired
from .js import (
    COMMENTS_JS,
    CURRENT_ID_JS,
    MARK_SCOPE_JS,
    MENTION_OPTION_JS,
    READ_JS,
    RELATION_JS,
)


class FeedMixin:
    """刷推荐流时的读、判、点、评、私信动作。状态见 BrowserCore。"""

    settings: AgentSettings
    context: BrowserContext | None
    page: Page | None
    is_mock: bool
    _shots: Path

    async def read_current(self, include_comments: bool | None = False) -> dict[str, Any]:
        page = await self._ensure_page()
        await self.assert_safe()
        # 作者、文案浮层只在鼠标移到视频上时才出现在 DOM 里。
        try:
            await page.mouse.move(600, 400)
            await page.wait_for_timeout(400)
        except Exception:
            pass
        data = await page.evaluate(READ_JS)
        if not data:
            shot = await self.screenshot("no_video")
            raise ActionError(
                "没有在页面上识别到视频。可能还在登录/推荐页加载，或抖音改版了。"
                f"已保存截图：{shot}"
            )
        data = dict(data)
        if not data.get("aweme_id"):
            # 直播/非普通视频没有作品 id；不要给它编一个 id 继续点赞评论。
            data["is_live"] = True
            seed = f"{data.get('url')}|{data.get('author')}|{data.get('description')}"
            data["aweme_id"] = "web_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:18]
        if not data.get("url"):
            data["url"] = page.url
        if include_comments is None:
            include_comments = self.settings.auto_comment
        if data.get("is_live"):
            data["comments"] = []
        elif include_comments:
            try:
                data["comments"] = await self.read_comments()
            except Exception:
                data["comments"] = []
        else:
            data.setdefault("comments", [])
        return data

    async def read_comments(self) -> list[str]:
        page = await self._ensure_page()
        opened = await self._open_comments()
        if not opened:
            return []
        try:
            found = await page.evaluate(COMMENTS_JS)
            return [str(x) for x in (found or []) if str(x).strip()][:5]
        finally:
            try:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(250)
            except Exception:
                pass

    async def _mark_current(self) -> None:
        try:
            await (await self._ensure_page()).evaluate(MARK_SCOPE_JS)
        except Exception:
            pass

    async def _open_comments(self) -> bool:
        page = await self._ensure_page()
        if await self._find_visible(selectors.COMMENT_PANEL):
            return True
        await self._mark_current()
        scoped = [f'[data-dy-agent-current="1"] {sel}' for sel in selectors.COMMENT_OPEN]
        trigger = await self._find_visible(scoped + selectors.COMMENT_OPEN)
        if not trigger:
            return False
        try:
            await trigger.click()
        except Exception:
            return False
        await page.wait_for_timeout(1200)
        return bool(await self._find_visible(selectors.COMMENT_PANEL))

    async def current_id(self) -> str:
        page = await self._ensure_page()
        try:
            value = await page.evaluate(CURRENT_ID_JS)
            return str(value or "")
        except Exception:
            return ""

    async def next_video(self) -> str:
        page = await self._ensure_page()
        before = await self.current_id()
        # 1) keyboard feed navigation
        try:
            await page.keyboard.press("ArrowDown")
            await page.wait_for_timeout(1600)
            after = await self.current_id()
            if after and after != before:
                return after
        except Exception:
            pass
        # 2) wheel
        try:
            await page.mouse.wheel(0, 900)
            await page.wait_for_timeout(1600)
            after = await self.current_id()
            if after and after != before:
                return after
        except Exception:
            pass
        # 3) PageDown
        try:
            await page.keyboard.press("PageDown")
            await page.wait_for_timeout(1600)
            after = await self.current_id()
            if after and after != before:
                return after
        except Exception:
            pass
        # 4) feed next arrow（直播页对键盘不敏感，这个更可靠）
        try:
            arrow = page.locator('[data-e2e="video-switch-next-arrow"]')
            for i in range(await arrow.count()):
                try:
                    if await arrow.nth(i).is_visible():
                        await arrow.nth(i).click()
                        await page.wait_for_timeout(1600)
                        after = await self.current_id()
                        if after and after != before:
                            return after
                        break
                except Exception:
                    continue
        except Exception:
            pass
        # 5) 直播/异常卡片可能卡住键盘滚动：刷新推荐流，换一批内容，不终止整轮。
        if not self.is_mock:
            try:
                await page.goto(
                    self.settings.feed_url or "https://www.douyin.com/?recommend=1&from_nav=1",
                    wait_until="domcontentloaded",
                    timeout=45000,
                )
                await page.wait_for_timeout(2200)
                return await self.current_id()
            except Exception:
                return ""
        raise ActionError("推荐流没有滑动到下一条，已停止，避免在同一个视频上重复操作。")

    async def _like_state(self, loc) -> bool:
        try:
            return bool(
                await loc.evaluate(
                    """el => {
                       const state = el.getAttribute('data-e2e-state') || '';
                       if (state.includes('no-digged')) return false;
                       if (state.includes('digged')) return true;
                       return el.getAttribute('aria-pressed') === 'true' ||
                         /(?:^|\\s)(liked|active)(?:\\s|$)/i.test(el.className || '') ||
                         !!(el.querySelector && el.querySelector('.liked'));
                     }"""
                )
            )
        except Exception:
            return False

    async def like(self) -> dict[str, Any]:
        page = await self._ensure_page()
        await self.assert_safe()
        await self._mark_current()
        scoped = [f'[data-dy-agent-current="1"] {sel}' for sel in selectors.LIKE]
        btn = await self._find_visible(scoped + selectors.LIKE)
        if not btn:
            shot = await self.screenshot("like_missing")
            raise ActionError(f"没有找到点赞按钮，可能页面改版。截图：{shot}")
        if await self._like_state(btn):
            return {"status": "skipped", "detail": "已经是点赞状态"}
        try:
            await btn.click()
        except Exception as exc:
            raise ActionError(f"点赞点击失败：{exc}") from exc
        await page.wait_for_timeout(650)
        state = await self._like_state(btn)
        shot = await self.screenshot("like")
        return {
            "status": "sent" if state else "uncertain",
            "detail": "已点击点赞" + ("，状态已确认" if state else "，未读到确认状态"),
            "screenshot": shot,
        }

    async def _insert_comment_mention(self, box, name: str) -> bool:
        page = await self._ensure_page()
        name = (name or "").strip()
        if not name:
            return False

        async def find_option():
            try:
                return await page.evaluate(MENTION_OPTION_JS, name)
            except Exception:
                return None

        try:
            await box.click()
            await page.keyboard.type("@")
            option = None
            for i in range(6):
                await page.wait_for_timeout(400)
                option = await find_option()
                if option:
                    break
                if i == 2 and len(name) > 3:
                    await page.keyboard.type(name[:4])
            if option:
                await page.mouse.click(
                    float(option["x"]) + float(option["w"]) / 2,
                    float(option["y"]) + float(option["h"]) / 2,
                )
                await page.wait_for_timeout(650)
                chips = await box.locator(".douyin_mention_word, [class*='mention_word']").count()
                if chips:
                    await box.focus()
                    await page.keyboard.press("End")
                    return True
            await box.click()
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
            await page.wait_for_timeout(200)
            return False
        except Exception:
            try:
                await box.click()
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")
            except Exception:
                pass
            return False

    async def comment(self, text: str, mention: str = "") -> dict[str, Any]:
        page = await self._ensure_page()
        await self.assert_safe()
        if not await self._open_comments():
            shot = await self.screenshot("comment_missing")
            raise ActionError(f"没有找到评论输入框，可能页面改版。截图：{shot}")
        trigger = await self._find_visible(selectors.COMMENT_INPUT_TRIGGER)
        if trigger:
            try:
                await trigger.click()
                await page.wait_for_timeout(400)
            except Exception:
                pass
        box = await self._find_visible(selectors.COMMENT_INPUT, prefer_last=True)
        if not box:
            shot = await self.screenshot("comment_input_missing")
            raise ActionError(f"评论输入框不可见。截图：{shot}")
        draft = await self._editor_text(box)
        if draft:
            shot = await self.screenshot("comment_draft")
            raise ActionError(f"评论输入框里已有未发送草稿，已停止，避免混入。截图：{shot}")
        mention_used = False
        try:
            await box.click()
            if mention:
                mention_used = await self._insert_comment_mention(box, mention)
                if not mention_used:
                    await page.keyboard.insert_text(f"@{mention} ")
            await page.keyboard.insert_text(text)
            await page.wait_for_timeout(350)
        except Exception as exc:
            raise ActionError(f"填写评论失败：{exc}") from exc
        send = await self._find_visible(selectors.COMMENT_SEND)
        if not send:
            shot = await self.screenshot("comment_send_missing")
            raise ActionError(f"没有找到评论发送按钮。截图：{shot}")
        try:
            await send.click()
        except Exception as exc:
            raise ActionError(f"发送评论失败：{exc}") from exc
        await page.wait_for_timeout(1100)
        verified = False
        try:
            current = (await box.inner_text()).strip() if await box.is_visible() else ""
            body = await page.evaluate("() => document.body ? document.body.innerText : ''")
            probe = text[:24].strip()
            verified = (not current) or bool(probe and probe in body) or bool(mention and mention in body)
        except Exception:
            pass
        shot = await self.screenshot("comment")
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        note = ""
        if mention:
            note = "；已 @ 接收账号" if mention_used else "；@ 为纯文本（未选中联系人）"
        return {
            "status": "sent" if verified else "uncertain",
            "detail": ("评论已发送" if verified else "已点击发送，但未读到确认状态") + note,
            "screenshot": shot,
        }

    async def author_relation(self, author_url: str, author_name: str = "") -> dict[str, Any]:
        """Open the author profile in a background tab and read the follow relation."""
        if self.is_mock:
            return {"mutual": False, "following": False, "hits": []}
        if not author_url:
            return {"mutual": False, "following": False, "hits": []}
        assert self.context is not None
        page = await self.new_page()
        try:
            await page.goto(author_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1800)
            if not await self.is_logged_in():
                raise LoginRequired("登录态不可用，无法核对互关关系")
            data = await page.evaluate(RELATION_JS)
            if author_name and author_name.strip():
                body = await page.evaluate("() => document.body ? document.body.innerText : ''")
                if author_name not in body:
                    raise ActionError(f"打开的作者页不是「{author_name}」，已停止私信。")
            return dict(data or {})
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def dm(self, author_url: str, author_name: str, text: str) -> dict[str, Any]:
        if not author_url:
            raise ActionError("没有作者主页，无法私信")
        assert self.context is not None
        page = await self.new_page()
        active = page
        page.set_default_timeout(15000)
        try:
            await page.goto(author_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(2200)
            if not await self.is_logged_in():
                raise LoginRequired("登录态不可用，无法私信")
            body = await page.evaluate("() => document.body ? document.body.innerText : ''")
            if author_name and author_name.strip() and author_name not in body:
                raise ActionError(f"打开的作者页不是「{author_name}」，已停止私信。")
            btn = await self._find_visible(selectors.DM_BUTTON)
            if not btn:
                shot_path = self._shots / f"dm_missing_{_now_tag()}.png"
                await page.screenshot(path=str(shot_path))
                raise ActionError(f"作者页没有找到私信按钮。截图：/media/shots/{shot_path.name}")
            before_pages = set(self.context.pages)
            await btn.click()
            await page.wait_for_timeout(1000)
            # 私信可能开在当前页，也可能开了新页面；只认这次点击新开的页面。
            opened = [p for p in self.context.pages if p not in before_pages]
            if opened:
                active = opened[-1]
                await active.wait_for_timeout(700)
            box = await self._find_visible_in(active, selectors.DM_INPUT, prefer_last=True)
            if not box:
                raise ActionError("没有找到私信输入框")
            draft = await self._editor_text(box)
            if draft:
                raise ActionError("私信输入框里已有未发送草稿，已停止，避免混入。")
            await box.click()
            await active.keyboard.insert_text(text)
            await active.wait_for_timeout(250)
            send = await self._find_visible_in(active, selectors.DM_SEND)
            if not send:
                raise ActionError("没有找到私信发送按钮")
            await send.click()
            await active.wait_for_timeout(900)
            body_after = await active.evaluate("() => document.body ? document.body.innerText : ''")
            verified = text[:18] in body_after
            shot_name = f"dm_{_now_tag()}.png"
            await active.screenshot(path=str(self._shots / shot_name))
            return {
                "status": "sent" if verified else "uncertain",
                "detail": "私信已发送" if verified else "已点击私信发送，但未读到确认",
                "screenshot": f"/media/shots/{shot_name}",
            }
        finally:
            try:
                await page.close()
            except Exception:
                pass
            if active is not page:
                try:
                    await active.close()
                except Exception:
                    pass
