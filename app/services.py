"""跨路由共享的长驻服务单例。

runner / analytics / publisher / planner / operator / chat / bus 的单例在各自模块里；
replier 和 sharer 需要 runner 来构造，放这里供 main 的 lifespan 和各 api 路由共用，
避免 main ↔ router 循环导入。
"""
from __future__ import annotations

from .agent import runner
from .replier import AutoReplier
from .sharer import AutoSharer

replier = AutoReplier(runner)
sharer = AutoSharer(runner)
