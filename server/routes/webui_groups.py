from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from nonebot.log import logger
from sqlalchemy import func

from next_bot.db import Group, User, get_session
from server.routes import (
    api_error,
    api_success,
    build_pagination_slice,
    read_json_object,
    read_pagination_query,
)

router = APIRouter()

_BUILTIN_GROUPS: tuple[str, str] = ("guest", "default")
_GROUP_NAME_PATTERN = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff._-]{1,32}$")
_TOKEN_ITEM_PATTERN = re.compile(r"^[^\s,]{1,256}$")


@dataclass(frozen=True)
class ValidatedGroupPayload:
    name: str
    permissions: str
    inherits: str


class GroupPayloadValidationError(ValueError):
    def __init__(self, message: str, *, field: str | None = None):
        super().__init__(message)
        self.field = field


def _require_field(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise GroupPayloadValidationError(f"{key} 为必填项", field=key)
    return payload.get(key)


def _normalize_group_name(raw_value: Any) -> str:
    value = str(raw_value).strip()
    if not value:
        raise GroupPayloadValidationError("身份组名称不能为空", field="name")
    if _GROUP_NAME_PATTERN.fullmatch(value) is None:
        raise GroupPayloadValidationError(
            "身份组名称格式错误，仅允许中文、英文、数字和 ._-，长度 1-32",
            field="name",
        )
    return value


def _normalize_token_csv(
    raw_value: Any,
    *,
    field: str,
    label: str,
) -> str:
    if raw_value is None:
        return ""
    text = str(raw_value).strip()
    if not text:
        return ""

    items: list[str] = []
    for token in text.split(","):
        value = token.strip()
        if not value:
            continue
        if _TOKEN_ITEM_PATTERN.fullmatch(value) is None:
            raise GroupPayloadValidationError(
                f"{label}项格式错误，不能包含空白或逗号，且长度 1-256",
                field=field,
            )
        items.append(value)

    return ",".join(sorted(set(items)))


def _parse_csv_values(value: str) -> list[str]:
    return [item for item in (v.strip() for v in value.split(",")) if item]


def _validate_inherits_targets(session, *, inherits: str, self_name: str) -> None:
    targets = _parse_csv_values(inherits)
    if not targets:
        return

    if self_name in set(targets):
        raise GroupPayloadValidationError("继承列表不能包含自身", field="inherits")

    existing = {
        str(row[0])
        for row in session.query(Group.name)
        .filter(Group.name.in_(set(targets)))
        .all()
    }
    missing = sorted(set(targets) - existing)
    if missing:
        raise GroupPayloadValidationError(
            f"继承目标不存在：{missing[0]}",
            field="inherits",
        )


def _build_user_count_map(session) -> dict[str, int]:
    rows = session.query(User.group, func.count(User.id)).group_by(User.group).all()
    result: dict[str, int] = {}
    for group_name, count in rows:
        if group_name is None:
            continue
        result[str(group_name)] = int(count)
    return result


def _serialize_group(group: Group, *, user_count_map: dict[str, int]) -> dict[str, Any]:
    group_name = str(group.name)
    return {
        "name": group_name,
        "permissions": str(group.permissions or ""),
        "inherits": str(group.inherits or ""),
        "user_count": int(user_count_map.get(group_name, 0)),
        "builtin": group_name in _BUILTIN_GROUPS,
    }


def _validate_create_payload(payload: dict[str, Any]) -> ValidatedGroupPayload:
    name = _normalize_group_name(_require_field(payload, "name"))
    permissions = _normalize_token_csv(
        payload.get("permissions", ""),
        field="permissions",
        label="权限",
    )
    inherits = _normalize_token_csv(
        payload.get("inherits", ""),
        field="inherits",
        label="继承",
    )
    return ValidatedGroupPayload(
        name=name,
        permissions=permissions,
        inherits=inherits,
    )


def _validate_update_payload(
    payload: dict[str, Any],
    *,
    current: Group,
    target_name: str,
) -> ValidatedGroupPayload:
    if "name" in payload:
        incoming_name = str(payload.get("name", "")).strip()
        if incoming_name and incoming_name != target_name:
            raise GroupPayloadValidationError("不支持修改身份组名称", field="name")

    permissions = _normalize_token_csv(
        payload.get("permissions", current.permissions),
        field="permissions",
        label="权限",
    )
    inherits = _normalize_token_csv(
        payload.get("inherits", current.inherits),
        field="inherits",
        label="继承",
    )
    return ValidatedGroupPayload(
        name=target_name,
        permissions=permissions,
        inherits=inherits,
    )


def _remove_inherit(inherits: str, removed_name: str) -> str:
    values = [item for item in _parse_csv_values(inherits) if item != removed_name]
    return ",".join(sorted(set(values)))


def _validation_error(exc: GroupPayloadValidationError) -> JSONResponse:
    logger.warning(f"参数校验失败：field={exc.field or ''}，reason={exc}")
    return api_error(
        status_code=422,
        code="validation_error",
        message=str(exc),
        details=[{"field": exc.field, "message": str(exc)}] if exc.field else None,
    )


@router.get("/webui/api/groups")
async def webui_groups_list(request: Request) -> JSONResponse:
    pagination, error_response = read_pagination_query(request)
    if error_response is not None:
        return error_response
    assert pagination is not None

    keyword = str(request.query_params.get("q") or "").strip().lower()

    session = get_session()
    try:
        groups = session.query(Group).order_by(Group.name.asc()).all()
        user_count_map = _build_user_count_map(session)
        serialized = [
            _serialize_group(item, user_count_map=user_count_map)
            for item in groups
        ]
        if keyword:
            serialized = [
                item
                for item in serialized
                if keyword in " ".join(
                    [
                        str(item.get("name") or ""),
                        str(item.get("permissions") or ""),
                        str(item.get("inherits") or ""),
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
        logger.exception(f"加载身份组列表失败：reason={exc}")
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()


@router.get("/webui/api/groups/options")
async def webui_groups_options() -> JSONResponse:
    session = get_session()
    try:
        groups = session.query(Group.name).order_by(Group.name.asc()).all()
        return api_success(data=[str(item[0]) for item in groups if item[0] is not None])
    except Exception as exc:
        logger.exception(f"加载身份组选项失败：reason={exc}")
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()


@router.post("/webui/api/groups")
async def webui_groups_create(request: Request) -> JSONResponse:
    data, error_response = await read_json_object(request)
    if error_response is not None:
        return error_response
    assert data is not None

    try:
        validated = _validate_create_payload(data)
    except GroupPayloadValidationError as exc:
        return _validation_error(exc)

    session = get_session()
    try:
        exists = session.query(Group).filter(Group.name == validated.name).first()
        if exists is not None:
            return api_error(
                status_code=409,
                code="conflict",
                message="身份组已存在",
                details=[{"field": "name", "message": "身份组已存在"}],
            )

        _validate_inherits_targets(
            session,
            inherits=validated.inherits,
            self_name=validated.name,
        )

        group = Group(
            name=validated.name,
            permissions=validated.permissions,
            inherits=validated.inherits,
        )
        session.add(group)
        session.commit()

        user_count_map = _build_user_count_map(session)
        logger.info(f"创建身份组成功：name={group.name}")
        return api_success(
            status_code=201,
            data=_serialize_group(group, user_count_map=user_count_map),
            headers={"Location": f"/webui/api/groups/{group.name}"},
        )
    except GroupPayloadValidationError as exc:
        session.rollback()
        return _validation_error(exc)
    except Exception as exc:
        session.rollback()
        logger.exception(f"创建身份组异常：name={validated.name}，reason={exc}")
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()


@router.patch("/webui/api/groups/{group_name}")
async def webui_groups_update(group_name: str, request: Request) -> JSONResponse:
    payload, error_response = await read_json_object(request)
    if error_response is not None:
        return error_response
    assert payload is not None

    session = get_session()
    try:
        group = session.query(Group).filter(Group.name == group_name).first()
        if group is None:
            logger.warning(f"更新身份组失败：name={group_name}，reason=身份组不存在")
            return api_error(
                status_code=404,
                code="not_found",
                message="身份组不存在",
            )

        try:
            validated = _validate_update_payload(
                payload,
                current=group,
                target_name=group_name,
            )
        except GroupPayloadValidationError as exc:
            return _validation_error(exc)

        _validate_inherits_targets(
            session,
            inherits=validated.inherits,
            self_name=group_name,
        )

        group.permissions = validated.permissions
        group.inherits = validated.inherits
        session.commit()

        user_count_map = _build_user_count_map(session)
        logger.info(f"更新身份组成功：name={group_name}")
        return api_success(data=_serialize_group(group, user_count_map=user_count_map))
    except GroupPayloadValidationError as exc:
        session.rollback()
        return _validation_error(exc)
    except Exception as exc:
        session.rollback()
        logger.exception(f"更新身份组异常：name={group_name}，reason={exc}")
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()


@router.delete("/webui/api/groups/{group_name}")
async def webui_groups_delete(group_name: str) -> JSONResponse:
    if group_name in _BUILTIN_GROUPS:
        logger.warning(f"删除身份组失败：name={group_name}，reason=系统内置身份组不可删除")
        return api_error(
            status_code=422,
            code="validation_error",
            message="系统内置身份组不可删除",
            details=[{"field": "name", "message": "系统内置身份组不可删除"}],
        )

    session = get_session()
    try:
        group = session.query(Group).filter(Group.name == group_name).first()
        if group is None:
            logger.warning(f"删除身份组失败：name={group_name}，reason=身份组不存在")
            return api_error(
                status_code=404,
                code="not_found",
                message="身份组不存在",
            )

        session.delete(group)
        session.flush()

        session.query(User).filter(User.group == group_name).update(
            {User.group: "guest"},
            synchronize_session=False,
        )

        all_groups = session.query(Group).all()
        for item in all_groups:
            item.inherits = _remove_inherit(item.inherits, group_name)

        session.commit()
        logger.info(f"删除身份组成功：name={group_name}")
        return Response(status_code=204)
    except Exception as exc:
        session.rollback()
        logger.exception(f"删除身份组异常：name={group_name}，reason={exc}")
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()
