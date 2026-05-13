from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from nonebot.log import logger

from nextbot.stats import get_dashboard_metrics
from server.routes import api_error, api_success
from server.routes.webui import _client_ip

router = APIRouter()


@router.get("/webui/api/dashboard")
async def webui_dashboard_api(request: Request) -> JSONResponse:
    try:
        metrics = get_dashboard_metrics()
    except Exception as exc:
        client_ip = _client_ip(request)
        user_agent = request.headers.get("user-agent", "")[:200]
        logger.exception(
            f"加载仪表盘失败：reason={exc} client_ip={client_ip} user_agent={user_agent!r}"
        )
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )

    return api_success(data=metrics)
