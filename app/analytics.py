from __future__ import annotations

import asyncio
import re
from typing import Any

from . import db
from .agent import runner
from .browser import ActionError, BrowserManager, LoginRequired
from .events import bus

PROFILE_URL = "https://www.douyin.com/user/self?showTab=post"

_METRIC_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(万|亿|w|W)?")


def parse_metric(text: str) -> int:
    raw = (text or "").strip().replace(",", "")
    match = _METRIC_RE.search(raw)
    if not match:
        return 0
    value = float(match.group(1))
    unit = (match.group(2) or "").lower()
    if unit in ("万", "w"):
        value *= 10000
    elif unit == "亿":
        value *= 100000000
    return int(round(value))


def _id_from_url(url: str) -> str:
    match = re.search(r"/(?:video|note)/(\d+)", url or "")
    return match.group(1) if match else ""


def _metric_from_text(text: str, labels: tuple[str, ...]) -> int:
    for label in labels:
        match = re.search(rf"{label}[^0-9]{{0,8}}([0-9]+(?:\.[0-9]+)?\s*(?:万|亿|w|W)?)", text or "")
        if match:
            return parse_metric(match.group(1))
        match = re.search(rf"([0-9]+(?:\.[0-9]+)?\s*(?:万|亿|w|W)?)[^0-9]{{0,6}}{label}", text or "")
        if match:
            return parse_metric(match.group(1))
    return 0


PROFILE_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const stat = (selectors) => {
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) {
        const t = clean(el.innerText);
        if (t) return t;
      }
    }
    return '';
  };
  const headerText = clean(document.body ? document.body.innerText.slice(0, 8000) : '');
  const links = [...document.querySelectorAll(
    '[data-e2e="user-post-list"] a[href*="/video/"], [data-e2e="user-post-list"] a[href*="/note/"]'
  )];
  const seen = new Set();
  const works = [];
  for (const a of links) {
    const url = a.href || '';
    if (!url || seen.has(url)) continue;
    seen.add(url);
    const img = a.querySelector('img');
    let card = a;
    for (let i = 0; i < 7 && card && card.parentElement; i++) {
      const text = clean(card.innerText);
      if (text.length > 8) break;
      card = card.parentElement;
    }
    const text = clean(card && card.innerText);
    const metrics = [...(card ? card.querySelectorAll('span, div, p') : [])].map(el => {
      const t = clean(el.innerText);
      const r = el.getBoundingClientRect();
      return {text: t, cls: String(el.className || '').slice(0, 70), x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)};
    }).filter(x => x.w > 0 && x.h > 0 && /^[0-9]+(?:\.[0-9]+)?\s*(?:万|亿|w|W)?$/.test(x.text));
    works.push({
      url,
      cover: ((img && (img.currentSrc || img.src)) || ''),
      title: clean((img && (img.alt || img.getAttribute('aria-label'))) || '') || text.slice(0, 120),
      text: text.slice(0, 500),
      metrics: metrics.slice(0, 8),
    });
  }
  return {
    headerText,
    followers: stat(['[data-e2e="user-info-fans"]', '.user-info-fans', '[class*="fans"]']),
    following: stat(['[data-e2e="user-info-follow"]', '.user-info-follow']),
    likes: stat(['[data-e2e="user-info-like"]', '.user-info-like']),
    works,
  };
}
"""

DETAIL_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const text = (selectors) => {
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) {
        const t = clean(el.innerText);
        if (t) return t;
      }
    }
    return '';
  };
  return {
    like: text(['[data-e2e="video-player-digg"]', '[data-e2e="like-count"]']),
    comment: text(['[data-e2e="video-player-comment"]', '[data-e2e="comment-count"]']),
    collect: text(['[data-e2e="video-player-collect"]', '[data-e2e="collect-count"]']),
    share: text(['[data-e2e="video-player-share"]', '[data-e2e="share-count"]']),
    title: text(['[data-e2e="video-desc"]', 'h1']),
  };
}
"""


class AnalyticsService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._last_refresh = ""
        self._last_error = ""
        self._last_result: dict[str, Any] = {}

    def status(self) -> dict[str, Any]:
        return {
            "busy": self._lock.locked(),
            "last_refresh": self._last_refresh,
            "last_error": self._last_error,
            "last_result": self._last_result,
        }

    async def refresh(self, *, detail: bool = False, limit: int = 30) -> dict[str, Any]:
        if self._lock.locked():
            raise ActionError("正在刷新数据，请稍等")
        async with self._lock:
            browser: BrowserManager = runner.browser
            await browser.ensure_started()
            if not await browser.is_logged_in():
                raise LoginRequired("浏览器还没有登录抖音")
            page = await browser.new_page()
            page.set_default_timeout(20000)
            try:
                await page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(3000)
                for _ in range(3):
                    try:
                        await page.mouse.wheel(0, 900)
                    except Exception:
                        pass
                    await page.wait_for_timeout(900)
                raw = await page.evaluate(PROFILE_JS)
                header = str(raw.get("headerText") or "")
                followers = parse_metric(str(raw.get("followers") or "")) or _metric_from_text(header, ("粉丝",))
                following = parse_metric(str(raw.get("following") or "")) or _metric_from_text(header, ("关注",))
                likes = parse_metric(str(raw.get("likes") or "")) or _metric_from_text(header, ("获赞",))
                works_raw = raw.get("works") or []
                collected: list[dict[str, Any]] = []
                for item in works_raw[: max(1, min(int(limit or 30), 100))]:
                    if not isinstance(item, dict):
                        continue
                    url = str(item.get("url") or "")
                    aweme_id = _id_from_url(url)
                    if not aweme_id:
                        continue
                    text = str(item.get("text") or "")
                    work = {
                        "aweme_id": aweme_id,
                        "title": str(item.get("title") or "")[:500],
                        "cover": str(item.get("cover") or ""),
                        "url": url,
                        "kind": "note" if "/note/" in url else "video",
                        "published_at": "",
                    }
                    db.upsert_work(work)
                    metrics = item.get("metrics") or []
                    metric_text = ""
                    if isinstance(metrics, list) and metrics:
                        first = metrics[0] if isinstance(metrics[0], dict) else {}
                        metric_text = str(first.get("text") or "")
                    plays = parse_metric(metric_text) or _metric_from_text(text, ("播放",))
                    stats = {
                        "plays": plays,
                        "likes": 0,
                        "comments": 0,
                        "shares": 0,
                        "favorites": 0,
                        "raw": {"card": text[:500], "metrics": metrics[:8]},
                    }
                    detail_stats = await self._read_detail(browser, url) if detail else {}
                    if detail_stats:
                        stats.update(detail_stats)
                    else:
                        previous = db.latest_work_stats(aweme_id)
                        if previous:
                            for key in ("likes", "comments", "shares", "favorites"):
                                stats[key] = int(previous.get(key) or 0)
                            stats["raw"]["carried_from"] = previous.get("day")
                    db.save_work_stats(aweme_id, stats)
                    collected.append({**work, **stats})
                db.save_account_stats(
                    {
                        "followers": followers,
                        "following": following,
                        "likes": likes,
                        "works": len(works_raw),
                        "raw": {"header": header[:1000]},
                    }
                )
                self._last_refresh = db.now_iso()
                self._last_error = ""
                self._last_result = {
                    "works": len(collected),
                    "followers": followers,
                    "following": following,
                    "likes": likes,
                }
                await bus.emit(
                    "analytics",
                    f"作品数据已刷新：{len(collected)} 条作品，粉丝 {followers}",
                    self._last_result,
                )
                return {"ok": True, **self._last_result, "items": collected}
            except Exception as exc:
                self._last_error = str(exc)[:300]
                await bus.emit("analytics", f"作品数据刷新失败：{exc}", {}, level="warn")
                raise
            finally:
                try:
                    await page.close()
                except Exception:
                    pass

    async def _read_detail(self, browser: BrowserManager, url: str) -> dict[str, Any]:
        page = await browser.new_page()
        page.set_default_timeout(15000)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2200)
            raw = await page.evaluate(DETAIL_JS)
            return {
                "likes": parse_metric(str(raw.get("like") or "")),
                "comments": parse_metric(str(raw.get("comment") or "")),
                "favorites": parse_metric(str(raw.get("collect") or "")),
                "shares": parse_metric(str(raw.get("share") or "")),
                "raw": raw,
            }
        except Exception:
            return {}
        finally:
            try:
                await page.close()
            except Exception:
                pass


analytics = AnalyticsService()
