from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from nonebot.log import logger

from nextbot.command_config import (
    CommandConfigValidationError,
    list_command_configs,
    update_command_aliases,
    update_command_config,
)
from server.pages.console_page import render_commands_page
from server.routes import (
    api_error,
    api_success,
    build_pagination_slice,
    read_json_object,
    read_pagination_query,
)

router = APIRouter()

# A-3: command_key 字符集 + 长度白名单。
_COMMAND_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.\-/]{1,64}$")
# A-4: param_values 字段数 / key 长度 / 字符串值长度上限。
_PARAM_VALUES_MAX_KEYS = 64
_PARAM_VALUES_KEY_MAX_LEN = 64
_PARAM_VALUES_STR_MAX_LEN = 4096
# A-5: aliases 数组长度 + 单元素长度上限。
_ALIASES_MAX_ITEMS = 32
_ALIAS_MAX_LEN = 32


def _validate_param_values(raw: Any) -> JSONResponse | None:
    """A-4 / A-6: 校验 param_values 类型与大小，违规返回 422 响应。"""
    if not isinstance(raw, dict):
        return api_error(
            status_code=422,
            code="validation_error",
            message="param_values 必须是对象",
        )
    if len(raw) > _PARAM_VALUES_MAX_KEYS:
        return api_error(
            status_code=422,
            code="validation_error",
            message=f"param_values 字段数上限 {_PARAM_VALUES_MAX_KEYS}",
        )
    for param_key, param_value in raw.items():
        key_max = _PARAM_VALUES_KEY_MAX_LEN
        if not isinstance(param_key, str) or len(param_key) > key_max:
            return api_error(
                status_code=422,
                code="validation_error",
                message=f"param_values key 格式错误，长度上限 {key_max}",
            )
        str_max = _PARAM_VALUES_STR_MAX_LEN
        if isinstance(param_value, str) and len(param_value) > str_max:
            return api_error(
                status_code=422,
                code="validation_error",
                message=f"参数 {param_key} 值长度上限 {str_max}",
            )
    return None


def _validate_aliases_list(raw: list[Any]) -> JSONResponse | None:
    """A-5: 校验 aliases 数组长度 + 单元素长度，违规返回 422 响应。"""
    if len(raw) > _ALIASES_MAX_ITEMS:
        return api_error(
            status_code=422,
            code="validation_error",
            message=f"别名数量上限 {_ALIASES_MAX_ITEMS}",
        )
    for alias_item in raw:
        if not isinstance(alias_item, str):
            return api_error(
                status_code=422,
                code="validation_error",
                message="别名必须是字符串",
            )
        if len(alias_item) > _ALIAS_MAX_LEN:
            return api_error(
                status_code=422,
                code="validation_error",
                message=f"单个别名长度上限 {_ALIAS_MAX_LEN}",
            )
    return None


def _map_validation_error(
    exc: CommandConfigValidationError,
) -> tuple[int, str, str, list[dict[str, Any]]]:
    """把 service 层校验异常映射为 (status_code, code, message, details)。"""
    details = exc.errors or []
    status_code = 422
    error_code = "validation_error"
    message = str(exc)
    for item in details:
        field = str(item.get("field", "")).strip()
        item_message = str(item.get("message", "")).strip()
        if field == "command_key" and item_message == "命令不存在":
            return 404, "not_found", item_message, details
        if field == "command_key" and item_message == "命令已下线，无法编辑":
            return 409, "conflict", item_message, details
    return status_code, error_code, message, details


@router.get("/webui/commands", response_class=HTMLResponse)
async def webui_commands_page() -> HTMLResponse:
    return HTMLResponse(content=render_commands_page())


@router.get("/webui/api/commands")
async def webui_commands_api_list(request: Request) -> JSONResponse:
    pagination, error_response = read_pagination_query(request)
    if error_response is not None:
        return error_response
    assert pagination is not None

    keyword = str(request.query_params.get("q") or "").strip().lower()

    try:
        commands = list_command_configs()
        commands.sort(
            key=lambda item: (
                str(item.get("display_name") or "").lower(),
                str(item.get("command_key") or "").lower(),
            )
        )
        if keyword:
            commands = [
                item
                for item in commands
                if keyword in " ".join(
                    [
                        str(item.get("display_name") or ""),
                        str(item.get("description") or ""),
                        str(item.get("usage") or ""),
                        str(item.get("permission") or ""),
                        str(item.get("command_key") or ""),
                        " ".join(item.get("aliases") or []),
                    ]
                ).lower()
            ]
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"加载命令配置失败：reason={str(exc)[:500]}")
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )

    meta, offset, limit = build_pagination_slice(
        total=len(commands),
        page=pagination["page"],
        per_page=pagination["per_page"],
    )
    return api_success(
        data=commands[offset : offset + limit],
        meta=meta,
    )


@router.patch("/webui/api/commands/{command_key}")
async def webui_commands_api_update(command_key: str, request: Request) -> JSONResponse:
    # A-3: command_key 字符集 + 长度白名单校验，避免超长 / 控制符进入 DB 与日志。
    if not _COMMAND_KEY_PATTERN.fullmatch(command_key):
        return api_error(
            status_code=400,
            code="invalid_request_parameter",
            message="命令 key 格式错误",
        )

    payload, error_response = await read_json_object(request)
    if error_response is not None:
        return error_response

    assert payload is not None

    update_payload: dict[str, Any] = {}
    if "enabled" in payload:
        update_payload["enabled"] = payload.get("enabled")
    if "param_values" in payload:
        raw_param_values = payload.get("param_values")
        param_values_error = _validate_param_values(raw_param_values)
        if param_values_error is not None:
            return param_values_error
        update_payload["param_values"] = raw_param_values

    if not update_payload:
        return api_error(
            status_code=400,
            code="invalid_request_body",
            message="至少需要提供 enabled 或 param_values",
        )

    try:
        updated_command = update_command_config(command_key, **update_payload)
    except CommandConfigValidationError as exc:
        status_code, error_code, message, details = _map_validation_error(exc)
        logger.warning(
            f"保存命令配置失败：command_key={command_key}，reason={str(exc)[:500]}"
        )
        return api_error(
            status_code=status_code,
            code=error_code,
            message=message,
            details=details,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            f"保存命令配置异常：command_key={command_key}，reason={str(exc)[:500]}"
        )
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )

    logger.info(f"保存命令配置成功：command_key={command_key}")
    return api_success(data=updated_command)


@router.patch("/webui/api/commands/{command_key}/aliases")
async def webui_commands_api_update_aliases(command_key: str, request: Request) -> JSONResponse:
    # A-3: command_key 字符集 + 长度白名单校验。
    if not _COMMAND_KEY_PATTERN.fullmatch(command_key):
        return api_error(
            status_code=400,
            code="invalid_request_parameter",
            message="命令 key 格式错误",
        )

    payload, error_response = await read_json_object(request)
    if error_response is not None:
        return error_response

    assert payload is not None

    raw_aliases = payload.get("aliases")
    if not isinstance(raw_aliases, list):
        return api_error(
            status_code=422,
            code="validation_error",
            message="aliases 必须是数组",
        )

    aliases_error = _validate_aliases_list(raw_aliases)
    if aliases_error is not None:
        return aliases_error

    try:
        updated_command = update_command_aliases(command_key, raw_aliases)
    except CommandConfigValidationError as exc:
        status_code, error_code, message, details = _map_validation_error(exc)
        logger.warning(
            f"保存命令别名失败：command_key={command_key}，reason={str(exc)[:500]}"
        )
        return api_error(
            status_code=status_code,
            code=error_code,
            message=message,
            details=details,
        )
    except Exception as exc:  # noqa: BLE001
        # C-6: 与 update_config endpoint 对称，补未知异常兜底。
        logger.exception(
            f"保存命令别名异常：command_key={command_key}，reason={str(exc)[:500]}"
        )
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )

    logger.info(f"保存命令别名成功：command_key={command_key}")
    return api_success(data=updated_command)
