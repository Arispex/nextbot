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
        return _get_effective_permissions_in_session(session, user_id)
    finally:
        session.close()


def _get_effective_permissions_in_session(session, user_id: str) -> set[str]:
    """同 get_effective_permissions，但复用调用方传入的 session。

    用于 handler 在已开 transaction 时查询 effective perms（例如
    层级护栏校验），避免 BEGIN IMMEDIATE 下嵌套 get_session() 死锁。
    """
    user = session.query(User).filter(User.user_id == user_id).first()
    if user is None:
        group_name = "guest"
        user_perms: set[str] = set()
    else:
        group_name = user.group or "guest"
        user_perms = set(split_csv_values(user.permissions))

    group_perms = _get_group_permissions(session, group_name, set())
    return user_perms | group_perms


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


def is_owner(user_id: str) -> bool:
    """是否为 .env 配置的 owner。

    所有 POLA / blocklist / 层级护栏 / registry 校验在 owner 调用方
    都应短路放行——owner 是 .env 短路而非 DB 组，行级 mutation 对其
    实际有效权限无影响。
    """
    return user_id in get_owner_ids()


# 仅 owner 可授予的危险权限。包含完全匹配 + 通配匹配。
# 此列表故意保守：任何能用来 (a) 修改其他用户的权限 / 身份组，或
# (b) 修改组的权限 / 继承关系，或 (c) 创建 / 删除组的 key 都进 blocklist。
DANGEROUS_PERMISSION_PREFIXES: frozenset[str] = frozenset({
    "permission.user.add",
    "permission.user.remove",
    "permission.user.group.set",
    "permission.group.guest.sync",
    "permission.group.guest.reset",
    "group.permission.add",
    "group.permission.remove",
    "group.add",
    "group.delete",
    "group.inherit.add",
    "group.inherit.clear",
})


def is_dangerous_permission(permission: str) -> bool:
    """检查权限 key 是否在 owner-only blocklist。

    规则：
    - 万能权限 ``*`` → True（仅 owner 能授）
    - 完全匹配 dangerous key → True
    - ``.* `` 通配后缀：去掉 ``*`` 后剩下的 prefix 若覆盖任何 dangerous key
      则为 True（例如 ``permission.*`` 会覆盖 ``permission.user.add``）
    """
    if permission == "*":
        return True
    if permission in DANGEROUS_PERMISSION_PREFIXES:
        return True
    if permission.endswith(".*"):
        prefix = permission[:-1]  # "permission.*" → "permission."
        for danger in DANGEROUS_PERMISSION_PREFIXES:
            if danger.startswith(prefix):
                return True
    return False


# 软警告阈值：继承链深度 > 8 即拒绝新边，避免误操作建超长链导致
# get_effective_permissions 性能退化。
MAX_INHERIT_DEPTH = 8


def _would_create_inheritance_cycle(session, child: str, new_parent: str) -> bool:
    """检测新增 ``child → new_parent`` 边后是否形成环。

    DFS 从 new_parent 沿 inherits 链向上遍历，若遇到 child 即说明
    新边构成环（child 已经是 new_parent 的祖先）。

    Args:
        session: 当前 SQLAlchemy session（在 commit 前调用）
        child: 即将添加 inherits 的子组名
        new_parent: 新增的父组名
    """
    if child == new_parent:
        return True
    stack: list[str] = [new_parent]
    visited: set[str] = set()
    while stack:
        node = stack.pop()
        if node == child:
            return True
        if node in visited:
            continue
        visited.add(node)
        group = session.query(Group).filter(Group.name == node).first()
        if group is None:
            continue
        stack.extend(split_csv_values(group.inherits))
    return False


def _measure_inherit_depth(session, group_name: str, visited: set[str]) -> int:
    """递归测量某组继承链最深路径长度（含自身）。

    用于 MAX_INHERIT_DEPTH 软警告。visited 集合避免循环时栈溢出。
    """
    if group_name in visited:
        return 0
    visited.add(group_name)
    group = session.query(Group).filter(Group.name == group_name).first()
    if group is None:
        return 0
    parents = split_csv_values(group.inherits)
    if not parents:
        return 1
    return 1 + max(
        _measure_inherit_depth(session, parent, visited.copy()) for parent in parents
    )


def validate_permission_key(key: str) -> bool:
    """检查 permission key 是否已知（来自 command_config 注册表）。

    规则：
    - 完全匹配 ``_registry`` 中的 key → True
    - ``.* `` 后缀通配：prefix 在任何已知 key 中存在即 True
      （例如 ``economy.*`` 会匹配 ``economy.signin``）
    - owner 调用方应在 handler 处用 is_owner() 短路跳过此校验，
      避免 forward-compat 时 owner 无法授予即将上线的新 key。
    """
    # 延迟 import 避免与 command_config.py 形成 import-time 循环
    from nextbot.command_config import get_permission_registry

    registry = get_permission_registry()
    if key in registry:
        return True
    if key == "*":
        return False
    if key.endswith(".*"):
        prefix = key[:-1]  # "economy.*" → "economy."
        return any(known.startswith(prefix) for known in registry)
    return False


def suggest_permission_keys(key: str, *, n: int = 3) -> list[str]:
    """typo 时的"是否想说"建议。"""
    import difflib

    from nextbot.command_config import get_permission_registry

    registry = get_permission_registry()
    return difflib.get_close_matches(key, sorted(registry), n=n, cutoff=0.6)


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
