from __future__ import annotations

import asyncio
import json
from typing import Any

from . import db


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def _publish(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def emit(
        self,
        kind: str,
        message: str,
        data: dict[str, Any] | None = None,
        level: str = "info",
    ) -> dict[str, Any]:
        event = db.insert_event(level, kind, message, data)
        self._publish(event)
        return event

    async def stream(self, request) -> Any:
        queue = self.subscribe()
        try:
            hello = {"id": 0, "ts": db.now_iso(), "level": "info", "kind": "hello", "message": "connected", "data": {}}
            yield {"data": json.dumps(hello, ensure_ascii=False)}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
                    continue
                yield {"data": json.dumps(event, ensure_ascii=False, default=str)}
        finally:
            self.unsubscribe(queue)


bus = EventBus()
