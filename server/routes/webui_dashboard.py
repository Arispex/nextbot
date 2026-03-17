from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from nonebot.log import logger

from next_bot.stats import get_dashboard_metrics
from server.routes import api_error, api_success

router = APIRouter()


@router.get("/webui/api/dashboard")
async def webui_dashboard_api() -> JSONResponse:
    try:
        metrics = get_dashboard_metrics()
    except Exception as exc:
        logger.exception(f"加载 Web UI 仪表盘失败：reason={exc}")
        return api_error(
            status_code=500,
            code="internal_error",
            message=f"加载失败，{exc}",
        )

    return api_success(data=metrics)
