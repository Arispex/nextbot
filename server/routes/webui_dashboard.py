from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from next_bot.stats import get_dashboard_metrics

router = APIRouter()


@router.get("/webui/api/dashboard")
async def webui_dashboard_api() -> JSONResponse:
    try:
        metrics = get_dashboard_metrics()
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "message": f"加载失败，{exc}",
            },
        )

    return JSONResponse(
        content={
            "ok": True,
            "data": metrics,
        }
    )
