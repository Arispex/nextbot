from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from next_bot.command_config import (
    CommandConfigValidationError,
    apply_command_batch_updates,
    list_command_configs,
)
from server.pages.console_page import render_commands_page

router = APIRouter()


@router.get("/webui/commands", response_class=HTMLResponse)
async def webui_commands_page() -> HTMLResponse:
    return HTMLResponse(content=render_commands_page())


@router.get("/webui/api/commands")
async def webui_commands_api_list() -> JSONResponse:
    commands = list_command_configs()
    return JSONResponse(
        content={
            "commands": commands,
        }
    )


@router.put("/webui/api/commands/batch")
async def webui_commands_api_batch_save(request: Request) -> JSONResponse:
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

    commands = payload.get("commands")
    if not isinstance(commands, list):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "message": "保存失败，commands 必须是数组"},
        )

    try:
        apply_command_batch_updates(commands)
    except CommandConfigValidationError as exc:
        return JSONResponse(
            status_code=422,
            content={
                "ok": False,
                "message": f"保存失败，{exc}",
                "errors": exc.errors,
            },
        )

    return JSONResponse(content={"ok": True, "message": "保存成功"})
