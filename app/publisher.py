from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import Page

from . import compliance, content, db
from .agent import runner
from .browser import BrowserError, LoginRequired
from .events import bus

UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"
SHOTS_DIR = Path(__file__).resolve().parent.parent / "data" / "media" / "shots"

_TITLE_SELECTORS = (
    'input[placeholder*="标题"]',
    'textarea[placeholder*="标题"]',
    'input[placeholder*="作品标题"]',
    '[contenteditable="true"][data-placeholder*="标题"]',
)
_CAPTION_SELECTORS = (
    '[contenteditable="true"][data-placeholder*="简介"]',
    '[contenteditable="true"][data-placeholder*="描述"]',
    'div[contenteditable="true"]',
)
_PUBLISH_SELECTORS = (
    'button:has-text("发布")',
    '[role="button"]:has-text("发布")',
)
_COVER_BUTTONS = ("选择封面", "更换封面", "编辑封面")
_COVER_CONFIRM = ("确定", "完成", "保存")


def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]


class ContentPublisher:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.last_error = ""
        self.last_result: dict[str, Any] = {}
        self.busy = False
        self._manual_page: Page | None = None

    @property
    def running(self) -> bool:
        return bool(self._task and not self._task.done())

    def status(self) -> dict[str, Any]:
        settings = db.load_settings()
        return {
            "enabled": bool(settings.auto_operate and settings.auto_publish),
            "running": self.running,
            "busy": self.busy or self._lock.locked(),
            "last_error": self.last_error,
            "last_result": self.last_result,
        }

    async def open_creator(self, *, keep_open: bool = False, show: bool = False) -> dict[str, Any]:
        browser = runner.browser
        await browser.ensure_started()
        if not await browser.is_logged_in():
            raise LoginRequired("浏览器还没有登录抖音")
        if self._manual_page and not self._manual_page.is_closed():
            try:
                await self._manual_page.close()
            except Exception:
                pass
            self._manual_page = None
        page = await browser.new_page()
        page.set_default_timeout(25000)
        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        shot = SHOTS_DIR / f"creator_center_{_now_tag()}.png"
        try:
            await page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(3500)
            await page.screenshot(path=str(shot))
            if show:
                await page.bring_to_front()
                await browser.set_window_visible(True)
            if keep_open:
                self._manual_page = page
            else:
                await page.close()
            return {
                "ok": True,
                "url": page.url,
                "title": await page.title() if not page.is_closed() else "抖音创作者中心",
                "screenshot": f"/media/shots/{shot.name}",
                "kept_open": keep_open,
                "visible": show,
            }
        except Exception:
            try:
                await page.close()
            except Exception:
                pass
            raise

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                settings = db.load_settings()
                enabled = settings.auto_operate and settings.auto_publish
                if enabled and not self._lock.locked():
                    due = db.due_content_plans()
                    for plan in due[:2]:
                        if self._stop.is_set():
                            break
                        try:
                            await self.publish(plan, dry_run=False)
                        except Exception as exc:
                            self.last_error = str(exc)[:300]
                            await bus.emit(
                                "publish",
                                f"定时发布失败：{plan.get('topic') or plan.get('id')} · {exc}",
                                {"plan_id": plan.get("id")},
                                level="error",
                            )
                delay = 20 if enabled else 60
            except Exception as exc:
                self.last_error = str(exc)[:300]
                delay = 60
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def publish(self, plan: dict[str, Any], *, dry_run: bool = True) -> dict[str, Any]:
        if self._lock.locked():
            raise BrowserError("已经有发布任务在跑")
        async with self._lock:
            self.busy = True
            try:
                async with runner.exclusive:
                    result = await self._publish(plan, dry_run=dry_run)
                self.last_result = result
                self.last_error = "" if result.get("status") in ("published", "dry_run") else result.get("detail", "")
                return result
            finally:
                self.busy = False

    async def _publish(self, plan: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
        plan_id = int(plan.get("id") or 0)
        prepared = await content.prepare_publish(plan)
        video: Path = prepared["video"]
        report = prepared["compliance"]
        if report.get("blocking"):
            raise ValueError("合规检查未通过")
        if dry_run or db.load_settings().dry_run:
            return {"status": "dry_run", "detail": "预演模式，未上传/发布"}
        if not dry_run:
            db.update_content_plan(plan_id, status="publishing", error="")
            plan = db.get_content_plan(plan_id) or plan
        browser = runner.browser
        await browser.ensure_started()
        if not await browser.is_logged_in():
            raise LoginRequired("浏览器还没有登录抖音")
        page = await browser.new_page()
        page.set_default_timeout(25000)
        shot = SHOTS_DIR / f"publish_{plan_id}_{_now_tag()}.png"
        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            await page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(3500)
            file_input = page.locator('input[type="file"]').first
            if not await file_input.count():
                await page.screenshot(path=str(shot))
                return {
                    "status": "failed",
                    "detail": "创作者中心没有找到上传控件，可能未登录或页面改版",
                    "screenshot": f"/media/shots/{shot.name}",
                }
            await file_input.set_input_files(str(video))
            ready = await self._wait_editor(page)
            await page.screenshot(path=str(shot))
            if not ready:
                return {
                    "status": "failed",
                    "detail": "视频上传后没有出现发布编辑器（可能还在审核/转码）",
                    "screenshot": f"/media/shots/{shot.name}",
                }
            filled = await self._fill_metadata(page, plan)
            cover = await self._upload_cover(page, plan)
            await page.screenshot(path=str(shot))
            if dry_run:
                return {
                    "status": "dry_run",
                    "detail": f"已进入发布编辑器，未点击发布；标题字段 {filled}；封面 {'已替换' if cover else '未替换'}",
                    "screenshot": f"/media/shots/{shot.name}",
                }
            publish_button = await self._find_button(page, _PUBLISH_SELECTORS)
            if publish_button is None:
                return {
                    "status": "failed",
                    "detail": "没有找到发布按钮",
                    "screenshot": f"/media/shots/{shot.name}",
                }
            await publish_button.click()
            await page.wait_for_timeout(3000)
            body = ""
            try:
                body = await page.evaluate("() => document.body ? document.body.innerText : ''")
            except Exception:
                pass
            success = any(word in body for word in ("发布成功", "审核中", "作品已发布"))
            if not success:
                await page.screenshot(path=str(shot))
                return {
                    "status": "uncertain",
                    "detail": "已点击发布，但没有读到明确的成功提示，请到创作者中心确认",
                    "screenshot": f"/media/shots/{shot.name}",
                }
            await page.screenshot(path=str(shot))
            aweme_id = ""
            try:
                links = await page.evaluate(
                    "() => [...document.querySelectorAll('a[href*=\"/video/\"]')].map(a => a.href).slice(0, 5)"
                )
                for link in links or []:
                    match = __import__("re").search(r"/video/(\\d+)", str(link))
                    if match:
                        aweme_id = match.group(1)
                        break
            except Exception:
                pass
            action_id = db.add_action(
                kind="publish",
                target_name=str(plan.get("topic") or ""),
                content=str(plan.get("caption") or ""),
                status="sent",
                policy="定时发布",
            )
            db.update_action(action_id, result="发布成功", screenshot=f"/media/shots/{shot.name}", executed_at=db.now_iso())
            db.update_content_plan(
                plan_id,
                status="published",
                aweme_id=aweme_id,
                error="",
            )
            await bus.emit(
                "publish",
                f"作品已发布：{plan.get('topic') or plan_id}",
                {"plan_id": plan_id, "aweme_id": aweme_id, "screenshot": f"/media/shots/{shot.name}"},
            )
            return {
                "status": "published",
                "detail": "发布成功",
                "aweme_id": aweme_id,
                "screenshot": f"/media/shots/{shot.name}",
            }
        except Exception as exc:
            try:
                await page.screenshot(path=str(shot))
            except Exception:
                pass
            db.update_content_plan(plan_id, status="failed", error=str(exc)[:300])
            return {
                "status": "failed",
                "detail": str(exc)[:300],
                "screenshot": f"/media/shots/{shot.name}" if shot.exists() else "",
            }
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def _wait_editor(self, page: Page) -> bool:
        for _ in range(40):
            await page.wait_for_timeout(750)
            try:
                has_publish = await page.locator('button:has-text("发布"), [role="button"]:has-text("发布")').count()
                has_editor = await page.locator('div[contenteditable="true"], input[placeholder*="标题"]').count()
                if has_publish and has_editor:
                    return True
            except Exception:
                pass
        return False

    async def _fill_metadata(self, page: Page, plan: dict[str, Any]) -> str:
        filled: list[str] = []
        title = str(plan.get("cover_text") or plan.get("topic") or "")[:30]
        caption = str(plan.get("caption") or "").strip()
        tags = plan.get("hashtags") or []
        if tags:
            caption = (caption + " " + " ".join(f"#{str(t).strip().lstrip('#')}" for t in tags)).strip()
        for sel in _TITLE_SELECTORS:
            try:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible():
                    await loc.fill(title)
                    filled.append("标题")
                    break
            except Exception:
                continue
        for sel in _CAPTION_SELECTORS:
            try:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible():
                    await loc.click()
                    await page.keyboard.insert_text(caption)
                    filled.append("正文")
                    break
            except Exception:
                continue
        return "、".join(filled) or "未识别"

    async def _upload_cover(self, page: Page, plan: dict[str, Any]) -> bool:
        cover_rel = str(plan.get("cover_path") or "")
        cover = content.media_path(cover_rel)
        if cover is None or not cover.exists():
            return False
        button = await self._find_button(page, tuple(f'button:has-text("{x}")' for x in _COVER_BUTTONS))
        if button is None:
            return False
        try:
            await button.click()
            await page.wait_for_timeout(1400)
            input_loc = page.locator('input[type="file"]')
            count = await input_loc.count()
            if count == 0:
                return False
            await input_loc.nth(count - 1).set_input_files(str(cover))
            await page.wait_for_timeout(1600)
            confirm = await self._find_button(page, tuple(f'button:has-text("{x}")' for x in _COVER_CONFIRM))
            if confirm is not None:
                await confirm.click()
                await page.wait_for_timeout(1200)
            return True
        except Exception:
            return False

    async def _find_button(self, page: Page, selectors: tuple[str, ...]):
        for sel in selectors:
            try:
                loc = page.locator(sel)
                for i in range(await loc.count()):
                    item = loc.nth(i)
                    if await item.is_visible():
                        return item
            except Exception:
                continue
        return None


publisher = ContentPublisher()
