from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from nonebot.log import logger
from sqlalchemy import func

from next_bot.db import Server, get_session
from next_bot.tshock_api import (
    TShockRequestError,
    get_error_reason,
    is_success,
    request_server_api,
)
from server.routes import api_error, api_success, read_json_data, read_json_object

router = APIRouter()

_NAME_PATTERN = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff ._-]{1,32}$")


@dataclass(frozen=True)
class ValidatedServerPayload:
    name: str
    ip: str
    game_port: str
    restapi_port: str
    token: str


class ServerPayloadValidationError(ValueError):
    def __init__(self, message: str, *, field: str | None = None):
        super().__init__(message)
        self.field = field


def _serialize_server(server: Server) -> dict[str, Any]:
    return {
        "id": int(server.id),
        "name": str(server.name),
        "ip": str(server.ip),
        "game_port": str(server.game_port),
        "restapi_port": str(server.restapi_port),
        "token": str(server.token),
    }


def _require_field(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise ServerPayloadValidationError(f"{key} 为必填项", field=key)
    return payload.get(key)


def _normalize_name(raw_value: Any) -> str:
    value = str(raw_value).strip()
    if not value:
        raise ServerPayloadValidationError("服务器名称不能为空", field="name")
    if _NAME_PATTERN.fullmatch(value) is None:
        raise ServerPayloadValidationError(
            "服务器名称格式错误，仅允许中英文、数字、空格和 -_.，长度 1-32",
            field="name",
        )
    return value


def _normalize_host(raw_value: Any) -> str:
    value = str(raw_value).strip()
    if not value:
        raise ServerPayloadValidationError("服务器地址不能为空", field="ip")
    return value


def _normalize_port(raw_value: Any, *, field: str) -> str:
    if isinstance(raw_value, bool):
        raise ServerPayloadValidationError("端口必须是 1-65535 的整数", field=field)

    parsed: int
    if isinstance(raw_value, int):
        parsed = raw_value
    elif isinstance(raw_value, float):
        if not raw_value.is_integer():
            raise ServerPayloadValidationError("端口必须是整数", field=field)
        parsed = int(raw_value)
    elif isinstance(raw_value, str):
        text = raw_value.strip()
        if not text:
            raise ServerPayloadValidationError("端口不能为空", field=field)
        try:
            parsed = int(text)
        except ValueError as exc:
            raise ServerPayloadValidationError("端口必须是整数", field=field) from exc
    else:
        raise ServerPayloadValidationError("端口必须是整数", field=field)

    if not 1 <= parsed <= 65535:
        raise ServerPayloadValidationError("端口范围必须在 1-65535", field=field)
    return str(parsed)


def _normalize_token(raw_value: Any) -> str:
    value = str(raw_value).strip()
    if not value:
        raise ServerPayloadValidationError("Token 不能为空", field="token")
    if not 1 <= len(value) <= 128:
        raise ServerPayloadValidationError("Token 长度必须在 1-128 之间", field="token")
    return value


def _validate_server_payload(payload: dict[str, Any]) -> ValidatedServerPayload:
    name = _normalize_name(_require_field(payload, "name"))
    ip = _normalize_host(_require_field(payload, "ip"))
    game_port = _normalize_port(_require_field(payload, "game_port"), field="game_port")
    restapi_port = _normalize_port(
        _require_field(payload, "restapi_port"),
        field="restapi_port",
    )
    token = _normalize_token(_require_field(payload, "token"))
    return ValidatedServerPayload(
        name=name,
        ip=ip,
        game_port=game_port,
        restapi_port=restapi_port,
        token=token,
    )


def _validation_error(exc: ServerPayloadValidationError) -> JSONResponse:
    logger.warning(f"参数校验失败：field={exc.field or ''}，reason={exc}")
    return api_error(
        status_code=422,
        code="validation_error",
        message=str(exc),
        details=[{"field": exc.field, "message": str(exc)}] if exc.field else None,
    )


@router.get("/webui/api/servers")
async def webui_servers_list() -> JSONResponse:
    session = get_session()
    try:
        servers = session.query(Server).order_by(Server.id.asc()).all()
        return api_success(data=[_serialize_server(item) for item in servers])
    except Exception as exc:
        logger.exception(f"加载服务器列表失败：reason={exc}")
        return api_error(
            status_code=500,
            code="internal_error",
            message=str(exc),
        )
    finally:
        session.close()


@router.post("/webui/api/servers")
async def webui_servers_create(request: Request) -> JSONResponse:
    data, error_response = await read_json_object(request)
    if error_response is not None:
        return error_response
    assert data is not None

    try:
        validated = _validate_server_payload(data)
    except ServerPayloadValidationError as exc:
        return _validation_error(exc)

    session = get_session()
    try:
        max_id = int(session.query(func.max(Server.id)).scalar() or 0)
        server = Server(
            id=max_id + 1,
            name=validated.name,
            ip=validated.ip,
            game_port=validated.game_port,
            restapi_port=validated.restapi_port,
            token=validated.token,
        )
        session.add(server)
        session.commit()
        logger.info(f"创建服务器成功：server_id={server.id}，name={server.name}")
        return api_success(
            status_code=201,
            data=_serialize_server(server),
            headers={"Location": f"/webui/api/servers/{server.id}"},
        )
    except Exception as exc:
        session.rollback()
        logger.exception(f"创建服务器异常：name={validated.name}，reason={exc}")
        return api_error(
            status_code=500,
            code="internal_error",
            message=str(exc),
        )
    finally:
        session.close()


@router.put("/webui/api/servers/{server_id}")
async def webui_servers_update(server_id: int, request: Request) -> JSONResponse:
    data, error_response = await read_json_data(request)
    if error_response is not None:
        return error_response
    assert data is not None

    try:
        validated = _validate_server_payload(data)
    except ServerPayloadValidationError as exc:
        return _validation_error(exc)

    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
        if server is None:
            logger.warning(f"更新服务器失败：server_id={server_id}，reason=服务器不存在")
            return api_error(
                status_code=404,
                code="not_found",
                message="服务器不存在",
            )

        server.name = validated.name
        server.ip = validated.ip
        server.game_port = validated.game_port
        server.restapi_port = validated.restapi_port
        server.token = validated.token
        session.commit()
        logger.info(f"更新服务器成功：server_id={server.id}，name={server.name}")
        return api_success(data=_serialize_server(server))
    except Exception as exc:
        session.rollback()
        logger.exception(f"更新服务器异常：server_id={server_id}，reason={exc}")
        return api_error(
            status_code=500,
            code="internal_error",
            message=str(exc),
        )
    finally:
        session.close()


@router.delete("/webui/api/servers/{server_id}")
async def webui_servers_delete(server_id: int) -> JSONResponse:
    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
        if server is None:
            logger.warning(f"删除服务器失败：server_id={server_id}，reason=服务器不存在")
            return api_error(
                status_code=404,
                code="not_found",
                message="服务器不存在",
            )

        deleted_id = int(server.id)
        deleted_name = str(server.name)
        session.delete(server)
        session.flush()
        session.query(Server).filter(Server.id > deleted_id).update(
            {Server.id: Server.id - 1},
            synchronize_session=False,
        )
        session.commit()
        logger.info(f"删除服务器成功：server_id={deleted_id}，name={deleted_name}")
        return api_success(data={})
    except Exception as exc:
        session.rollback()
        logger.exception(f"删除服务器异常：server_id={server_id}，reason={exc}")
        return api_error(
            status_code=500,
            code="internal_error",
            message=str(exc),
        )
    finally:
        session.close()


@router.post("/webui/api/servers/{server_id}/test")
async def webui_servers_test(server_id: int) -> JSONResponse:
    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
    except Exception as exc:
        logger.exception(f"测试服务器异常：server_id={server_id}，reason={exc}")
        return api_error(
            status_code=500,
            code="internal_error",
            message=str(exc),
        )
    finally:
        session.close()

    if server is None:
        logger.warning(f"测试服务器失败：server_id={server_id}，reason=服务器不存在")
        return api_error(
            status_code=404,
            code="not_found",
            message="服务器不存在",
        )

    try:
        response = await request_server_api(server, "/tokentest")
    except TShockRequestError:
        logger.warning(f"测试服务器失败：server_id={server_id}，reason=无法连接服务器")
        return api_success(
            data={
                "reachable": False,
                "reason": "无法连接服务器",
            }
        )
    except Exception as exc:
        logger.exception(f"测试服务器异常：server_id={server_id}，reason={exc}")
        return api_error(
            status_code=500,
            code="internal_error",
            message=str(exc),
        )

    if is_success(response):
        logger.info(f"测试服务器成功：server_id={server_id}")
        return api_success(
            data={
                "reachable": True,
                "reason": "一切正常",
            }
        )

    reason = get_error_reason(response)
    logger.warning(f"测试服务器失败：server_id={server_id}，reason={reason}")
    return api_success(
        data={
            "reachable": False,
            "reason": reason,
        }
    )
