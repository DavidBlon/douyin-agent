"""BrowserManager 抛出的异常类型。"""
from __future__ import annotations


class BrowserError(RuntimeError):
    pass


class LoginRequired(BrowserError):
    pass


class RiskControl(BrowserError):
    pass


class ActionError(BrowserError):
    pass
