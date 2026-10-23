from __future__ import annotations

import asyncio
import time
from typing import Any

from . import db
from .agent import runner
from .events import bus


class AutoOperator:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._last_run = 0.0
        self.last_error = ""
        self.last_result: dict[str, Any] = {}

    @property
    def running(self) -> bool:
        return bool(self._task and not self._task.done())

    def status(self) -> dict[str, Any]:
        settings = db.load_settings()
        interval = settings.auto_interval_min or 30
        return {
            "enabled": bool(settings.auto_operate),
            "running": self.running,
            "busy": runner.running,
            "interval_min": interval,
            "last_error": self.last_error,
            "last_result": self.last_result,
        }

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
        await asyncio.sleep(15)
        while not self._stop.is_set():
            try:
                settings = db.load_settings()
                interval = max(5, int(settings.auto_interval_min or 30)) * 60
                if (
                    settings.auto_operate
                    and not runner.running
                    and not runner.exclusive.locked()
                    and not getattr(runner, "reply_pending", False)
                    and time.monotonic() - self._last_run >= interval
                ):
                    self._last_run = time.monotonic()
                    try:
                        result = await runner.run_once()
                        self.last_result = result
                        self.last_error = ""
                    except Exception as exc:
                        self.last_error = str(exc)[:300]
                        await bus.emit("error", f"全自动互动失败：{exc}", {}, level="error")
            except Exception as exc:
                self.last_error = str(exc)[:300]
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass


operator = AutoOperator()
