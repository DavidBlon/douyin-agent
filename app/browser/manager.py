from __future__ import annotations

from .core import BrowserCore
from .creator_reply import CreatorReplyMixin
from .fans import FansMixin
from .feed import FeedMixin
from .inbox import InboxMixin
from .notices import NoticesMixin
from .share import ShareMixin


class BrowserManager(
    BrowserCore,
    FeedMixin,
    CreatorReplyMixin,
    InboxMixin,
    ShareMixin,
    FansMixin,
    NoticesMixin,
):
    """专用 Chrome 的总管：生命周期/窗口/截图在 BrowserCore，
    各页面域的操作在对应 mixin。组合方式保证对外 API 和原来的单体类完全一致。
    """
