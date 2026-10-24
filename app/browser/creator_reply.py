"""自己作品下的评论回复：打开作品页、定位那条评论、填写并发送回复。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from playwright.async_api import Page

from .. import selectors
from .core import _now_tag


class CreatorReplyMixin:
    """评论通知驱动的一次回复；由 replier 在收到通知后调用。"""

    _shots: Path

    async def reply_comment_on_work(
        self,
        work_url: str,
        author: str,
        comment: str,
        reply_text: str,
        page: Page | None = None,
    ) -> dict[str, Any]:
        own_page = page is None
        if page is None:
            page = await self.new_page()
        page.set_default_timeout(20000)
        shot_name = f"reply_comment_{_now_tag()}.png"
        shot_path = self._shots / shot_name
        try:
            if own_page:
                await page.goto(work_url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(3000)
            else:
                await page.wait_for_timeout(2200)
            panel_open = False
            try:
                panel_open = bool(
                    await page.evaluate(
                        """() => !!document.querySelector('[data-e2e="comment-item"], .public-DraftEditor-content, #comment-input-container, .comment-input-container')"""
                    )
                )
            except Exception:
                panel_open = False
            if not panel_open:
                tab_box = None
                try:
                    tab_box = await page.evaluate(
                        """() => {
                            const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
                            const visible = el => {
                                const r = el.getBoundingClientRect();
                                const s = getComputedStyle(el);
                                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                            };
                            const related = [...document.querySelectorAll('div,span')].find(el => {
                                return visible(el) && clean(el.innerText) === '相关推荐' && el.getBoundingClientRect().left > innerWidth * 0.5;
                            });
                            const anchorY = related ? (related.getBoundingClientRect().top + related.getBoundingClientRect().height / 2) : null;
                            const tabs = [...document.querySelectorAll('div,span')].filter(el => {
                                const t = clean(el.innerText);
                                const r = el.getBoundingClientRect();
                                if (!visible(el) || !/^评论(\(\d+\))?$/.test(t) || r.left < innerWidth * 0.5) return false;
                                if (anchorY === null) return true;
                                return Math.abs((r.top + r.height / 2) - anchorY) < 18;
                            }).sort((a, b) => {
                                const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
                                return ra.width * ra.height - rb.width * rb.height;
                            });
                            if (!tabs[0]) return null;
                            const r = tabs[0].getBoundingClientRect();
                            return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
                        }"""
                    )
                except Exception:
                    tab_box = None
                if tab_box:
                    try:
                        await page.evaluate(
                            "() => { const h = document.querySelector('#douyin-header'); if (h) h.style.pointerEvents = 'none'; }"
                        )
                        try:
                            await page.mouse.click(
                                tab_box["x"] + tab_box["w"] / 2,
                                tab_box["y"] + tab_box["h"] / 2,
                            )
                        finally:
                            await page.evaluate(
                                "() => { const h = document.querySelector('#douyin-header'); if (h) h.style.pointerEvents = ''; }"
                            )
                        await page.wait_for_timeout(1600)
                        panel_open = bool(
                            await page.evaluate(
                                """() => {
                        const text = document.body ? document.body.innerText : '';
                        return !!document.querySelector('[data-e2e="comment-item"], .public-DraftEditor-content, #comment-input-container, .comment-input-container, [data-e2e="comment-list"]')
                            || text.includes('全部评论') || text.includes('留下你的精彩评论');
                    }"""
                            )
                        )
                    except Exception:
                        panel_open = False
                if not panel_open:
                    try:
                        parent_clicked = bool(
                            await page.evaluate(
                                """() => {
                                    const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
                                    const visible = el => {
                                        const r = el.getBoundingClientRect();
                                        const s = getComputedStyle(el);
                                        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                                    };
                                    const related = [...document.querySelectorAll('div,span')].find(el => visible(el) && clean(el.innerText) === '相关推荐' && el.getBoundingClientRect().left > innerWidth * 0.5);
                                    const anchorY = related ? (related.getBoundingClientRect().top + related.getBoundingClientRect().height / 2) : null;
                                    const tab = [...document.querySelectorAll('div,span')].find(el => {
                                        const t = clean(el.innerText);
                                        const r = el.getBoundingClientRect();
                                        if (!visible(el) || !/^评论(\\(\\d+\\))?$/.test(t) || r.left < innerWidth * 0.5) return false;
                                        return anchorY === null || Math.abs((r.top + r.height / 2) - anchorY) < 18;
                                    });
                                    if (!tab) return false;
                                    const target = tab.parentElement || tab;
                                    target.click();
                                    return true;
                                }"""
                            )
                        )
                        if parent_clicked:
                            await page.wait_for_timeout(1600)
                            panel_open = bool(
                                await page.evaluate(
                                    """() => {
                                        const text = document.body ? document.body.innerText : '';
                                        return !!document.querySelector('[data-e2e="comment-item"], .public-DraftEditor-content, #comment-input-container, .comment-input-container, [data-e2e="comment-list"]')
                                            || text.includes('全部评论') || text.includes('留下你的精彩评论');
                                    }"""
                                )
                            )
                    except Exception:
                        panel_open = False
                if not panel_open:
                    try:
                        tab = page.get_by_text(re.compile(r"^评论\(\d+\)$")).first
                        if await tab.count():
                            await tab.click(timeout=5000)
                            await page.wait_for_timeout(1600)
                            panel_open = bool(
                                await page.evaluate(
                                    """() => {
                        const text = document.body ? document.body.innerText : '';
                        return !!document.querySelector('[data-e2e="comment-item"], .public-DraftEditor-content, #comment-input-container, .comment-input-container, [data-e2e="comment-list"]')
                            || text.includes('全部评论') || text.includes('留下你的精彩评论');
                    }"""
                                )
                            )
                    except Exception:
                        panel_open = False
                if not panel_open:
                    # 兜底：视频右侧操作栏的评论气泡（相对位置）
                    try:
                        size = await page.evaluate("() => ({ w: innerWidth, h: innerHeight })")
                        await page.mouse.click(size["w"] - 421, size["h"] * 0.633)
                        await page.wait_for_timeout(1600)
                        panel_open = bool(
                            await page.evaluate(
                                """() => {
                        const text = document.body ? document.body.innerText : '';
                        return !!document.querySelector('[data-e2e="comment-item"], .public-DraftEditor-content, #comment-input-container, .comment-input-container, [data-e2e="comment-list"]')
                            || text.includes('全部评论') || text.includes('留下你的精彩评论');
                    }"""
                            )
                        )
                    except Exception:
                        panel_open = False
            if not panel_open:
                for sel in (
                    '[data-e2e="feed-comment-icon"]',
                    '[data-e2e="comment-icon"]',
                    '[data-e2e="video-comment"]',
                ):
                    loc = page.locator(sel)
                    for i in range(await loc.count()):
                        try:
                            if not await loc.nth(i).is_visible():
                                continue
                            box = await loc.nth(i).bounding_box()
                            if not box:
                                continue
                            await page.evaluate(
                                "() => { const h = document.querySelector('#douyin-header'); if (h) h.style.pointerEvents = 'none'; }"
                            )
                            try:
                                await page.mouse.click(
                                    box["x"] + box["width"] / 2,
                                    box["y"] + box["height"] / 2,
                                )
                            finally:
                                await page.evaluate(
                                    "() => { const h = document.querySelector('#douyin-header'); if (h) h.style.pointerEvents = ''; }"
                                )
                            opened = True
                            break
                        except Exception:
                            continue
                    if opened:
                        break
            else:
                opened = True
            if not opened:
                return {"status": "failed", "detail": "没有打开评论面板"}
            await page.wait_for_timeout(1600)

            # 展开“展开N条回复”，让嵌套回复进入 DOM 再定位。
            for _ in range(4):
                expanded = await page.evaluate(
                    """() => {
                        const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
                        const candidates = [...document.querySelectorAll('span,div')].filter(el => {
                            const t = clean(el.innerText);
                            const r = el.getBoundingClientRect();
                            return r.width > 0 && r.height > 0 && t.startsWith('展开') && t.includes('回复');
                        });
                        const el = candidates[0];
                        if (!el) return false;
                        el.click();
                        return true;
                    }"""
                )
                if not expanded:
                    break
                await page.wait_for_timeout(650)

            target = await page.evaluate(
                """([author, comment]) => {
                  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
                  const visible = el => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                  };
                  const mark = el => {
                    el.setAttribute('data-dy-agent-reply', '1');
                    const r = el.getBoundingClientRect();
                    return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
                  };
                  // 先找到“评论文字”本身，再从它最近的祖先行里找回复按钮，
                  // 这样嵌套回复不会误点到父评论的“回复”。
                  const textNodes = [...document.querySelectorAll('span,div,p')].filter(el => {
                    const t = clean(el.innerText);
                    if (!t || !t.includes(comment) || !visible(el)) return false;
                    return ![...el.children].some(c => clean(c.innerText) === t);
                  }).sort((a, b) => {
                    const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
                    return ra.width * ra.height - rb.width * rb.height;
                  });
                  for (const textEl of textNodes.slice(0, 3)) {
                    let row = textEl;
                    for (let i = 0; i < 8 && row; i++, row = row.parentElement) {
                      const controls = [...row.querySelectorAll('span,div')].filter(el => clean(el.innerText) === '回复' && visible(el));
                      if (controls.length) return mark(controls[controls.length - 1]);
                      const r = row.getBoundingClientRect();
                      if (r.width > 900) break;
                    }
                  }
                  const controls = [...document.querySelectorAll('span,div')].filter(el => clean(el.innerText) === '回复' && visible(el));
                  const hit = controls.find(el => {
                    const item = el.closest('[data-e2e="comment-item"]');
                    const text = ((item && item.innerText) || '').replace(/\\s+/g, ' ');
                    return text.includes(author) && (!comment || text.includes(comment));
                  }) || controls[0];
                  return hit ? mark(hit) : null;
                }""",
                [author, comment],
            )
            if not comment:
                target = None
            if not target and comment:
                try:
                    await page.screenshot(path=str(shot_path))
                except Exception:
                    pass
                return {"status": "failed", "detail": f"没有找到这条评论的回复按钮。截图：/media/shots/{shot_name}"}
            if target:
                await page.evaluate(
                    "() => { const h = document.querySelector('#douyin-header'); if (h) h.style.pointerEvents = 'none'; }"
                )
                try:
                    await page.mouse.click(target["x"] + target["w"] / 2, target["y"] + target["h"] / 2)
                finally:
                    await page.evaluate(
                        "() => { const h = document.querySelector('#douyin-header'); if (h) h.style.pointerEvents = ''; }"
                    )
                await page.wait_for_timeout(800)

            editor = None
            editors = page.locator('.public-DraftEditor-content')
            for i in range(await editors.count()):
                if await editors.nth(i).is_visible():
                    editor = editors.nth(i)
                    break
            if editor is None:
                for sel in (
                    '#comment-input-container [contenteditable="true"]',
                    '.comment-input-container [contenteditable="true"]',
                    'textarea',
                ):
                    loc = page.locator(sel)
                    for i in range(await loc.count()):
                        if await loc.nth(i).is_visible():
                            editor = loc.nth(i)
                            break
                    if editor is not None:
                        break
            if editor is None:
                return {"status": "failed", "detail": "没有找到回复输入框"}
            try:
                await editor.click(timeout=4000)
                await page.keyboard.insert_text(reply_text)
                await page.wait_for_timeout(700)
            except Exception as exc:
                return {"status": "failed", "detail": f"填写回复失败：{str(exc)[:160]}"}

            send = await self._find_visible_in(page, selectors.COMMENT_SEND)
            if not send:
                await page.evaluate(
                    """() => {
                      const spans = [...document.querySelectorAll('span, button')].filter(el => {
                        const r = el.getBoundingClientRect();
                        if (r.width < 8 || r.height < 8) return false;
                        const html = el.outerHTML || '';
                        return html.includes('#FE2C55') &&
                          !!el.closest('#comment-input-container, .comment-input-container, .commentInput-right-ct');
                      });
                      const el = spans[spans.length - 1];
                      if (el) el.setAttribute('data-dy-agent-send', '1');
                    }"""
                )
                if await page.locator('[data-dy-agent-send="1"]').count():
                    send = page.locator('[data-dy-agent-send="1"]').first
            if not send:
                await page.screenshot(path=str(shot_path))
                return {"status": "failed", "detail": f"没有找到发送按钮。截图：/media/shots/{shot_name}"}
            try:
                await send.click(timeout=5000)
            except Exception as exc:
                await page.screenshot(path=str(shot_path))
                return {"status": "failed", "detail": f"点击发送失败：{str(exc)[:160]}"}
            await page.wait_for_timeout(1700)
            try:
                await page.screenshot(path=str(shot_path))
            except Exception:
                pass
            verified = False
            try:
                body = await page.evaluate("() => document.body ? document.body.innerText : ''")
                verified = reply_text[:12] in body
            except Exception:
                pass
            return {
                "status": "sent" if verified else "uncertain",
                "detail": "评论回复已发送" if verified else "已点击发送，但未读到确认",
                "screenshot": f"/media/shots/{shot_name}",
            }
        except Exception as exc:
            try:
                await page.screenshot(path=str(shot_path))
            except Exception:
                pass
            return {
                "status": "failed",
                "detail": f"评论回复失败：{str(exc)[:200]}",
                "screenshot": f"/media/shots/{shot_name}",
            }
        finally:
            if own_page and not page.is_closed():
                try:
                    await page.close()
                except Exception:
                    pass
