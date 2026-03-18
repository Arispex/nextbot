from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from nonebot.log import logger
from sqlalchemy import func

from next_bot.db import Group, Server, User, get_session
from next_bot.time_utils import format_beijing_datetime
from next_bot.tshock_api import (
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

_USER_ID_PATTERN = re.compile(r"^\d{5,20}$")
_USER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff]+$")
_MAX_USER_NAME_LENGTH = 16


@dataclass(frozen=True)
class ValidatedUserPayload:
    user_id: str
    name: str
    coins: int
    permissions: str
    group: str


class UserPayloadValidationError(ValueError):
    def __init__(self, message: str, *, field: str | None = None):
        super().__init__(message)
        self.field = field


def _require_field(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise UserPayloadValidationError(f"{key} 为必填项", field=key)
    return payload.get(key)


def _normalize_user_id(raw_value: Any) -> str:
    value = str(raw_value).strip()
    if not value:
        raise UserPayloadValidationError("用户 ID 不能为空", field="user_id")
    if _USER_ID_PATTERN.fullmatch(value) is None:
        raise UserPayloadValidationError("用户 ID 必须是 5-20 位数字", field="user_id")
    return value


def _normalize_user_name(raw_value: Any) -> str:
    value = str(raw_value).strip()
    if not value:
        raise UserPayloadValidationError("用户名称不能为空", field="name")
    if len(value) > _MAX_USER_NAME_LENGTH:
        raise UserPayloadValidationError(
            f"用户名称过长，最多 {_MAX_USER_NAME_LENGTH} 个字符",
            field="name",
        )
    if value.isdigit():
        raise UserPayloadValidationError("用户名称不能为纯数字", field="name")
    if _USER_NAME_PATTERN.fullmatch(value) is None:
        raise UserPayloadValidationError(
            "用户名称不能包含符号，只能使用中文、英文和数字",
            field="name",
        )
    return value


def _normalize_coins(raw_value: Any) -> int:
    if isinstance(raw_value, bool):
        raise UserPayloadValidationError("金币必须是非负整数", field="coins")

    parsed: int
    if isinstance(raw_value, int):
        parsed = raw_value
    elif isinstance(raw_value, float):
        if not raw_value.is_integer():
            raise UserPayloadValidationError("金币必须是整数", field="coins")
        parsed = int(raw_value)
    elif isinstance(raw_value, str):
        text = raw_value.strip()
        if not text:
            raise UserPayloadValidationError("金币不能为空", field="coins")
        try:
            parsed = int(text)
        except ValueError as exc:
            raise UserPayloadValidationError("金币必须是整数", field="coins") from exc
    else:
        raise UserPayloadValidationError("金币必须是整数", field="coins")

    if parsed < 0:
        raise UserPayloadValidationError("金币必须是非负整数", field="coins")
    return parsed


def _normalize_permissions(raw_value: Any) -> str:
    if raw_value is None:
        return ""
    text = str(raw_value).strip()
    if not text:
        return ""
    values = sorted({item.strip() for item in text.split(",") if item.strip()})
    return ",".join(values)


def _normalize_group(raw_value: Any) -> str:
    value = str(raw_value).strip()
    if not value:
        raise UserPayloadValidationError("身份组不能为空", field="group")
    return value


def _validate_payload(payload: dict[str, Any]) -> ValidatedUserPayload:
    user_id = _normalize_user_id(_require_field(payload, "user_id"))
    name = _normalize_user_name(_require_field(payload, "name"))
    coins = _normalize_coins(_require_field(payload, "coins"))
    permissions = _normalize_permissions(payload.get("permissions", ""))
    group = _normalize_group(_require_field(payload, "group"))
    return ValidatedUserPayload(
        user_id=user_id,
        name=name,
        coins=coins,
        permissions=permissions,
        group=group,
    )


def _format_created_at(value: datetime | None) -> str:
    return format_beijing_datetime(value)


def _serialize_user(user: User) -> dict[str, Any]:
    return {
        "id": int(user.id),
        "user_id": str(user.user_id),
        "name": str(user.name),
        "coins": int(user.coins),
        "permissions": str(user.permissions or ""),
        "group": str(user.group),
        "created_at": _format_created_at(user.created_at),
    }


async def _sync_user_whitelist(user: User) -> list[dict[str, Any]]:
    session = get_session()
    try:
        servers = session.query(Server).order_by(Server.id.asc()).all()
    finally:
        session.close()

    results: list[dict[str, Any]] = []
    for server in servers:
        try:
            response = await request_server_api(
                server,
                "/v3/server/rawcmd",
                params={"cmd": f"/bwl add {user.name}"},
            )
        except TShockRequestError:
            results.append(
                {
                    "server_id": int(server.id),
                    "server_name": str(server.name),
                    "success": False,
                    "reason": "无法连接服务器",
                }
            )
            continue

        if is_success(response):
            results.append(
                {
                    "server_id": int(server.id),
                    "server_name": str(server.name),
                    "success": True,
                    "reason": "",
                }
            )
            continue

        results.append(
            {
                "server_id": int(server.id),
                "server_name": str(server.name),
                "success": False,
                "reason": get_error_reason(response),
            }
        )
    return results


def _validation_error(exc: UserPayloadValidationError) -> JSONResponse:
    logger.warning(f"参数校验失败：field={exc.field or ''}，reason={exc}")
    return api_error(
        status_code=422,
        code="validation_error",
        message=str(exc),
        details=[{"field": exc.field, "message": str(exc)}] if exc.field else None,
    )


@router.get("/webui/api/users")
async def webui_users_list(request: Request) -> JSONResponse:
    pagination, error_response = read_pagination_query(request)
    if error_response is not None:
        return error_response
    assert pagination is not None

    keyword = str(request.query_params.get("q") or "").strip().lower()

    session = get_session()
    try:
        users = session.query(User).order_by(User.id.asc()).all()
        serialized = [_serialize_user(item) for item in users]
        if keyword:
            serialized = [
                item
                for item in serialized
                if keyword in " ".join(
                    [
                        str(item.get("id") or ""),
                        str(item.get("user_id") or ""),
                        str(item.get("name") or ""),
                        str(item.get("group") or ""),
                        str(item.get("permissions") or ""),
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
        logger.exception(f"加载用户列表失败：reason={exc}")
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()


@router.post("/webui/api/users")
async def webui_users_create(request: Request) -> JSONResponse:
    data, error_response = await read_json_object(request)
    if error_response is not None:
        return error_response
    assert data is not None

    try:
        validated = _validate_payload(data)
    except UserPayloadValidationError as exc:
        return _validation_error(exc)

    session = get_session()
    try:
        if session.query(User).filter(User.user_id == validated.user_id).first() is not None:
            return api_error(
                status_code=409,
                code="conflict",
                message="用户 ID 已存在",
                details=[{"field": "user_id", "message": "用户 ID 已存在"}],
            )

        if session.query(User).filter(User.name == validated.name).first() is not None:
            return api_error(
                status_code=409,
                code="conflict",
                message="用户名称已被占用",
                details=[{"field": "name", "message": "用户名称已被占用"}],
            )

        if session.query(Group).filter(Group.name == validated.group).first() is None:
            return api_error(
                status_code=422,
                code="validation_error",
                message="身份组不存在",
                details=[{"field": "group", "message": "身份组不存在"}],
            )

        user = User(
            user_id=validated.user_id,
            name=validated.name,
            coins=validated.coins,
            permissions=validated.permissions,
            group=validated.group,
        )
        session.add(user)
        session.commit()
        logger.info(f"创建用户成功：user_id={user.user_id}，name={user.name}")
        return api_success(
            status_code=201,
            data=_serialize_user(user),
            headers={"Location": f"/webui/api/users/{user.id}"},
        )
    except Exception as exc:
        session.rollback()
        logger.exception(f"创建用户异常：user_id={validated.user_id}，reason={exc}")
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()


@router.put("/webui/api/users/{user_id}")
async def webui_users_update(user_id: int, request: Request) -> JSONResponse:
    payload, error_response = await read_json_object(request)
    if error_response is not None:
        return error_response
    assert payload is not None

    try:
        validated = _validate_payload(payload)
    except UserPayloadValidationError as exc:
        return _validation_error(exc)

    session = get_session()
    try:
        user = session.query(User).filter(User.id == user_id).first()
        if user is None:
            logger.warning(f"更新用户失败：user_id={user_id}，reason=用户不存在")
            return api_error(
                status_code=404,
                code="not_found",
                message="用户不存在",
            )

        if (
            session.query(User)
            .filter(User.user_id == validated.user_id, User.id != user_id)
            .first()
            is not None
        ):
            return api_error(
                status_code=409,
                code="conflict",
                message="用户 ID 已存在",
                details=[{"field": "user_id", "message": "用户 ID 已存在"}],
            )

        if (
            session.query(User)
            .filter(User.name == validated.name, User.id != user_id)
            .first()
            is not None
        ):
            return api_error(
                status_code=409,
                code="conflict",
                message="用户名称已被占用",
                details=[{"field": "name", "message": "用户名称已被占用"}],
            )

        if session.query(Group).filter(Group.name == validated.group).first() is None:
            return api_error(
                status_code=422,
                code="validation_error",
                message="身份组不存在",
                details=[{"field": "group", "message": "身份组不存在"}],
            )

        user.user_id = validated.user_id
        user.name = validated.name
        user.coins = validated.coins
        user.permissions = validated.permissions
        user.group = validated.group
        session.commit()
        logger.info(f"更新用户成功：user_id={user_id}，account_id={user.user_id}")
        return api_success(data=_serialize_user(user))
    except Exception as exc:
        session.rollback()
        logger.exception(f"更新用户异常：user_id={user_id}，reason={exc}")
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()


@router.delete("/webui/api/users/{user_id}")
async def webui_users_delete(user_id: int) -> JSONResponse:
    session = get_session()
    try:
        user = session.query(User).filter(User.id == user_id).first()
        if user is None:
            logger.warning(f"删除用户失败：user_id={user_id}，reason=用户不存在")
            return api_error(
                status_code=404,
                code="not_found",
                message="用户不存在",
            )

        deleted_user_id = str(user.user_id)
        deleted_name = str(user.name)
        session.delete(user)
        session.commit()
        logger.info(f"删除用户成功：user_id={user_id}，account_id={deleted_user_id}，name={deleted_name}")
        return Response(status_code=204)
    except Exception as exc:
        session.rollback()
        logger.exception(f"删除用户异常：user_id={user_id}，reason={exc}")
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()


@router.post("/webui/api/users/{user_id}/sync-whitelist")
async def webui_users_sync_whitelist(user_id: int) -> JSONResponse:
    session = get_session()
    try:
        user = session.query(User).filter(User.id == user_id).first()
    except Exception as exc:
        logger.exception(f"同步用户白名单异常：user_id={user_id}，reason={exc}")
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()

    if user is None:
        logger.warning(f"同步用户白名单失败：user_id={user_id}，reason=用户不存在")
        return api_error(
            status_code=404,
            code="not_found",
            message="用户不存在",
        )

    try:
        results = await _sync_user_whitelist(user)
    except Exception as exc:
        logger.exception(f"同步用户白名单异常：user_id={user_id}，reason={exc}")
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )

    if not results:
        logger.warning(f"同步用户白名单失败：user_id={user_id}，reason=暂无可同步的服务器")
    else:
        logger.info(f"同步用户白名单完成：user_id={user_id}，server_count={len(results)}")

    return api_success(
        data={
            "user_id": str(user.user_id),
            "name": str(user.name),
            "results": results,
        }
    )
