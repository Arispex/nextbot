from __future__ import annotations

from typing import Any

import nonebot
from nonebot import on_notice
from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import (
    Bot as OBV11Bot,
)
from nonebot.adapters.onebot.v11 import (
    GroupDecreaseNoticeEvent,
    GroupIncreaseNoticeEvent,
)
from nonebot.adapters.onebot.v11 import (
    Message as OBV11Message,
)
from nonebot.adapters.onebot.v11 import (
    MessageSegment as OBV11MessageSegment,
)
from nonebot.log import logger
from nonebot.rule import Rule

from nextbot.access_control import get_group_ids
from nextbot.audit import audit_permission_change
from nextbot.ban_core import apply_ban_to_db
from nextbot.sync_orchestrator import (
    format_sync_outcomes_for_user,
    trigger_sync_all_servers,
)
from nextbot.text_utils import EMOJI_USER, reply_success


# MI-5.1：on_notice() 不带 rule 时会被任何 NoticeEvent 触发，nonebot 类型注解
# 只做 dependency hint 不会过滤事件。这里用 Rule 显式过滤，避免每条 friend_add /
# poke / honor 都进入 3 个 handler 的 dispatch 阶段。
async def _is_increase(event: Event) -> bool:
    return isinstance(event, GroupIncreaseNoticeEvent)


async def _is_decrease(event: Event) -> bool:
    return isinstance(event, GroupDecreaseNoticeEvent)


increase_matcher = on_notice(rule=Rule(_is_increase))
decrease_matcher = on_notice(rule=Rule(_is_decrease))
auto_ban_on_leave_matcher = on_notice(rule=Rule(_is_decrease))

_AUTO_BAN_REASON = "退群自动封禁"


def _group_allowed(group_id: int) -> bool:
    allowed = {g.strip() for g in get_group_ids() if g.strip().isdigit()}
    return str(group_id) in allowed


def _unescape(value: str) -> str:
    return value.replace("\\\\", "\x00").replace("\\n", "\n").replace("\x00", "\\")


def _load_template(field: str) -> str:
    config = nonebot.get_driver().config
    raw = str(getattr(config, field, "") or "")
    return _unescape(raw).strip()


async def _fetch_nickname(bot: OBV11Bot, user_id: int) -> str:
    try:
        info: Any = await bot.call_api("get_stranger_info", user_id=user_id, no_cache=True)
    except Exception as exc:
        logger.warning(f"拉取 QQ 昵称失败：user_id={user_id}，reason={exc}")
        return ""
    if isinstance(info, dict):
        return str(info.get("nickname") or "").strip()
    return ""


def _render(template: str, *, user_id: int, nickname: str) -> OBV11Message:
    display_nick = nickname or str(user_id)
    text = template.replace("{nickname}", display_nick).replace("{user_id}", str(user_id))
    parts = text.split("{at}")
    message = OBV11Message()
    for i, chunk in enumerate(parts):
        # MI-5.5：strip() 后才判定空，避免纯空白 chunk（如模板末尾的 \n）也作为 text 段发出
        if chunk.strip():
            message += OBV11MessageSegment.text(chunk)
        if i < len(parts) - 1:
            message += OBV11MessageSegment.at(user_id)
    return message


async def _send_group_notify(
    bot: Bot,
    group_id: int,
    user_id: int,
    template: str,
    *,
    event_label: str,
) -> None:
    if not isinstance(bot, OBV11Bot):
        return
    if not _group_allowed(group_id):
        return
    if not template:
        return

    nickname = await _fetch_nickname(bot, user_id)
    message = _render(template, user_id=user_id, nickname=nickname)
    if not message:
        return

    try:
        await bot.call_api("send_group_msg", group_id=group_id, message=message)
    except Exception as exc:
        logger.warning(
            f"发送{event_label}消息失败：group_id={group_id}，user_id={user_id}，reason={exc}"
        )
        return
    logger.info(
        f"发送{event_label}消息成功：group_id={group_id}，user_id={user_id}，nickname={nickname}"
    )


@increase_matcher.handle()
async def handle_group_increase(bot: Bot, event: Event) -> None:
    # MI-5.4：rule 已过滤，但加 isinstance 守卫作 defense-in-depth
    if not isinstance(event, GroupIncreaseNoticeEvent):
        return
    config = nonebot.get_driver().config
    if not bool(getattr(config, "group_welcome_enabled", False)):
        return
    template = _load_template("group_welcome_template")
    await _send_group_notify(
        bot,
        group_id=event.group_id,
        user_id=event.user_id,
        template=template,
        event_label="入群欢迎",
    )


@decrease_matcher.handle()
async def handle_group_decrease(bot: Bot, event: Event) -> None:
    if not isinstance(event, GroupDecreaseNoticeEvent):
        return
    config = nonebot.get_driver().config
    if not bool(getattr(config, "group_farewell_enabled", False)):
        return
    template = _load_template("group_farewell_template")
    await _send_group_notify(
        bot,
        group_id=event.group_id,
        user_id=event.user_id,
        template=template,
        event_label="退群送别",
    )


@auto_ban_on_leave_matcher.handle()
async def handle_auto_ban_on_leave(bot: Bot, event: Event) -> None:
    # MI-5.4：rule 已过滤，但加 isinstance 守卫作 defense-in-depth
    if not isinstance(event, GroupDecreaseNoticeEvent):
        return
    if not isinstance(bot, OBV11Bot):
        return
    if not _group_allowed(event.group_id):
        return
    config = nonebot.get_driver().config
    if not bool(getattr(config, "group_auto_ban_on_leave_enabled", False)):
        return

    user_id = str(event.user_id)
    sub_type = str(event.sub_type or "")
    reason = f"{_AUTO_BAN_REASON}（{sub_type}）" if sub_type else _AUTO_BAN_REASON

    # MI-5.3：删除前置 _lookup_user_name_and_ban_status SELECT，直接调 apply_ban_to_db；
    # 内部已通过条件 UPDATE 兜底 owner_protected / not_found / already_banned race condition。
    result = apply_ban_to_db(user_id, reason)
    if result.code == "not_found":
        logger.info(
            f"退群自动封禁跳过未注册用户：group_id={event.group_id}，user_id={user_id}"
        )
        return
    if result.code == "owner_protected":
        logger.info(
            f"退群自动封禁跳过 Owner：group_id={event.group_id}，user_id={user_id}"
        )
        return
    if result.code == "already_banned":
        logger.info(
            f"退群自动封禁跳过已封禁用户：group_id={event.group_id}，user_id={user_id}"
        )
        return
    if result.code != "banned":
        # 防御未来新增的状态码
        logger.warning(
            f"退群自动封禁未落库（未知 code）：group_id={event.group_id}，"
            f"user_id={user_id}，code={result.code}"
        )
        return

    # MI-5.2：被动事件触发的 ban 是最敏感的状态变更，必须走统一审计入口
    audit_permission_change(
        actor_user_id="system",
        action="user.ban.auto_on_leave",
        target=user_id,
        before={"is_banned": False},
        after={"is_banned": True, "ban_reason": reason},
        context={
            "group_id": event.group_id,
            "sub_type": sub_type,
            "user_name": result.user_name,
        },
    )

    sync_outcomes = await trigger_sync_all_servers(caller="auto_ban_on_leave")
    logger.info(
        f"退群自动封禁完成：group_id={event.group_id}，user_id={user_id}，"
        f"name={result.user_name}，sub_type={sub_type}"
    )

    if not bool(getattr(config, "group_auto_ban_on_leave_notify", False)):
        return

    lines = [
        reply_success("封禁"),
        f"{EMOJI_USER} 用户：{result.user_name}（{result.user_qq}）",
        f"📋 原因：{reason}",
        format_sync_outcomes_for_user(sync_outcomes),
    ]
    try:
        await bot.call_api(
            "send_group_msg", group_id=event.group_id, message="\n".join(lines)
        )
    except Exception as exc:
        logger.warning(
            f"退群自动封禁通知发送失败：group_id={event.group_id}，user_id={user_id}，reason={exc}"
        )
