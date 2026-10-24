"""AI 视角直播：把专用浏览器当前页的 CDP 录屏流推给前端。"""
from __future__ import annotations

import base64
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..liveview import get_liveview

router = APIRouter()


@router.websocket("/api/live/ws")
async def live_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    view = get_liveview()
    queue = await view.subscribe()
    try:
        await websocket.send_text(json.dumps({"type": "hello", **view.status()}, ensure_ascii=False))
        while True:
            frame = await queue.get()
            await websocket.send_bytes(base64.b64decode(frame))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await view.unsubscribe(queue)
