"""Playwright 驱动的抖音页面操作包。

BrowserManager = BrowserCore + 按域拆分的 mixin（见 manager.py）。
对外导入面保持与原 app/browser.py 单体模块一致。
"""
from __future__ import annotations

from .errors import ActionError, BrowserError, LoginRequired, RiskControl
from .js import (
    CHAT_MESSAGES_JS,
    CHAT_TRANSCRIPT_JS,
    COMMENTS_JS,
    CONVERSATIONS_JS,
    CURRENT_ID_JS,
    FANS_FIND_JS,
    FANS_LIST_JS,
    FANS_SCROLL_JS,
    FOLLOW_BUTTON_JS,
    MARK_SCOPE_JS,
    MENTION_OPTION_JS,
    NAV_BADGES_JS,
    NOTIFICATION_ROWS_JS,
    READ_JS,
    RELATION_JS,
    RISK_JS,
    UNFOLLOW_BUTTON_JS,
    UNFOLLOW_STATE_JS,
)
from .manager import BrowserManager

__all__ = [
    "ActionError",
    "BrowserError",
    "BrowserManager",
    "LoginRequired",
    "RiskControl",
    "READ_JS",
    "CURRENT_ID_JS",
    "NAV_BADGES_JS",
]
