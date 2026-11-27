"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。

图纸导入/核验清单路由放在资源路由之前注册，避免与 `/{module}` 动态路径混淆。
"""
from __future__ import annotations

from app.routers import drawing_import, resource

ROUTERS = [drawing_import, resource]
