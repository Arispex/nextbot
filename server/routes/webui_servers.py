from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from nonebot.log import logger
from sqlalchemy import func

from nextbot.db import Server, get_session
from nextbot.large_image import release_server_semaphores_all
from nextbot.server_validation import (
    ServerPayloadValidationError,
    ValidatedServerPayload,
    validate_server_payload_dict,
)
from nextbot.tshock_api import (
    TShockRequestError,
    get_error_reason,
    is_success,
    request_server_api,
)
from server.routes import (
    api_error,
    api_success,
    build_pagination_slice,
    read_json_object,
    read_pagination_query,
)

router = APIRouter()


def _serialize_server(server: Server) -> dict[str, Any]:
    return {
        "id": int(server.id),
        "name": str(server.name),
        "ip": str(server.ip),
        "game_port": str(server.game_port),
        "restapi_port": str(server.restapi_port),
        "token": str(server.token),
    }


def _validate_server_payload(payload: dict[str, Any]) -> ValidatedServerPayload:
    return validate_server_payload_dict(payload)


def _validation_error(exc: ServerPayloadValidationError) -> JSONResponse:
    logger.warning(f"参数校验失败：field={exc.field or ''}，reason={exc.reason}")
    return api_error(
        status_code=422,
        code="validation_error",
        message=exc.reason,
        details=[{"field": exc.field, "message": exc.reason}] if exc.field else None,
    )


@router.get("/webui/api/servers")
async def webui_servers_list(request: Request) -> JSONResponse:
    pagination, error_response = read_pagination_query(request)
    if error_response is not None:
        return error_response
    assert pagination is not None

    keyword = str(request.query_params.get("q") or "").strip().lower()

    session = get_session()
    try:
        servers = session.query(Server).order_by(Server.id.asc()).all()
        serialized = [_serialize_server(item) for item in servers]
        if keyword:
            serialized = [
                item
                for item in serialized
                if keyword in " ".join(
                    [
                        str(item.get("id") or ""),
                        str(item.get("name") or ""),
                        str(item.get("ip") or ""),
                        str(item.get("game_port") or ""),
                        str(item.get("restapi_port") or ""),
                    ]
                ).lower()
            ]
        meta, offset, limit = build_pagination_slice(
            total=len(serialized),
            page=pagination["page"],
            per_page=pagination["per_page"],
        )
        return api_success(
            data=serialized[offset : offset + limit],
            meta=meta,
        )
    except Exception as exc:
        logger.exception(f"加载服务器列表失败：reason={exc}")
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
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
            message="内部错误",
        )
    finally:
        session.close()


@router.put("/webui/api/servers/{server_id}")
async def webui_servers_update(server_id: int, request: Request) -> JSONResponse:
    payload, error_response = await read_json_object(request)
    if error_response is not None:
        return error_response
    assert payload is not None

    try:
        validated = _validate_server_payload(payload)
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
            message="内部错误",
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
        # R8 M-5：删除 server 后清理所有已注册 per-server semaphore pool 中的对应 entry
        release_server_semaphores_all(deleted_id)
        logger.info(f"删除服务器成功：server_id={deleted_id}，name={deleted_name}")
        return Response(status_code=204)
    except Exception as exc:
        session.rollback()
        logger.exception(f"删除服务器异常：server_id={server_id}，reason={exc}")
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
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
            message="内部错误",
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
            message="内部错误",
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


def _extract_upstream_error(response: Any) -> str:
    payload = getattr(response, "payload", {}) or {}
    if isinstance(payload, dict):
        raw = payload.get("error")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return get_error_reason(response)


def _load_server_or_none(server_id: int) -> Server | None:
    session = get_session()
    try:
        return session.query(Server).filter(Server.id == server_id).first()
    finally:
        session.close()


@router.get("/webui/api/servers/{server_id}/plugin-config")
async def webui_servers_plugin_config_get(server_id: int) -> JSONResponse:
    server = _load_server_or_none(server_id)
    if server is None:
        logger.warning(
            f"读取插件配置失败：server_id={server_id}，reason=服务器不存在"
        )
        return api_error(status_code=404, code="not_found", message="服务器不存在")

    try:
        response = await request_server_api(server, "/nextbot/config")
    except TShockRequestError:
        logger.warning(
            f"读取插件配置失败：server_id={server_id}，reason=无法连接服务器"
        )
        return api_error(
            status_code=502,
            code="upstream_error",
            message="无法连接服务器",
        )
    except Exception as exc:
        logger.exception(f"读取插件配置异常：server_id={server_id}，reason={exc}")
        return api_error(
            status_code=500, code="internal_error", message="内部错误"
        )

    if not is_success(response):
        reason = _extract_upstream_error(response)
        logger.warning(
            f"读取插件配置失败：server_id={server_id}，reason={reason}"
        )
        return api_error(
            status_code=502, code="upstream_error", message=reason
        )

    logger.info(f"读取插件配置成功：server_id={server_id}")
    return api_success(data=response.payload)


@router.patch("/webui/api/servers/{server_id}/plugin-config")
async def webui_servers_plugin_config_update(
    server_id: int, request: Request
) -> JSONResponse:
    data, error_response = await read_json_object(request)
    if error_response is not None:
        return error_response
    assert data is not None

    if not isinstance(data, dict) or not data:
        return api_error(
            status_code=422,
            code="validation_error",
            message="未提供任何更新字段",
        )

    params: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if isinstance(value, bool):
            params[key.strip()] = "true" if value else "false"
        elif value is None:
            params[key.strip()] = ""
        else:
            params[key.strip()] = str(value)

    if not params:
        return api_error(
            status_code=422,
            code="validation_error",
            message="未提供任何更新字段",
        )

    server = _load_server_or_none(server_id)
    if server is None:
        logger.warning(
            f"更新插件配置失败：server_id={server_id}，reason=服务器不存在"
        )
        return api_error(status_code=404, code="not_found", message="服务器不存在")

    try:
        response = await request_server_api(
            server, "/nextbot/config/update", params=params
        )
    except TShockRequestError:
        logger.warning(
            f"更新插件配置失败：server_id={server_id}，reason=无法连接服务器"
        )
        return api_error(
            status_code=502,
            code="upstream_error",
            message="无法连接服务器",
        )
    except Exception as exc:
        logger.exception(f"更新插件配置异常：server_id={server_id}，reason={exc}")
        return api_error(
            status_code=500, code="internal_error", message="内部错误"
        )

    if not is_success(response):
        reason = _extract_upstream_error(response)
        logger.warning(
            f"更新插件配置失败：server_id={server_id}，field_count={len(params)}，reason={reason}"
        )
        return api_error(
            status_code=502, code="upstream_error", message=reason
        )

    logger.info(
        f"更新插件配置成功：server_id={server_id}，field_count={len(params)}"
    )
    return api_success(data=response.payload)


@router.post("/webui/api/servers/{server_id}/plugin-config/verify-nextbot")
async def webui_servers_plugin_config_verify_nextbot(
    server_id: int,
) -> JSONResponse:
    server = _load_server_or_none(server_id)
    if server is None:
        logger.warning(
            f"验证 NextBot 连通性失败：server_id={server_id}，reason=服务器不存在"
        )
        return api_error(status_code=404, code="not_found", message="服务器不存在")

    try:
        response = await request_server_api(
            server, "/nextbot/config/verify-nextbot", timeout=15.0
        )
    except TShockRequestError:
        logger.warning(
            f"验证 NextBot 连通性失败：server_id={server_id}，reason=无法连接服务器"
        )
        return api_error(
            status_code=502,
            code="upstream_error",
            message="无法连接服务器",
        )
    except Exception as exc:
        logger.exception(
            f"验证 NextBot 连通性异常：server_id={server_id}，reason={exc}"
        )
        return api_error(
            status_code=500, code="internal_error", message="内部错误"
        )

    if not is_success(response):
        reason = _extract_upstream_error(response)
        logger.warning(
            f"验证 NextBot 连通性失败：server_id={server_id}，reason={reason}"
        )
        return api_error(
            status_code=502, code="upstream_error", message=reason
        )

    probe_status = ""
    if isinstance(response.payload, dict):
        probe_status = str(response.payload.get("probeStatus") or "")
    logger.info(
        f"验证 NextBot 连通性完成：server_id={server_id}，probeStatus={probe_status}"
    )
    return api_success(data=response.payload)
