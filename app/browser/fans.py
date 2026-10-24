"""粉丝列表、回关和取关；粉丝操作走独立浏览器上下文（BrowserCore._fan_context）。"""
from __future__ import annotations

from typing import Any

from playwright.async_api import BrowserContext, Page

from ..config import AgentSettings
from .errors import ActionError, BrowserError, LoginRequired, RiskControl
from .js import (
    FANS_FIND_JS,
    FANS_LIST_JS,
    FANS_SCROLL_JS,
    FOLLOW_BUTTON_JS,
    RISK_JS,
    UNFOLLOW_BUTTON_JS,
    UNFOLLOW_STATE_JS,
)


class FansMixin:
    """运行台粉丝卡片对应的读取和单个回关/取关。"""

    settings: AgentSettings
    context: BrowserContext | None
    is_mock: bool

    async def _open_fans_modal(self, page: Page) -> bool:
        await page.goto("https://www.douyin.com/user/self", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1000)
        try:
            await page.wait_for_selector('[data-e2e="user-info-fans"]', state="visible", timeout=12000)
        except Exception:
            pass
        try:
            hits = await page.evaluate(RISK_JS)
        except Exception:
            hits = []
        if hits:
            raise RiskControl("页面出现风控/验证提示：" + "、".join(str(x) for x in hits))
        trigger = page.locator('[data-e2e="user-info-fans"]')
        clicked = False
        for i in range(await trigger.count()):
            try:
                if await trigger.nth(i).is_visible():
                    await trigger.nth(i).click(timeout=8000)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            try:
                clicked = bool(
                    await page.evaluate(
                        """() => {
                            const el = document.querySelector('[data-e2e="user-info-fans"]');
                            if (!el) return false;
                            el.click();
                            return true;
                        }"""
                    )
                )
            except Exception:
                clicked = False
        if not clicked:
            return False
        for _ in range(25):
            await page.wait_for_timeout(300)
            box = page.locator('[data-e2e="user-fans-container"]')
            if not await box.count():
                continue
            try:
                if not await box.first.is_visible():
                    continue
            except Exception:
                continue
            # 先把列表滚到底，确保后面的粉丝节点也加载出来。
            for _ in range(2):
                try:
                    await page.evaluate(FANS_SCROLL_JS)
                except Exception:
                    pass
                await page.wait_for_timeout(350)
            return True
        return False

    async def list_fans(self, limit: int = 24) -> dict[str, Any]:
        if self.is_mock:
            return {"items": [], "total": 0}
        if not self.context:
            raise BrowserError("浏览器还没有启动")
        if not await self.is_logged_in():
            raise LoginRequired("浏览器还没有登录抖音")
        page = await self._new_fan_page()
        try:
            if not await self._open_fans_modal(page):
                raise ActionError("没有打开粉丝列表，可能页面改版了")
            data = await page.evaluate(FANS_LIST_JS, max(1, min(int(limit), 60)))
            return dict(data or {"items": [], "total": 0})
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def _follow_via_profile(self, page: Page, name: str, profile_url: str) -> dict[str, Any]:
        """有主页链接时直接去对方主页点关注/回关，比打开粉丝弹窗快很多。"""
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=45000)
        state: dict[str, Any] = {}
        for _ in range(10):
            state = dict(await page.evaluate(UNFOLLOW_STATE_JS) or {})
            if state.get("hits"):
                break
            await page.wait_for_timeout(300)
        try:
            hits = await page.evaluate(RISK_JS)
        except Exception:
            hits = []
        if hits:
            raise RiskControl("页面出现风控/验证提示：" + "、".join(str(x) for x in hits))
        if state.get("following"):
            return {"status": "already", "detail": "已经关注或互关，无需重复操作", "name": name}
        if not await page.evaluate(FOLLOW_BUTTON_JS):
            return {"status": "failed", "detail": "主页上没有找到关注/回关按钮", "name": name}
        btn = page.locator('[data-dy-agent-follow-profile="1"]').first
        try:
            await btn.click(timeout=5000)
        except Exception as exc:
            return {"status": "failed", "detail": f"关注按钮点击失败：{str(exc)[:160]}", "name": name}
        following = False
        for _ in range(8):
            await page.wait_for_timeout(250)
            state = dict(await page.evaluate(UNFOLLOW_STATE_JS) or {})
            if state.get("following"):
                following = True
                break
        try:
            hits = await page.evaluate(RISK_JS)
        except Exception:
            hits = []
        if hits:
            return {"status": "uncertain", "detail": "回关后页面出现验证提示，请手动确认结果", "name": name}
        return {
            "status": "sent" if following else "uncertain",
            "detail": "已回关" if following else "已点击回关，但未读到状态变化",
            "name": name,
            "profile_url": profile_url,
        }

    async def follow_fan(self, name: str, profile_url: str = "") -> dict[str, Any]:
        name = (name or "").strip()
        profile_url = (profile_url or "").strip()
        if not name and not profile_url:
            raise ActionError("缺少粉丝账号")
        if self.is_mock:
            return {"status": "dry_run", "detail": "预演，未回关", "name": name}
        if not self.context:
            raise BrowserError("浏览器还没有启动")
        if not await self.is_logged_in():
            raise LoginRequired("浏览器还没有登录抖音")
        page = await self._new_fan_page()
        try:
            if profile_url:
                return await self._follow_via_profile(page, name, profile_url)
            if not await self._open_fans_modal(page):
                raise ActionError("没有打开粉丝列表，可能页面改版了")
            found = await page.evaluate(
                FANS_FIND_JS, {"name": name, "url": profile_url}
            )
            found = dict(found or {})
            if found.get("error") == "already":
                return {
                    "status": "already",
                    "detail": "已经关注或互关，无需重复操作",
                    "name": found.get("name") or name,
                }
            if found.get("error"):
                return {
                    "status": "failed",
                    "detail": "粉丝列表里没有找到这个账号",
                    "name": name,
                }
            btn = page.locator('[data-dy-agent-follow="1"]').first
            try:
                await btn.scroll_into_view_if_needed(timeout=5000)
                await btn.click(timeout=5000)
            except Exception as exc:
                return {
                    "status": "failed",
                    "detail": f"回关按钮点击失败：{str(exc)[:160]}",
                    "name": found.get("name") or name,
                }
            state = ""
            for _ in range(8):
                await page.wait_for_timeout(250)
                try:
                    state = str(
                        await page.evaluate(
                            """() => {
                                const row = document.querySelector('[data-dy-agent-fan="1"]');
                                if (!row) return '';
                                const button = [...row.querySelectorAll('.G93_q6Y6 .semi-button')][0];
                                return button ? (button.innerText || '').trim() : '';
                            }"""
                        )
                        or ""
                    )
                except Exception:
                    state = ""
                if state and state != "回关":
                    break
            try:
                hits = await page.evaluate(RISK_JS)
            except Exception:
                hits = []
            if hits:
                return {
                    "status": "uncertain",
                    "detail": "回关后页面出现验证提示，请手动确认结果",
                    "name": found.get("name") or name,
                }
            verified = bool(state and state != "回关")
            return {
                "status": "sent" if verified else "uncertain",
                "detail": "已回关" if verified else "已点击回关，但未读到状态变化",
                "name": found.get("name") or name,
                "profile_url": found.get("url") or profile_url,
                "state": state,
            }
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def unfollow_fan(self, name: str, profile_url: str = "") -> dict[str, Any]:
        name = (name or "").strip()
        profile_url = (profile_url or "").strip()
        if not profile_url:
            raise ActionError("缺少粉丝主页链接")
        if self.is_mock:
            return {"status": "dry_run", "detail": "预演，未取关", "name": name}
        if not self.context:
            raise BrowserError("浏览器还没有启动")
        if not await self.is_logged_in():
            raise LoginRequired("浏览器还没有登录抖音")
        page = await self._new_fan_page()
        try:
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1200)
            try:
                hits = await page.evaluate(RISK_JS)
            except Exception:
                hits = []
            if hits:
                raise RiskControl("页面出现风控/验证提示：" + "、".join(str(x) for x in hits))
            try:
                body = await page.evaluate("() => document.body ? document.body.innerText : ''")
            except Exception:
                body = ""
            if name and name not in body:
                # 昵称在部分主页模板里渲染较慢，不能只凭一次 innerText 判断走错页面。
                for _ in range(3):
                    await page.wait_for_timeout(300)
                    try:
                        body = await page.evaluate("() => document.body ? document.body.innerText : ''")
                    except Exception:
                        body = ""
                    if name in body:
                        break
                if name not in body:
                    if "用户不存在" in body or "账号已注销" in body or "/user/" not in page.url:
                        return {"status": "failed", "detail": "打开的不是有效的粉丝主页"}
            found = await page.evaluate(UNFOLLOW_BUTTON_JS)
            if not found:
                return {
                    "status": "skipped",
                    "detail": "主页没有已关注/互关按钮，可能已经取关",
                    "name": name,
                    "profile_url": profile_url,
                }
            btn = page.locator('[data-dy-agent-unfollow="1"]').first
            try:
                await btn.scroll_into_view_if_needed(timeout=5000)
                await btn.click(timeout=5000)
            except Exception as exc:
                return {
                    "status": "failed",
                    "detail": f"取关按钮点击失败：{str(exc)[:160]}",
                    "name": name,
                    "profile_url": profile_url,
                }
            await page.wait_for_timeout(400)
            clicked_menu = False
            for label in ("取消关注", "不再关注", "取关"):
                try:
                    loc = page.get_by_text(label, exact=True)
                    for i in range(await loc.count()):
                        if await loc.nth(i).is_visible():
                            await loc.nth(i).click(timeout=4000)
                            clicked_menu = True
                            break
                except Exception:
                    continue
                if clicked_menu:
                    break
            await page.wait_for_timeout(350)
            confirmed = await page.evaluate(
                r"""() => {
                    const clean = s => (s || '').replace(/\s+/g, ' ').trim();
                    const dialogs = [...document.querySelectorAll('[role="dialog"], .semi-modal, [class*="modal"], [class*="dialog"]')]
                        .filter(el => {
                            const r = el.getBoundingClientRect();
                            return r.width > 0 && r.height > 0;
                        });
                    for (const dialog of dialogs) {
                        const text = dialog.innerText || '';
                        if (!/取消关注|不再关注|取关/.test(text)) continue;
                        const button = [...dialog.querySelectorAll('button')]
                            .find(el => ['确定', '确认'].includes(clean(el.innerText)));
                        if (button) {
                            button.setAttribute('data-dy-agent-confirm-unfollow', '1');
                            return true;
                        }
                    }
                    return false;
                }"""
            )
            if confirmed:
                try:
                    confirm_btn = page.locator('[data-dy-agent-confirm-unfollow="1"]').first
                    await confirm_btn.click(timeout=4000)
                    await page.wait_for_timeout(450)
                except Exception:
                    pass
            await page.wait_for_timeout(400)
            state = await page.evaluate(UNFOLLOW_STATE_JS)
            state = dict(state or {})
            if not state.get("following") and (state.get("followable") or not state.get("hits")):
                return {
                    "status": "sent",
                    "detail": "已取关",
                    "name": name,
                    "profile_url": profile_url,
                }
            return {
                "status": "uncertain",
                "detail": "已点击取关，但主页仍显示已关注，请刷新确认",
                "name": name,
                "profile_url": profile_url,
            }
        finally:
            try:
                await page.close()
            except Exception:
                pass
