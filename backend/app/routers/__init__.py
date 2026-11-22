"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import resource, cross_module, metering

# 注册顺序即匹配顺序：/api/metering/* 和 /api/cross/* 必须先于
# resource 的 /api/{module} 动态路径注册，否则会被当成模块名。
ROUTERS = [metering, cross_module, resource]
