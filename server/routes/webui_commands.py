from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from nonebot.log import logger

from next_bot.command_config import (
    CommandConfigValidationError,
    list_command_configs,
    update_command_config,
)
from server.pages.console_page import render_commands_page
from server.routes import api_error, api_success, read_json_data

router = APIRouter()


@router.get("/webui/commands", response_class=HTMLResponse)
async def webui_commands_page() -> HTMLResponse:
    return HTMLResponse(content=render_commands_page())


@router.get("/webui/api/commands")
async def webui_commands_api_list() -> JSONResponse:
    try:
        commands = list_command_configs()
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"加载 Web UI 命令配置失败：reason={exc}")
        return api_error(
            status_code=500,
            code="internal_error",
            message=f"加载失败，{exc}",
        )
    return api_success(data=commands)


@router.patch("/webui/api/commands/{command_key}")
async def webui_commands_api_update(command_key: str, request: Request) -> JSONResponse:
    data, error_response = await read_json_data(request, action="保存")
    if error_response is not None:
        return error_response

    assert data is not None

    update_payload: dict[str, Any] = {}
    if "enabled" in data:
        update_payload["enabled"] = data.get("enabled")
    if "param_values" in data:
        update_payload["param_values"] = data.get("param_values")

    if not update_payload:
        return api_error(
            status_code=400,
            code="invalid_request_body",
            message="保存失败，至少需要提供 enabled 或 param_values",
        )

    try:
        updated_command = update_command_config(command_key, **update_payload)
    except CommandConfigValidationError as exc:
        details = exc.errors or []
        status_code = 422
        error_code = "validation_error"
        for item in details:
            field = str(item.get("field", "")).strip()
            message = str(item.get("message", "")).strip()
            if field == "command_key" and message == "命令不存在":
                status_code = 404
                error_code = "not_found"
                break
            if field == "command_key" and message == "命令已下线，无法编辑":
                status_code = 409
                error_code = "conflict"
                break
        logger.warning(
            f"保存 Web UI 命令配置失败：command_key={command_key}，reason={exc}"
        )
        return api_error(
            status_code=status_code,
            code=error_code,
            message=f"保存失败，{exc}",
            details=details,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            f"保存 Web UI 命令配置异常：command_key={command_key}，reason={exc}"
        )
        return api_error(
            status_code=500,
            code="internal_error",
            message=f"保存失败，{exc}",
        )

    logger.info(f"保存 Web UI 命令配置成功：command_key={command_key}")
    return api_success(data=updated_command)
