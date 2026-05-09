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
    parts = [
        f"actor={actor_user_id}",
        f"action={action}",
        f"target={target}",
    ]
    if before is not None:
        parts.append(f"before={before!r}")
    if after is not None:
        parts.append(f"after={after!r}")
    if context:
        parts.append(f"context={context!r}")
    logger.warning(f"权限审计：{' '.join(parts)}")
