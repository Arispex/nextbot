from __future__ import annotations

from typing import Iterable

from nonebot.log import logger

from nextbot.access_control import get_owner_ids
from nextbot.db import Group, User, get_session


def split_csv_values(value: str) -> list[str]:
    return [item for item in (v.strip() for v in value.split(",")) if item]


def join_csv_values(values: Iterable[str]) -> str:
    return ",".join(sorted(set(values)))


def _match_permission(granted: str, required: str) -> bool:
    if granted.endswith(".*"):
        prefix = granted[:-1]
        return required.startswith(prefix)
    return granted == required


def get_effective_permissions(user_id: str) -> set[str]:
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user is None:
            group_name = "guest"
            user_perms: set[str] = set()
        else:
            group_name = user.group or "guest"
            user_perms = set(split_csv_values(user.permissions))

        group_perms = _get_group_permissions(session, group_name, set())
        return user_perms | group_perms
    finally:
        session.close()


def _get_group_permissions(
    session,
    group_name: str,
    visited: set[str],
) -> set[str]:
    if group_name in visited:
        return set()
    visited.add(group_name)

    group = session.query(Group).filter(Group.name == group_name).first()
    if group is None:
        return set()

    perms = set(split_csv_values(group.permissions))
    for parent in split_csv_values(group.inherits):
        perms |= _get_group_permissions(session, parent, visited)
    return perms


def has_permission(user_id: str, permission: str) -> bool:
    owner_ids = get_owner_ids()
    if user_id in owner_ids:
        return True
    perms = get_effective_permissions(user_id)
    return any(_match_permission(granted, permission) for granted in perms)


def require_permission(permission: str):
    def decorator(func):
        import inspect
        import typing
        from functools import wraps

        signature = inspect.signature(func)
        try:
            # include_extras=True preserves Annotated metadata (e.g. NoneBot2's
            # `T_State = Annotated[Dict, _STATE_FLAG]`) so downstream injectors
            # can still recognize the parameter after we rebuild the signature.
            type_hints = typing.get_type_hints(func, include_extras=True)
        except Exception:
            type_hints = {}

        parameters = [
            parameter.replace(
                annotation=type_hints.get(parameter.name, parameter.annotation)
            )
            for parameter in signature.parameters.values()
        ]
        resolved_signature = signature.replace(
            parameters=parameters,
            return_annotation=type_hints.get("return", signature.return_annotation),
        )

        @wraps(func)
        async def wrapper(*args, **kwargs):
            bound = resolved_signature.bind_partial(*args, **kwargs)
            bot = bound.arguments.get("bot")
            event = bound.arguments.get("event")
            if bot is None or event is None:
                return await func(*args, **kwargs)

            user_id = event.get_user_id()
            if not has_permission(user_id, permission):
                logger.info(
                    f"权限不足：user_id={user_id} permission={permission}"
                )
                await bot.send(event, f"🔒 没有权限，需要权限：{permission}")
                return
            return await func(*args, **kwargs)

        setattr(wrapper, "__signature__", resolved_signature)
        return wrapper

    return decorator


def add_permission(value: str, permission: str) -> str:
    perms = set(split_csv_values(value))
    perms.add(permission)
    return join_csv_values(perms)


def remove_permission(value: str, permission: str) -> str:
    perms = set(split_csv_values(value))
    perms.discard(permission)
    return join_csv_values(perms)


def add_inherit(value: str, parent: str) -> str:
    parents = set(split_csv_values(value))
    parents.add(parent)
    return join_csv_values(parents)


def remove_inherit(value: str, parent: str) -> str:
    parents = set(split_csv_values(value))
    parents.discard(parent)
    return join_csv_values(parents)
