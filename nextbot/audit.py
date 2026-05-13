"""权限审计统一日志入口。

所有 permission-mutating handler 应通过 audit_permission_change() 记录变更，
便于事故回查时按 actor / target / action 聚合。

格式：
  [WARN] 权限审计：actor=<qq> action=<verb> target=<group_name|user_id>
         before=<repr> after=<repr> context=<repr>

WARN 级别：让审计行在 INFO 主流量中突出，不代表"出错"。
"""
from __future__ import annotations

from typing import Any

from nonebot.log import logger

# P-1.9：限制 audit 入参类型避免 ORM 对象 repr 泄漏敏感列。
_ALLOWED_SNAPSHOT_TYPES: tuple[type, ...] = (
    str, int, bool, float, dict, list, tuple, type(None),
)


def _safe_repr(value: Any) -> str:
    """对值取 repr 后转义换行/回车，避免攻击者通过用户可控字段
    （user.name / ban_reason 等）注入伪造的审计行污染日志流。"""
    return repr(value).replace("\n", "\\n").replace("\r", "\\r")


def _coerce_snapshot(name: str, value: Any, actor_user_id: str, action: str) -> Any:
    """P-1.9：runtime 校验 before / after 类型。

    only accept primitive / dict / list / tuple / None；遇到 ORM 对象等
    超范围类型时记 ERROR 并 str(...) 兜底，防止 __repr__ 把 password_hash /
    email / ban_reason 等 internal 列写入审计日志。
    """
    if isinstance(value, _ALLOWED_SNAPSHOT_TYPES):
        return value
    logger.error(
        f"audit_permission_change 收到非预期类型，强制 str：actor={actor_user_id} "
        f"action={action} field={name} type={type(value).__name__}"
    )
    return str(value)


def audit_permission_change(
    *,
    actor_user_id: str,
    action: str,
    target: str,
    before: Any | None = None,
    after: Any | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    """统一审计权限变更事件。

    Args:
        actor_user_id: 触发变更的用户 QQ。
        action: 动作 verb，如 "group.add" / "group.delete" /
            "group.permission.add" / "user.group.set"。
        target: 目标对象（身份组名或目标用户 QQ）。
        before: 变更前状态快照（CSV 字符串、dict 或其他可 repr 对象）。
        after: 变更后状态快照。
        context: 额外上下文，例如 cascade counts、原因、备注等。
    """
    if before is not None:
        before = _coerce_snapshot("before", before, actor_user_id, action)
    if after is not None:
        after = _coerce_snapshot("after", after, actor_user_id, action)

    parts = [
        f"actor={actor_user_id}",
        f"action={action}",
        f"target={target}",
    ]
    if before is not None:
        parts.append(f"before={_safe_repr(before)}")
    if after is not None:
        parts.append(f"after={_safe_repr(after)}")
    if context:
        parts.append(f"context={_safe_repr(context)}")
    logger.warning(f"权限审计：{' '.join(parts)}")
