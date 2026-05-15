from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Path, Request
from fastapi.responses import JSONResponse, Response
from nonebot.log import logger
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

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
    client_ip as _shared_client_ip,
    read_json_object,
    read_pagination_query,
    user_agent as _shared_user_agent,
)

router = APIRouter()


# H-1：mask 形式 token 用于 list / create / update 响应，避免后端 API 明文外泄。
_TOKEN_MASK_PREFIX = "****"
_KEYWORD_MAX_LENGTH = 200  # A-9：搜索关键字长度上限
_PLUGIN_CONFIG_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")  # A-4
_PLUGIN_CONFIG_MAX_KEYS = 64  # A-4：单次更新字段数上限
_PLUGIN_CONFIG_VALUE_MAX_LEN = 1024  # A-4：单字段 value 长度上限


def _mask_token(token: str) -> str:
    """H-1：把 token 转为 mask 形式返回前端；保留末 4 位便于运维识别。"""
    raw = str(token or "")
    if not raw:
        return ""
    if len(raw) <= 4:
        return _TOKEN_MASK_PREFIX
    return _TOKEN_MASK_PREFIX + raw[-4:]


def _is_mask_token(token: str) -> bool:
    """H-1：客户端回填的 mask 形式表示"保留原值"。"""
    return token.startswith(_TOKEN_MASK_PREFIX)


# CRIT-1 / HIGH-2：thin re-export aliases；canonical helper 在 server/routes/__init__.py。
_client_ip = _shared_client_ip
_user_agent = _shared_user_agent


def _serialize_server(server: Server) -> dict[str, Any]:
    """H-1：list / create / update 响应统一返回 mask token，不再明文外泄。"""
    return {
        "id": int(server.id),
        "name": str(server.name),
        "ip": str(server.ip),
        "game_port": str(server.game_port),
        "restapi_port": str(server.restapi_port),
        "token": _mask_token(str(server.token)),
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

    # A-9：限制搜索关键字长度，避免攻击者发超长 q
    keyword = str(request.query_params.get("q") or "").strip()[:_KEYWORD_MAX_LENGTH].lower()

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

    client_ip = _client_ip(request)
    user_agent = _user_agent(request)

    session = get_session()
    try:
        # B-6：max+1 主键计算并发可能冲突，IntegrityError 时 rollback 后重试一次
        for attempt in range(2):
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
                break
            except IntegrityError:
                session.rollback()
                if attempt == 0:
                    logger.warning(
                        f"创建服务器主键冲突，重试一次：name={validated.name} "
                        f"client_ip={client_ip}"
                    )
                    continue
                raise
        logger.info(
            f"创建服务器成功：server_id={server.id}，name={server.name} "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )
        return api_success(
            status_code=201,
            data=_serialize_server(server),
            headers={"Location": f"/webui/api/servers/{server.id}"},
        )
    except Exception as exc:
        session.rollback()
        logger.exception(
            f"创建服务器异常：name={validated.name}，reason={exc} "
            f"client_ip={client_ip}"
        )
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()


@router.put("/webui/api/servers/{server_id}")
async def webui_servers_update(
    request: Request,
    server_id: int = Path(ge=1, le=2_147_483_647),  # A-8
) -> JSONResponse:
    payload, error_response = await read_json_object(request)
    if error_response is not None:
        return error_response
    assert payload is not None

    client_ip = _client_ip(request)
    user_agent = _user_agent(request)

    # H-1：判断客户端是否回填了完整 token；mask 形式或空串表示"保留原值"
    raw_token = str(payload.get("token", "")).strip() if isinstance(payload, dict) else ""
    keep_existing_token = (not raw_token) or _is_mask_token(raw_token)

    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
        if server is None:
            logger.warning(
                f"更新服务器失败：server_id={server_id}，reason=服务器不存在 "
                f"client_ip={client_ip}"
            )
            return api_error(
                status_code=404,
                code="not_found",
                message="服务器不存在",
            )

        # H-1：若客户端要求保留原 token，构造 validation 入参时塞回原 token 让校验通过
        validation_payload = dict(payload) if isinstance(payload, dict) else {}
        if keep_existing_token:
            validation_payload["token"] = str(server.token)

        try:
            validated = _validate_server_payload(validation_payload)
        except ServerPayloadValidationError as exc:
            return _validation_error(exc)

        server.name = validated.name
        server.ip = validated.ip
        server.game_port = validated.game_port
        server.restapi_port = validated.restapi_port
        # H-1：仅在客户端显式传新 token 时才更新（mask / 空串 → 跳过赋值）
        if not keep_existing_token:
            server.token = validated.token
        session.commit()
        logger.info(
            f"更新服务器成功：server_id={server.id}，name={server.name}，"
            f"token_changed={not keep_existing_token} "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )
        return api_success(data=_serialize_server(server))
    except Exception as exc:
        session.rollback()
        logger.exception(
            f"更新服务器异常：server_id={server_id}，reason={exc} "
            f"client_ip={client_ip}"
        )
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()


@router.delete("/webui/api/servers/{server_id}")
async def webui_servers_delete(
    request: Request,
    server_id: int = Path(ge=1, le=2_147_483_647),  # A-8
) -> JSONResponse:
    client_ip = _client_ip(request)
    user_agent = _user_agent(request)
    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
        if server is None:
            logger.warning(
                f"删除服务器失败：server_id={server_id}，reason=服务器不存在 "
                f"client_ip={client_ip}"
            )
            return api_error(
                status_code=404,
                code="not_found",
                message="服务器不存在",
            )

        deleted_id = int(server.id)
        deleted_name = str(server.name)
        session.delete(server)
        session.flush()
        reindex_result = session.query(Server).filter(Server.id > deleted_id).update(
            {Server.id: Server.id - 1},
            synchronize_session=False,
        )
        session.commit()
        # R8 M-5：删除 server 后清理所有已注册 per-server semaphore pool 中的对应 entry
        release_server_semaphores_all(deleted_id)
        # D-3：记录 reindex 影响行数，便于审计回溯 server_id 突变
        logger.info(
            f"删除服务器成功：server_id={deleted_id}，name={deleted_name}，"
            f"reindex_rows={int(reindex_result or 0)} "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )
        return Response(status_code=204)
    except Exception as exc:
        session.rollback()
        logger.exception(
            f"删除服务器异常：server_id={server_id}，reason={exc} "
            f"client_ip={client_ip}"
        )
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()


@router.post("/webui/api/servers/{server_id}/test")
async def webui_servers_test(
    request: Request,
    server_id: int = Path(ge=1, le=2_147_483_647),  # A-8
) -> JSONResponse:
    client_ip = _client_ip(request)
    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
    except Exception as exc:
        logger.exception(
            f"测试服务器异常：server_id={server_id}，reason={exc} "
            f"client_ip={client_ip}"
        )
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()

    if server is None:
        logger.warning(
            f"测试服务器失败：server_id={server_id}，reason=服务器不存在 "
            f"client_ip={client_ip}"
        )
        return api_error(
            status_code=404,
            code="not_found",
            message="服务器不存在",
        )

    try:
        # B-4：从默认 5s 提升到 10s，给慢响应 / DNS 慢的远端留余量
        response = await request_server_api(server, "/tokentest", timeout=10.0)
    except TShockRequestError:
        logger.warning(
            f"测试服务器失败：server_id={server_id}，reason=无法连接服务器 "
            f"client_ip={client_ip}"
        )
        return api_success(
            data={
                "reachable": False,
                "reason": "无法连接服务器",
            }
        )
    except Exception as exc:
        logger.exception(
            f"测试服务器异常：server_id={server_id}，reason={exc} "
            f"client_ip={client_ip}"
        )
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )

    if is_success(response):
        logger.info(f"测试服务器成功：server_id={server_id} client_ip={client_ip}")
        return api_success(
            data={
                "reachable": True,
                "reason": "一切正常",
            }
        )

    reason = get_error_reason(response)
    logger.warning(
        f"测试服务器失败：server_id={server_id}，reason={reason} "
        f"client_ip={client_ip}"
    )
    return api_success(
        data={
            "reachable": False,
            "reason": reason,
        }
    )


@router.get("/webui/api/servers/{server_id}/token")
async def webui_servers_reveal_token(
    request: Request,
    server_id: int = Path(ge=1, le=2_147_483_647),  # A-8
) -> JSONResponse:
    """H-1：按需返回完整 token，仅供前端「显示」按钮临时获取。

    审计要点：明文 token 不再随 list / detail 默认返回，而是必须显式 GET 本端点；
    每次调用都会以 WARN 级别记录访问者 IP / UA，便于审计追溯。
    """
    client_ip = _client_ip(request)
    user_agent = _user_agent(request)
    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
        if server is None:
            logger.warning(
                f"展示 token 失败：server_id={server_id}，reason=服务器不存在 "
                f"client_ip={client_ip}"
            )
            return api_error(
                status_code=404,
                code="not_found",
                message="服务器不存在",
            )
        logger.warning(
            f"展示 token 成功：server_id={server_id}，name={server.name} "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )
        return api_success(data={"token": str(server.token)})
    except Exception as exc:
        logger.exception(
            f"展示 token 异常：server_id={server_id}，reason={exc} "
            f"client_ip={client_ip}"
        )
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()


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
async def webui_servers_plugin_config_get(
    request: Request,
    server_id: int = Path(ge=1, le=2_147_483_647),  # A-8
) -> JSONResponse:
    client_ip = _client_ip(request)
    server = _load_server_or_none(server_id)
    if server is None:
        logger.warning(
            f"读取插件配置失败：server_id={server_id}，reason=服务器不存在 "
            f"client_ip={client_ip}"
        )
        return api_error(status_code=404, code="not_found", message="服务器不存在")

    try:
        response = await request_server_api(server, "/nextbot/config")
    except TShockRequestError:
        logger.warning(
            f"读取插件配置失败：server_id={server_id}，reason=无法连接服务器 "
            f"client_ip={client_ip}"
        )
        return api_error(
            status_code=502,
            code="upstream_error",
            message="无法连接服务器",
        )
    except Exception as exc:
        logger.exception(
            f"读取插件配置异常：server_id={server_id}，reason={exc} "
            f"client_ip={client_ip}"
        )
        return api_error(
            status_code=500, code="internal_error", message="内部错误"
        )

    if not is_success(response):
        reason = _extract_upstream_error(response)
        logger.warning(
            f"读取插件配置失败：server_id={server_id}，reason={reason} "
            f"client_ip={client_ip}"
        )
        return api_error(
            status_code=502, code="upstream_error", message=reason
        )

    logger.info(f"读取插件配置成功：server_id={server_id} client_ip={client_ip}")
    return api_success(data=response.payload)


@router.patch("/webui/api/servers/{server_id}/plugin-config")
async def webui_servers_plugin_config_update(
    request: Request,
    server_id: int = Path(ge=1, le=2_147_483_647),  # A-8
) -> JSONResponse:
    data, error_response = await read_json_object(request)
    if error_response is not None:
        return error_response
    assert data is not None

    client_ip = _client_ip(request)

    if not isinstance(data, dict) or not data:
        return api_error(
            status_code=422,
            code="validation_error",
            message="未提供任何更新字段",
        )

    # A-4：字段数量上限，避免被滥用做内存放大或慢 RPC
    if len(data) > _PLUGIN_CONFIG_MAX_KEYS:
        return api_error(
            status_code=422,
            code="validation_error",
            message=f"更新字段数不能超过 {_PLUGIN_CONFIG_MAX_KEYS}",
        )

    params: dict[str, str] = {}
    for key, value in data.items():
        # A-4：key 字符集白名单（字母数字下划线点），长度上限 128，防注入 / 控制字符
        if not isinstance(key, str):
            continue
        normalized_key = key.strip()
        if not normalized_key or not _PLUGIN_CONFIG_KEY_PATTERN.fullmatch(normalized_key):
            return api_error(
                status_code=422,
                code="validation_error",
                message=f"字段名格式错误：{normalized_key[:64]}",
            )

        # A-4：value 类型白名单（bool/None/str/int/float），拒绝 list/dict 嵌套
        if isinstance(value, bool):
            converted = "true" if value else "false"
        elif value is None:
            converted = ""
        elif isinstance(value, (int, float)):
            converted = str(value)
        elif isinstance(value, str):
            converted = value
        else:
            return api_error(
                status_code=422,
                code="validation_error",
                message=f"字段值类型不支持：{normalized_key}",
            )

        # A-4：value 长度上限，防超长 payload
        if len(converted) > _PLUGIN_CONFIG_VALUE_MAX_LEN:
            return api_error(
                status_code=422,
                code="validation_error",
                message=f"字段值长度不能超过 {_PLUGIN_CONFIG_VALUE_MAX_LEN}：{normalized_key}",
            )

        params[normalized_key] = converted

    if not params:
        return api_error(
            status_code=422,
            code="validation_error",
            message="未提供任何更新字段",
        )

    server = _load_server_or_none(server_id)
    if server is None:
        logger.warning(
            f"更新插件配置失败：server_id={server_id}，reason=服务器不存在 "
            f"client_ip={client_ip}"
        )
        return api_error(status_code=404, code="not_found", message="服务器不存在")

    try:
        response = await request_server_api(
            server, "/nextbot/config/update", params=params
        )
    except TShockRequestError:
        logger.warning(
            f"更新插件配置失败：server_id={server_id}，reason=无法连接服务器 "
            f"client_ip={client_ip}"
        )
        return api_error(
            status_code=502,
            code="upstream_error",
            message="无法连接服务器",
        )
    except Exception as exc:
        logger.exception(
            f"更新插件配置异常：server_id={server_id}，reason={exc} "
            f"client_ip={client_ip}"
        )
        return api_error(
            status_code=500, code="internal_error", message="内部错误"
        )

    if not is_success(response):
        reason = _extract_upstream_error(response)
        logger.warning(
            f"更新插件配置失败：server_id={server_id}，field_count={len(params)}，"
            f"reason={reason} client_ip={client_ip}"
        )
        return api_error(
            status_code=502, code="upstream_error", message=reason
        )

    logger.info(
        f"更新插件配置成功：server_id={server_id}，field_count={len(params)} "
        f"client_ip={client_ip}"
    )
    return api_success(data=response.payload)


@router.post("/webui/api/servers/{server_id}/plugin-config/verify-nextbot")
async def webui_servers_plugin_config_verify_nextbot(
    request: Request,
    server_id: int = Path(ge=1, le=2_147_483_647),  # A-8
) -> JSONResponse:
    client_ip = _client_ip(request)
    server = _load_server_or_none(server_id)
    if server is None:
        logger.warning(
            f"验证 NextBot 连通性失败：server_id={server_id}，reason=服务器不存在 "
            f"client_ip={client_ip}"
        )
        return api_error(status_code=404, code="not_found", message="服务器不存在")

    try:
        # R2-T-3：后端 timeout 降到 10s，给前端 15s cap 留 5s 缓冲，避免 race。
        response = await request_server_api(
            server, "/nextbot/config/verify-nextbot", timeout=10.0
        )
    except TShockRequestError:
        logger.warning(
            f"验证 NextBot 连通性失败：server_id={server_id}，reason=无法连接服务器 "
            f"client_ip={client_ip}"
        )
        return api_error(
            status_code=502,
            code="upstream_error",
            message="无法连接服务器",
        )
    except Exception as exc:
        logger.exception(
            f"验证 NextBot 连通性异常：server_id={server_id}，reason={exc} "
            f"client_ip={client_ip}"
        )
        return api_error(
            status_code=500, code="internal_error", message="内部错误"
        )

    if not is_success(response):
        reason = _extract_upstream_error(response)
        logger.warning(
            f"验证 NextBot 连通性失败：server_id={server_id}，reason={reason} "
            f"client_ip={client_ip}"
        )
        return api_error(
            status_code=502, code="upstream_error", message=reason
        )

    probe_status = ""
    if isinstance(response.payload, dict):
        probe_status = str(response.payload.get("probeStatus") or "")
    logger.info(
        f"验证 NextBot 连通性完成：server_id={server_id}，probeStatus={probe_status} "
        f"client_ip={client_ip}"
    )
    return api_success(data=response.payload)
