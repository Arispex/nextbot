from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from nonebot.log import logger

from server.pages.console_page import render_settings_page
from server.routes import api_error, api_success, read_json_object
from server.settings_service import (
    SettingsValidationError,
    get_settings_metadata,
    get_settings_snapshot,
    save_settings,
)

router = APIRouter()
_RESTART_LOCK = threading.Lock()
_RESTART_SCHEDULED = False


def _restart_worker() -> None:
    global _RESTART_SCHEDULED
    try:
        time.sleep(0.8)
        logger.warning("检测到设置变更，程序即将重启...")
        os.execv(sys.executable, [sys.executable, *sys.argv])
    except Exception as exc:
        logger.exception(f"重启失败：{exc}")
        with _RESTART_LOCK:
            _RESTART_SCHEDULED = False


def _schedule_process_restart() -> bool:
    global _RESTART_SCHEDULED
    with _RESTART_LOCK:
        if _RESTART_SCHEDULED:
            return False
        _RESTART_SCHEDULED = True
    thread = threading.Thread(
        target=_restart_worker,
        name="nextbot-restart-worker",
        daemon=True,
    )
    thread.start()
    return True


@router.get("/webui/settings", response_class=HTMLResponse)
async def webui_settings_page() -> HTMLResponse:
    return HTMLResponse(content=render_settings_page())


@router.get("/webui/api/settings")
async def webui_settings_get() -> JSONResponse:
    return api_success(
        data=get_settings_snapshot(),
        meta=get_settings_metadata(),
    )


@router.put("/webui/api/settings")
async def webui_settings_put(request: Request) -> JSONResponse:
    payload, error_response = await read_json_object(request)
    if error_response is not None:
        return error_response

    try:
        result = save_settings(payload)
    except SettingsValidationError as exc:
        logger.warning(f"保存设置失败：field={exc.field or ''}，reason={exc}")
        details: list[dict[str, Any]] | None = None
        if exc.field:
            details = [{"field": exc.field, "message": str(exc)}]
        return api_error(
            status_code=422,
            code="validation_error",
            message=str(exc),
            details=details,
        )
    except Exception as exc:
        logger.exception(f"保存设置异常：reason={exc}")
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )

    if not _schedule_process_restart():
        logger.warning("保存设置失败：reason=重启已在进行中")
        return api_error(
            status_code=409,
            code="conflict",
            message="重启已在进行中，请稍后刷新页面",
            details=[{"field": "restart", "message": "重启已在进行中"}],
        )

    logger.info(f"保存设置成功：saved_fields={','.join(result.saved_fields)}")
    return api_success(
        data={
            "restart_scheduled": True,
            "saved_fields": result.saved_fields,
        }
    )
