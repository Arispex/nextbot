from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func

from next_bot.db import Group, User, get_session

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


def _bad_request(message: str) -> JSONResponse:
    return JSONResponse(status_code=400, content={"ok": False, "message": message})


def _unprocessable(message: str, *, field: str | None = None) -> JSONResponse:
    content: dict[str, Any] = {"ok": False, "message": message}
    if field:
        content["field"] = field
    return JSONResponse(status_code=422, content=content)


def _not_found(message: str) -> JSONResponse:
    return JSONResponse(status_code=404, content={"ok": False, "message": message})


def _internal_error(message: str) -> JSONResponse:
    return JSONResponse(status_code=500, content={"ok": False, "message": message})


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


@router.get("/webui/api/groups")
async def webui_groups_list() -> JSONResponse:
    session = get_session()
    try:
        groups = session.query(Group).order_by(Group.name.asc()).all()
        user_count_map = _build_user_count_map(session)
        return JSONResponse(
            content={
                "ok": True,
                "groups": [
                    _serialize_group(item, user_count_map=user_count_map)
                    for item in groups
                ],
                "builtin_groups": list(_BUILTIN_GROUPS),
            }
        )
    except Exception as exc:
        return _internal_error(f"获取身份组列表失败：{exc}")
    finally:
        session.close()


@router.post("/webui/api/groups")
async def webui_groups_create(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return _bad_request("请求体必须是 JSON")
    if not isinstance(payload, dict):
        return _bad_request("请求体必须是对象")

    try:
        validated = _validate_create_payload(payload)
    except GroupPayloadValidationError as exc:
        return _unprocessable(str(exc), field=exc.field)

    session = get_session()
    try:
        exists = session.query(Group).filter(Group.name == validated.name).first()
        if exists is not None:
            return _unprocessable("身份组已存在", field="name")

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
        return JSONResponse(
            content={
                "ok": True,
                "message": "新增成功",
                "group": _serialize_group(group, user_count_map=user_count_map),
            }
        )
    except GroupPayloadValidationError as exc:
        session.rollback()
        return _unprocessable(str(exc), field=exc.field)
    except Exception as exc:
        session.rollback()
        return _internal_error(f"新增身份组失败：{exc}")
    finally:
        session.close()


@router.put("/webui/api/groups/{group_name}")
async def webui_groups_update(group_name: str, request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return _bad_request("请求体必须是 JSON")
    if not isinstance(payload, dict):
        return _bad_request("请求体必须是对象")

    session = get_session()
    try:
        group = session.query(Group).filter(Group.name == group_name).first()
        if group is None:
            return _not_found("更新失败，身份组不存在")

        try:
            validated = _validate_update_payload(
                payload,
                current=group,
                target_name=group_name,
            )
        except GroupPayloadValidationError as exc:
            return _unprocessable(str(exc), field=exc.field)

        _validate_inherits_targets(
            session,
            inherits=validated.inherits,
            self_name=group_name,
        )

        group.permissions = validated.permissions
        group.inherits = validated.inherits
        session.commit()

        user_count_map = _build_user_count_map(session)
        return JSONResponse(
            content={
                "ok": True,
                "message": "更新成功",
                "group": _serialize_group(group, user_count_map=user_count_map),
            }
        )
    except GroupPayloadValidationError as exc:
        session.rollback()
        return _unprocessable(str(exc), field=exc.field)
    except Exception as exc:
        session.rollback()
        return _internal_error(f"更新身份组失败：{exc}")
    finally:
        session.close()


@router.delete("/webui/api/groups/{group_name}")
async def webui_groups_delete(group_name: str) -> JSONResponse:
    if group_name in _BUILTIN_GROUPS:
        return _unprocessable("系统内置身份组不可删除", field="name")

    session = get_session()
    try:
        group = session.query(Group).filter(Group.name == group_name).first()
        if group is None:
            return _not_found("删除失败，身份组不存在")

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
        return JSONResponse(content={"ok": True, "message": "删除成功"})
    except Exception as exc:
        session.rollback()
        return _internal_error(f"删除身份组失败：{exc}")
    finally:
        session.close()
