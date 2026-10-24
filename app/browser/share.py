"""视频原生分享面板：把当前视频分享给已有联系人。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.async_api import Page

from .core import _now_tag
from .errors import ActionError


class ShareMixin:
    """打开分享面板、校验视频 ID、挑联系人并发送。"""

    _shots: Path

    async def share_current_video(
        self, page: Page, recipient: str, expected_id: str = ""
    ) -> dict[str, Any]:
        """Use the video's native share panel to send it to an existing contact."""
        panel_count_js = """() => [...document.querySelectorAll('input[placeholder="搜索"]')].filter(e => {
            const r = e.getBoundingClientRect();
            return r.width > 0 && r.height > 0
                && e.getAttribute('data-e2e') !== 'searchbar-input'
                && !e.closest('[data-e2e="searchbar"]');
        }).length"""

        async def panel_count() -> int:
            try:
                return int(await page.evaluate(panel_count_js))
            except Exception:
                return 0

        async def debug_shot(prefix: str, extra: dict[str, Any]) -> None:
            debug = dict(extra)
            try:
                debug.update(
                    await page.evaluate(
                        """() => ({
                            url: location.href,
                            share_count: document.querySelectorAll('[data-e2e="video-player-share"]').length,
                            share_visible: [...document.querySelectorAll('[data-e2e="video-player-share"]')].filter(e => {
                                const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0;
                            }).length,
                        })"""
                    )
                )
            except Exception:
                pass
            shot_name = f"{prefix}_{_now_tag()}.png"
            try:
                await page.screenshot(path=str(self._shots / shot_name))
            except Exception:
                shot_name = ""
            raise ActionError(f"{extra.get('reason') or '分享失败'}（{debug}）截图：/media/shots/{shot_name}")

        try:
            await page.mouse.move(600, 400)
            await page.wait_for_timeout(300)
        except Exception:
            pass
        # 顶部搜索框不能当作分享面板；只有面板真的开着才收起来，避免误关视频弹层。
        if await panel_count():
            try:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(400)
            except Exception:
                pass

        clicked = False
        for _ in range(2):
            clicked = await page.evaluate(
                """() => {
                    const visible = el => {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                    };
                    const inView = el => {
                        const r = el.getBoundingClientRect();
                        return r.right > 0 && r.left < innerWidth && r.bottom > 0 && r.top < innerHeight;
                    };
                    let els = [...document.querySelectorAll('[data-e2e="video-player-share"]')]
                        .filter(e => visible(e) && inView(e));
                    // 推荐流会预渲染相邻视频，必须锁定“当前激活视频”里的分享按钮。
                    const active = document.querySelector('[data-e2e="feed-active-video"]');
                    if (active) {
                        const inside = els.filter(e => active.contains(e));
                        if (inside.length) els = inside;
                    }
                    els.sort((a, b) => {
                        const ra = a.getBoundingClientRect();
                        const rb = b.getBoundingClientRect();
                        return Math.abs(ra.top + ra.height / 2 - innerHeight / 2)
                            - Math.abs(rb.top + rb.height / 2 - innerHeight / 2);
                    });
                    const el = els[0];
                    if (!el) return null;
                    let vid = '';
                    let node = el;
                    for (let i = 0; i < 12 && node; i++, node = node.parentElement) {
                        const holder = node.matches && node.matches('[data-e2e="feed-active-video"][data-e2e-vid]')
                            ? node
                            : (node.querySelector ? node.querySelector('[data-e2e="feed-active-video"][data-e2e-vid]') : null);
                        if (holder) { vid = holder.getAttribute('data-e2e-vid') || ''; break; }
                    }
                    if (!vid) {
                        const info = document.querySelector('[data-e2e="video-info"][data-e2e-aweme-id]');
                        vid = info ? (info.getAttribute('data-e2e-aweme-id') || '') : '';
                    }
                    el.click();
                    return { clicked: true, vid };
                }"""
            )
            if isinstance(clicked, dict):
                clicked_vid = str(clicked.get("vid") or "")
                clicked = bool(clicked.get("clicked"))
                if expected_id and clicked_vid and clicked_vid != expected_id:
                    raise ActionError(
                        f"分享按钮属于视频 {clicked_vid}，与介绍的 {expected_id} 不一致，已取消分享"
                    )
            if not clicked:
                break
            for _ in range(24):
                await page.wait_for_timeout(250)
                if await panel_count():
                    return await self._share_pick_contact(page, recipient, panel_count_js)
        if not clicked:
            await debug_shot("share_missing", {"reason": "没有找到视频分享按钮"})
        await debug_shot("share_panel_missing", {"reason": "分享面板没有打开"})

    async def _share_pick_contact(self, page: Page, recipient: str, panel_count_js: str) -> dict[str, Any]:
        inputs = page.locator('input[placeholder="搜索"]')
        chosen = None
        for i in range(await inputs.count()):
            try:
                loc = inputs.nth(i)
                if not await loc.is_visible():
                    continue
                if await loc.get_attribute("data-e2e") == "searchbar-input":
                    continue
                chosen = loc
            except Exception:
                continue
        if chosen is None:
            return {"status": "failed", "detail": "分享面板没有可用的联系人搜索框"}
        try:
            await chosen.fill(recipient)
            await page.wait_for_timeout(1200)
        except Exception:
            pass
        result = await page.evaluate(
            """(name) => {
                const inputs = [...document.querySelectorAll('input[placeholder="搜索"]')].filter(e => {
                    const r = e.getBoundingClientRect();
                    return r.width > 0 && r.height > 0
                        && e.getAttribute('data-e2e') !== 'searchbar-input'
                        && !e.closest('[data-e2e="searchbar"]');
                });
                const input = inputs[inputs.length - 1];
                let panel = null;
                let node = input ? input.parentElement : null;
                for (let i = 0; i < 10 && node; i++, node = node.parentElement) {
                    if ((node.innerText || '').includes('分享给朋友')) { panel = node; break; }
                }
                const scopes = panel ? [panel] : [document];
                for (const scope of scopes) {
                    const names = [...scope.querySelectorAll('span')]
                        .filter(el => (el.innerText || '').trim() === name);
                    for (const nameEl of names) {
                        let row = nameEl;
                        for (let i = 0; i < 8 && row; i++) {
                            row = row.parentElement;
                            if (!row) break;
                            const button = [...row.querySelectorAll('div,button,span')].find(el => {
                                const r = el.getBoundingClientRect();
                                return (el.innerText || '').trim() === '分享' && r.width > 0 && r.height > 0;
                            });
                            if (button) { button.click(); return { clicked: true, scoped: !!panel }; }
                        }
                    }
                }
                return { clicked: false, scoped: !!panel };
            }""",
            recipient,
        )
        clicked = bool((result or {}).get("clicked"))
        verified = False
        for _ in range(16):
            await page.wait_for_timeout(250)
            try:
                body = await page.evaluate("() => document.body ? document.body.innerText : ''")
            except Exception:
                body = ""
            if any(word in body for word in ("分享成功", "已分享", "发送成功")):
                verified = True
                break
            try:
                remaining = int(await page.evaluate(panel_count_js))
            except Exception:
                remaining = 1
            if clicked and remaining == 0:
                verified = True
                break
        if not clicked:
            return {"status": "failed", "detail": f"分享面板里没有找到「{recipient}」"}
        return {
            "status": "sent" if verified else "uncertain",
            "detail": "原生视频分享已发送" if verified else "已点击分享，但未读到成功提示",
        }
