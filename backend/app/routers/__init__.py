"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import chains
from app.routers import quote_workflow
from app.routers import resource

# 固定前缀的报价工作流路由要先注册，否则会被 /api/{module}/{entry_id} 抢先匹配。
ROUTERS = [quote_workflow.module_router, quote_workflow.router, resource, chains]
