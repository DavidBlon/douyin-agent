"""BrowserCore：实例状态、Playwright 生命周期、窗口管理、登录/风控和通用元素查找。

各功能域 mixin（feed / creator_reply / inbox / share / fans / notices）只引用
在 ``BrowserCore.__init__`` 初始化的状态，自己不建状态。
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright, BrowserContext, Page, Playwright

from ..config import DEBUG_DIR, MEDIA_DIR, PROFILE_DIR, AgentSettings, env
from .errors import BrowserError, LoginRequired, RiskControl
from .js import RISK_JS


def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]



class BrowserCore:
    """专用 Chrome 的持有者：启动/关闭、页面与上下文、静默窗口、登录态与风控。"""

    def __init__(
        self,
        settings: AgentSettings,
        *,
        start_url: str = "",
        headless: bool | None = None,
        profile_dir: Path | None = None,
    ) -> None:
        self.settings = settings
        self.start_url = start_url or env.mock_url or ""
        self.headless = env.headless if headless is None else headless
        self.profile_dir = profile_dir or PROFILE_DIR
        self._pw: Playwright | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.activity_page: Page | None = None
        self.activity_at = 0.0
        self._fan_ctx: BrowserContext | None = None
        self.last_error = ""
        self._chrome_pids: set[int] = set()
        self._watch_task: asyncio.Task | None = None
        self._stay_visible = False
        self._shots = MEDIA_DIR / "shots"
        self._shots.mkdir(parents=True, exist_ok=True)

    @property
    def is_mock(self) -> bool:
        return bool(self.start_url and not self.start_url.startswith("http"))

    async def ensure_started(self) -> None:
        if self.context and self.page and not self.page.is_closed():
            return
        if self.context:
            try:
                await self.context.cookies("https://www.douyin.com")
            except Exception:
                self.context = None
                self.page = None
        await self.start()

    async def ensure_feed(self) -> None:
        """If the current tab is not a video feed/detail, open the configured feed."""
        if self.is_mock:
            return
        page = await self._ensure_page()
        try:
            ready = await page.evaluate(
                """() => !!document.querySelector(
                    '[data-e2e="feed-active-video"] video, [data-e2e="video-info"][data-e2e-aweme-id]'
                )"""
            )
        except Exception:
            ready = False
        if ready:
            return
        target = self.settings.feed_url or "https://www.douyin.com/?recommend=1&from_nav=1"
        try:
            await page.goto(target, wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:
            self.last_error = f"打开推荐流失败：{exc}"
        await page.wait_for_timeout(2500)
        try:
            await page.mouse.move(600, 400)
            await page.wait_for_timeout(300)
        except Exception:
            pass

    async def start(self, show: bool | None = None) -> None:
        if self.context:
            if show is not None:
                self._stay_visible = show
                await self._window_mode("show" if show else "hide")
                if show:
                    await self._focus_window()
            return
        self._pw = await async_playwright().start()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        visible = (not self.settings.silent_browser) if show is None else show
        launch_args = [
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
        ]
        if not visible:
            # 先画到屏幕外，再隐藏；避免默认位置启动时闪一下。
            launch_args += ["--window-position=-32000,-32000", "--window-size=1440,920"]
        params = dict(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            viewport={"width": 1440, "height": 920},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            args=launch_args,
        )
        channel = env.chrome_channel.strip()
        print(
            f"[browser] launch headless={self.headless} channel={channel or 'bundled'} profile={self.profile_dir}",
            flush=True,
        )
        try:
            self.context = await self._pw.chromium.launch_persistent_context(
                channel=channel or None, **params
            )
        except Exception as first:
            try:
                self.context = await self._pw.chromium.launch_persistent_context(**params)
            except Exception as second:
                await self._pw.stop()
                self._pw = None
                self.last_error = f"启动 Chrome 失败：{second}"
                raise BrowserError(
                    "启动 Chrome 失败。请确认已安装 Google Chrome，"
                    "或在 .env 里把 CHROME_CHANNEL 改成可用的通道。"
                    f"（{second} / {first}）"
                ) from second

        print(f"[browser] context pages={len(self.context.pages)}", flush=True)
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        self.page.set_default_timeout(15000)
        target = self.start_url or self.settings.feed_url
        try:
            await self.page.goto(target, wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:
            self.last_error = f"打开页面失败：{exc}"
        await self.page.wait_for_timeout(2200)
        if self.page.is_closed():
            await self.close()
            raise BrowserError("Chrome 启动了，但页面立即关闭；请查看 data/server.log。")
        try:
            await self.page.bring_to_front()
            await self.page.evaluate("1")
        except Exception as exc:
            await self.close()
            raise BrowserError(f"Chrome 页面不可用：{exc}") from exc
        visible = (not self.settings.silent_browser) if show is None else show
        self._stay_visible = visible
        if visible:
            await self._window_mode("show")
            await self._focus_window()
        else:
            await self._window_mode("hide")
        self.last_error = ""
        if os.name == "nt" and not self._watch_task:
            self._watch_task = asyncio.create_task(self._silent_watch())
        print(f"[browser] ready url={self.page.url} pages={len(self.context.pages)} silent={not visible}", flush=True)

    async def close(self) -> None:
        task = self._watch_task
        self._watch_task = None
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._stay_visible = False
        self._chrome_pids.clear()
        try:
            if self._fan_ctx:
                await self._fan_ctx.close()
        except Exception:
            pass
        self._fan_ctx = None
        try:
            if self.context:
                await self.context.close()
        except Exception:
            pass
        self.context = None
        self.page = None
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
        self._pw = None

    async def _ensure_page(self) -> Page:
        await self.ensure_started()
        assert self.page is not None
        if self.page.is_closed():
            self.page = await self.new_page()
        return self.page

    async def new_page(self, track: bool = True) -> Page:
        if not self.context:
            raise BrowserError("浏览器上下文不可用")
        page = await self.context.new_page()
        page.set_default_timeout(15000)
        if track:
            self.activity_page = page
            self.activity_at = time.monotonic()
        if self.settings.silent_browser and not self._stay_visible:
            if not self._chrome_pids:
                await self._window_mode("hide")
            else:
                await asyncio.sleep(0.15)
                self._hide_windows_now()
        return page

    async def _fan_context(self) -> BrowserContext:
        """粉丝/回关专用的独立上下文，共享登录 Cookie，不和刷流页面抢状态。"""
        if not self.context:
            raise BrowserError("浏览器上下文不可用")
        if self._fan_ctx is not None:
            try:
                if self._fan_ctx.pages is not None:
                    return self._fan_ctx
            except Exception:
                self._fan_ctx = None
        browser = getattr(self.context, "browser", None)
        if browser is None:
            return self.context
        storage = await self.context.storage_state()
        kwargs: dict[str, Any] = {
            "storage_state": storage,
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "viewport": {"width": 1440, "height": 900},
        }
        try:
            first = self.context.pages[0] if self.context.pages else None
            if first is not None:
                ua = await first.evaluate("() => navigator.userAgent")
                if ua:
                    kwargs["user_agent"] = ua
        except Exception:
            pass
        ctx = await browser.new_context(**kwargs)
        ctx.set_default_timeout(15000)
        self._fan_ctx = ctx
        return ctx

    async def reset_fan_context(self) -> None:
        ctx, self._fan_ctx = self._fan_ctx, None
        if ctx is None:
            return
        try:
            await ctx.close()
        except Exception:
            pass

    async def _new_fan_page(self) -> Page:
        ctx = await self._fan_context()
        page = await ctx.new_page()
        page.set_default_timeout(15000)
        if self.settings.silent_browser and not self._stay_visible:
            if not self._chrome_pids:
                await self._window_mode("hide")
            else:
                await asyncio.sleep(0.15)
                self._hide_windows_now()
        return page

    async def set_window_visible(self, visible: bool) -> None:
        self._stay_visible = visible
        if not self.context:
            return
        await self._window_mode("show" if visible else "hide")
        if visible:
            try:
                await self._focus_window()
            except Exception:
                pass

    async def _silent_watch(self) -> None:
        while True:
            try:
                await asyncio.sleep(0.8)
            except asyncio.CancelledError:
                return
            if not self.context:
                return
            if self.settings.silent_browser and not self._stay_visible:
                self._hide_windows_now()

    def _hide_windows_now(self) -> None:
        if os.name != "nt" or not self._chrome_pids:
            return
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
            user32.GetWindowThreadProcessId.argtypes = [
                wintypes.HWND,
                ctypes.POINTER(wintypes.DWORD),
            ]
            user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            pids = set(self._chrome_pids)

            def callback(hwnd: int, _: int) -> bool:
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value not in pids:
                    return True
                name = ctypes.create_unicode_buffer(128)
                user32.GetClassNameW(hwnd, name, 128)
                if name.value.startswith("Chrome_WidgetWin_"):
                    user32.ShowWindow(hwnd, 0)
                return True

            user32.EnumWindows(enum_proc(callback), 0)
        except Exception:
            pass

    async def _window_mode(self, mode: str) -> None:
        if os.name != "nt":
            return
        helper = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "browser_window.ps1")
        if not os.path.exists(helper):
            return

        def run() -> set[int]:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", helper, "-Mode", mode],
                timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW,
                capture_output=True,
            )
            out = (proc.stdout or b"").decode("utf-8", "ignore")
            match = re.search(r"pids=([\d,]+)", out)
            if not match:
                return set()
            return {int(x) for x in match.group(1).split(",") if x}

        try:
            pids = await asyncio.to_thread(run)
            if pids:
                self._chrome_pids = pids
        except Exception:
            pass

    async def _focus_window(self) -> None:
        """Best-effort: bring the dedicated Chrome window forward on Windows."""
        if os.name != "nt":
            return

        def activate() -> None:
            helper = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "focus_browser.ps1")
            if os.path.exists(helper):
                subprocess.run(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", helper],
                    timeout=8,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    capture_output=True,
                )
                return
            script = (
                "$ws = New-Object -ComObject WScript.Shell; "
                "try { $ws.AppActivate('抖音') | Out-Null } catch {}"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
                capture_output=True,
            )

        try:
            await asyncio.to_thread(activate)
        except Exception:
            pass

    async def cookies_names(self) -> set[str]:
        if self.is_mock:
            return {"mock"}
        if not self.context:
            return set()
        try:
            cookies = await self.context.cookies("https://www.douyin.com")
        except Exception:
            return set()
        return {str(c.get("name") or "") for c in cookies}

    async def is_logged_in(self) -> bool:
        if self.is_mock:
            return True
        names = await self.cookies_names()
        return bool(names & {"sessionid", "sessionid_ss", "sid_guard"})

    async def risk_texts(self) -> list[str]:
        if self.is_mock or not self.page:
            return []
        try:
            texts = await self.page.evaluate(RISK_JS)
            if isinstance(texts, list):
                return [str(t) for t in texts]
        except Exception:
            pass
        return []

    async def assert_safe(self) -> None:
        if self.is_mock:
            return
        if not await self.is_logged_in():
            raise LoginRequired("浏览器还没有登录抖音。先点“打开登录浏览器”，用手机扫码登录。")
        hits = await self.risk_texts()
        if hits:
            raise RiskControl("页面出现风控/验证提示：" + "、".join(hits))

    async def _find_visible(self, raw_selectors: list[str], *, prefer_last: bool = False):
        page = await self._ensure_page()
        for sel in raw_selectors:
            try:
                loc = page.locator(sel)
                count = await loc.count()
            except Exception:
                continue
            indexes = range(count - 1, -1, -1) if prefer_last else range(min(count, 12))
            for i in indexes:
                item = loc.nth(i)
                try:
                    if await item.is_visible():
                        return item
                except Exception:
                    continue
        return None

    async def _editor_text(self, box) -> str:
        try:
            raw = await box.inner_text()
        except Exception:
            return ""
        return (raw or "").replace("\u200b", "").strip()

    async def _find_visible_in(self, page: Page, raw_selectors: list[str], *, prefer_last: bool = False):
        for sel in raw_selectors:
            try:
                loc = page.locator(sel)
                count = await loc.count()
            except Exception:
                continue
            indexes = range(count - 1, -1, -1) if prefer_last else range(min(count, 12))
            for i in indexes:
                item = loc.nth(i)
                try:
                    if await item.is_visible():
                        return item
                except Exception:
                    continue
        return None

    async def screenshot(self, tag: str = "action") -> str:
        page = await self._ensure_page()
        name = f"{tag}_{_now_tag()}.png"
        path = self._shots / name
        try:
            await page.screenshot(path=str(path), full_page=False)
            return f"/media/shots/{name}"
        except Exception:
            return ""

    async def snapshot(self, tag: str = "manual") -> dict[str, Any]:
        page = await self._ensure_page()
        stamp = _now_tag()
        html_path = DEBUG_DIR / f"{tag}_{stamp}.html"
        png_path = self._shots / f"{tag}_{stamp}.png"
        html_path.write_text(await page.content(), encoding="utf-8")
        await page.screenshot(path=str(png_path), full_page=False)
        return {
            "url": page.url,
            "html": f"/debug/{html_path.name}",
            "screenshot": f"/media/shots/{png_path.name}",
        }

    async def status(self) -> dict[str, Any]:
        started = bool(self.context and self.page and not self.page.is_closed())
        logged = await self.is_logged_in() if started else False
        return {
            "started": started,
            "logged_in": logged,
            "mock": self.is_mock,
            "headless": self.headless,
            "url": self.page.url if started and self.page else "",
            "risk": [],
            "profile": str(self.profile_dir),
            "last_error": self.last_error,
        }

    async def open_login(self) -> dict[str, Any]:
        # 登录扫码必须让人看见；后台静默只用于登录后的运行。
        if self.headless:
            await self.close()
        self.headless = False
        if self.context and (not self.page or self.page.is_closed()):
            await self.close()
        if not self.context or not self.page:
            self.start_url = ""
            await self.start(show=True)
        else:
            await self.set_window_visible(True)
            try:
                await self.page.bring_to_front()
                await self._focus_window()
            except Exception:
                pass
        if self.page and "douyin.com" not in self.page.url:
            try:
                await self.page.goto("https://www.douyin.com/", wait_until="domcontentloaded")
                await self.page.wait_for_timeout(1500)
                await self.page.bring_to_front()
                await self.set_window_visible(True)
                await self._focus_window()
            except Exception:
                pass
        result = await self.status()
        if not result.get("started"):
            raise BrowserError(
                "浏览器进程没有保持运行。请查看 douyin-agent/data/server.log；"
                "如果多次失败，先关闭残留的 Chrome 再试。"
            )
        return result
