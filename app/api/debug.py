"""选择器调试路由：导航红点、通知下拉、创作者中心评论页的现场检查。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..agent import runner
from ..browser import NAV_BADGES_JS
from ..config import MEDIA_DIR

router = APIRouter()


@router.get("/api/debug/nav")
async def debug_nav() -> dict[str, Any]:
    page = runner.browser.page
    if page is None or page.is_closed():
        return {"ok": False, "detail": "浏览器页面未就绪"}
    try:
        data = await page.evaluate(NAV_BADGES_JS)
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:200]}
    return {"ok": True, "url": page.url, "data": data}


@router.get("/api/debug/notices")
async def debug_notices(click: int = -1) -> dict[str, Any]:
    page = await runner.browser._new_fan_page()
    js = r"""
    () => {
      const clean = s => (s || '').replace(/\s+/g, ' ').trim();
      const root = document.querySelector('[data-e2e="listDlgTest-container"]');
      if (!root) return { ok: false, html: document.body ? document.body.innerHTML.slice(0, 800) : '' };
      const cards = [...root.querySelectorAll('[data-e2e="user-name-card"]')];
      const rows = cards.slice(0, 12).map((card, index) => {
        let row = card;
        let text = '';
        for (let i = 0; i < 8 && row && row.parentElement; i++) {
          row = row.parentElement;
          text = clean(row.innerText);
          if (text.length > 6 && (text.includes('回复') || text.includes('评论') || text.includes('赞了') || text.includes('关注了你'))) break;
          if (text.length > 600) break;
        }
        const badge = [...row.querySelectorAll('*')].filter(el => {
          const r = el.getBoundingClientRect();
          if (r.width <= 0 || r.height <= 0 || r.width > 20 || r.height > 20) return false;
          const s = getComputedStyle(el);
          const bg = s.backgroundColor.replace(/\s/g, '');
          const red = bg === 'rgb(254,44,85)' || bg === 'rgb(255,44,85)' || bg === 'rgb(255,45,85)';
          const cls = typeof el.className === 'string' ? el.className : '';
          return red || /badge|unread|dot|count/i.test(cls);
        }).slice(0, 5).map(el => {
          const r = el.getBoundingClientRect();
          return { cls: String(el.className).slice(0, 80), w: Math.round(r.width), h: Math.round(r.height), bg: getComputedStyle(el).backgroundColor, text: clean(el.innerText).slice(0, 10) };
        });
        const img = row.querySelector('img');
        row.setAttribute('data-dy-notice-row', String(index));
        return {
          index,
          text: text.slice(0, 200),
          avatarWrap: img && img.parentElement ? String(img.parentElement.className || '').slice(0, 80) : '',
          badges: badge,
        };
      });
      const actions = [...document.querySelectorAll('button,span,div')].filter(el => {
        const t = clean(el.innerText);
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && t && t.length <= 10 && /已读|清空|全部|标记|收起|设置/.test(t);
      }).map(el => {
        const r = el.getBoundingClientRect();
        return { text: clean(el.innerText).slice(0, 12), cls: String(el.className).slice(0, 60), w: Math.round(r.width), h: Math.round(r.height) };
      }).slice(0, 20);
      return { ok: true, count: cards.length, rows, actions };
    }
    """
    try:
        await page.goto("https://www.douyin.com/user/self", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3500)
        bells = page.locator('[data-e2e="something-button"]')
        bell_info: list[str] = []
        clicked = False
        for i in range(await bells.count()):
            try:
                label = ((await bells.nth(i).inner_text()) or "").strip()
                aria = (await bells.nth(i).get_attribute("aria-label")) or ""
                title = (await bells.nth(i).get_attribute("title")) or ""
            except Exception:
                continue
            bell_info.append("|".join([label, aria, title])[:80])
            if "通知" in label or "通知" in aria or "通知" in title:
                if await bells.nth(i).is_visible():
                    await bells.nth(i).click()
                    clicked = True
                    break
        if not clicked:
            clicked = bool(
                await page.evaluate(
                    """() => {
                        const nodes = [...document.querySelectorAll('[data-e2e="something-button"],a,button,div,span')]
                          .filter(el => {
                            const t = (el.innerText || '').trim();
                            const a = el.getAttribute('aria-label') || '';
                            return t === '通知' || a.includes('通知');
                          });
                        const el = nodes.find(n => { const r = n.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
                        if (!el) return false;
                        el.click();
                        return true;
                    }"""
                )
            )
        await page.wait_for_timeout(1500)
        data = await page.evaluate(js)
        data = dict(data or {})
        data["bells"] = bell_info[:10]
        if click >= 0:
            target = page.locator(f'[data-dy-notice-row="{click}"]').first
            try:
                await target.click(timeout=5000)
            except Exception:
                cards = page.locator('[data-e2e="user-name-card"]')
                if click < await cards.count():
                    await cards.nth(click).click()
            await page.wait_for_timeout(2200)
            try:
                await page.screenshot(path=str(MEDIA_DIR / "shots" / "debug_notice_click.png"))
            except Exception:
                pass
            ctx = page.context
            pages = [p.url for p in ctx.pages]
            modal_text = ""
            inputs: list[dict[str, Any]] = []
            try:
                modal_text = await page.evaluate(
                    """() => {
                        const nodes = [...document.querySelectorAll('[role="dialog"], .semi-modal, [class*="modal"]')]
                          .filter(el => { const r = el.getBoundingClientRect(); return r.width > 300 && r.height > 300; });
                        return nodes.length ? (nodes[nodes.length - 1].innerText || '').slice(0, 1000) : '';
                    }"""
                )
                inputs = await page.evaluate(
                    """() => [...document.querySelectorAll('textarea,input,[contenteditable="true"]')]
                      .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
                      .map(el => ({ tag: el.tagName, cls: String(el.className).slice(0, 80), ph: el.getAttribute('placeholder') || '', text: (el.innerText || '').slice(0, 60) }))
                      .slice(0, 10)"""
                )
            except Exception:
                pass
            data["after"] = {
                "url": page.url,
                "pages": pages,
                "text": (await page.inner_text("body"))[:800],
                "modal_text": modal_text,
                "inputs": inputs,
            }
            if len(ctx.pages) > 1:
                newest = ctx.pages[-1]
                try:
                    data["after"]["new_url"] = newest.url
                    data["after"]["new_text"] = (await newest.inner_text("body"))[:800]
                except Exception:
                    pass
        return {"ok": True, "clicked": clicked, "data": data}
    finally:
        try:
            await page.close()
        except Exception:
            pass


@router.get("/api/debug/creator-comments")
async def debug_creator_comments() -> dict[str, Any]:
    page = await runner.browser._new_fan_page()
    try:
        await page.goto(
            "https://creator.douyin.com/creator-micro/interaction/comment",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        await page.wait_for_timeout(5000)
        try:
            link = page.get_by_text("评论管理", exact=True).first
            if await link.count():
                await link.click(timeout=5000)
                await page.wait_for_timeout(4500)
        except Exception:
            pass
        try:
            await page.screenshot(path=str(MEDIA_DIR / "shots" / "debug_creator_comments.png"))
        except Exception:
            pass
        info = await page.evaluate(
            """() => {
                const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
                const visible = el => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                };
                const buttons = [...document.querySelectorAll('button,div,span,a')]
                    .filter(el => visible(el) && clean(el.innerText) && clean(el.innerText).length <= 8)
                    .map(el => {
                        const r = el.getBoundingClientRect();
                        return { text: clean(el.innerText), cls: String(el.className).slice(0, 70), w: Math.round(r.width), h: Math.round(r.height) };
                    })
                    .filter(x => /回复|评论|未读|全部|筛选|加载/.test(x.text))
                    .slice(0, 30);
                return {
                    url: location.href,
                    title: document.title,
                    text: (document.body ? document.body.innerText : '').slice(0, 2000),
                    buttons,
                };
            }"""
        )
        return {"ok": True, "data": info}
    finally:
        try:
            await page.close()
        except Exception:
            pass


@router.get("/api/debug/creator-reply")
async def debug_creator_reply() -> dict[str, Any]:
    page = await runner.browser._new_fan_page()
    try:
        await page.goto("https://creator.douyin.com/creator-micro/interactive/comment", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)
        try:
            link = page.get_by_text("评论管理", exact=True).first
            if await link.count():
                await link.click(timeout=5000)
                await page.wait_for_timeout(3000)
        except Exception:
            pass
        await page.evaluate(
            """() => {
                for (let round = 0; round < 3; round++) {
                    const more = [...document.querySelectorAll('div,span')].find(el => /^查看\\d+条回复$/.test((el.innerText || '').trim()) && el.getBoundingClientRect().width > 0);
                    if (!more) break;
                    more.click();
                }
            }"""
        )
        await page.wait_for_timeout(1500)
        before = await page.evaluate(
            """() => {
                const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
                const visible = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
                return {
                    replies: [...document.querySelectorAll('div,span')].filter(el => visible(el) && clean(el.innerText) === '回复').slice(0, 6).map(el => {
                        let row = el;
                        for (let i = 0; i < 6 && row; i++, row = row.parentElement) {
                            if (clean(row.innerText).length > 12) return { text: clean(row.innerText).slice(0, 160), cls: String(row.className).slice(0, 60) };
                        }
                        return { text: '', cls: '' };
                    }),
                    inputs: [...document.querySelectorAll('textarea,input,[contenteditable="true"]')].filter(visible).map(el => ({ tag: el.tagName, cls: String(el.className).slice(0, 70), ph: el.getAttribute('placeholder') || '' })),
                };
            }"""
        )
        try:
            await page.get_by_text("回复", exact=True).first.click(timeout=4000)
            await page.wait_for_timeout(1200)
        except Exception:
            pass
        after = await page.evaluate(
            """() => {
                const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
                const visible = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
                return {
                    inputs: [...document.querySelectorAll('textarea,input,[contenteditable="true"]')].filter(visible).map(el => ({ tag: el.tagName, cls: String(el.className).slice(0, 70), ph: el.getAttribute('placeholder') || '' })),
                    senders: [...document.querySelectorAll('button,div,span')].filter(el => visible(el) && clean(el.innerText) === '发送').map(el => String(el.className).slice(0, 70)),
                };
            }"""
        )
        try:
            await page.screenshot(path=str(MEDIA_DIR / "shots" / "debug_creator_reply.png"))
        except Exception:
            pass
        return {"ok": True, "before": before, "after": after, "url": page.url}
    finally:
        try:
            await page.close()
        except Exception:
            pass
