"""按域拆分的 FastAPI 路由包。

每个模块一个 ``APIRouter``，注册完整路径（如 ``/api/status``），
由 ``app.main`` 统一 include；路径与拆分前逐字一致。
"""
