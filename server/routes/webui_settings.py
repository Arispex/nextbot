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
    return JSONResponse(
        content={
            "ok": True,
            "data": get_settings_snapshot(),
            "meta": get_settings_metadata(),
        }
    )


@router.put("/webui/api/settings")
async def webui_settings_put(request: Request) -> JSONResponse:
    try:
        payload: Any = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "message": "保存失败，请求体必须是 JSON"},
        )

    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "message": "保存失败，请求体必须是对象"},
        )

    data = payload.get("data")
    if data is None:
        data = payload
    if not isinstance(data, dict):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "message": "保存失败，data 必须是对象"},
        )

    try:
        result = save_settings(data)
    except SettingsValidationError as exc:
        content: dict[str, Any] = {"ok": False, "message": f"保存失败，{exc}"}
        if exc.field:
            content["field"] = exc.field
        return JSONResponse(status_code=422, content=content)
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "message": f"保存失败，{exc}"},
        )

    if not _schedule_process_restart():
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "message": "重启失败，重启已在进行中，请稍后刷新页面",
                "restart_scheduled": True,
            },
        )

    return JSONResponse(
        content={
            "ok": True,
            "message": "保存成功，正在重启程序",
            "restart_scheduled": True,
            "saved_fields": result.saved_fields,
        }
    )
