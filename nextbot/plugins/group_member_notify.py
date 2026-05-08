from __future__ import annotations

from typing import Any

import nonebot
from nonebot import on_notice
from nonebot.adapters import Bot
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

from nextbot.access_control import get_group_ids, get_owner_ids
from nextbot.ban_core import (
    apply_ban_to_db,
    format_blacklist_add_lines,
    sync_user_to_blacklist,
)
from nextbot.db import User, get_session
from nextbot.text_utils import EMOJI_USER, reply_success

increase_matcher = on_notice()
decrease_matcher = on_notice()
auto_ban_on_leave_matcher = on_notice()

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
        if chunk:
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
async def handle_group_increase(bot: Bot, event: GroupIncreaseNoticeEvent) -> None:
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
async def handle_group_decrease(bot: Bot, event: GroupDecreaseNoticeEvent) -> None:
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


def _lookup_user_name_and_ban_status(user_id: str) -> tuple[str | None, bool]:
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user is None:
            return None, False
        return user.name, bool(user.is_banned)
    finally:
        session.close()


@auto_ban_on_leave_matcher.handle()
async def handle_auto_ban_on_leave(bot: Bot, event: GroupDecreaseNoticeEvent) -> None:
    if not isinstance(bot, OBV11Bot):
        return
    if not _group_allowed(event.group_id):
        return
    config = nonebot.get_driver().config
    if not bool(getattr(config, "group_auto_ban_on_leave_enabled", False)):
        return

    user_id = str(event.user_id)
    if user_id in get_owner_ids():
        logger.info(
            f"退群自动封禁跳过 Owner：group_id={event.group_id}，user_id={user_id}"
        )
        return

    user_name, already_banned = _lookup_user_name_and_ban_status(user_id)
    if user_name is None:
        logger.info(
            f"退群自动封禁跳过未注册用户：group_id={event.group_id}，user_id={user_id}"
        )
        return
    if already_banned:
        logger.info(
            f"退群自动封禁跳过已封禁用户：group_id={event.group_id}，user_id={user_id}"
        )
        return

    sub_type = str(event.sub_type or "")
    reason = f"{_AUTO_BAN_REASON}（{sub_type}）" if sub_type else _AUTO_BAN_REASON

    result = apply_ban_to_db(user_id, reason)
    if result.code != "banned":
        logger.warning(
            f"退群自动封禁未落库：group_id={event.group_id}，user_id={user_id}，code={result.code}"
        )
        return

    outcomes = await sync_user_to_blacklist(result.user_name, reason)
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
    ]
    lines.extend(format_blacklist_add_lines(outcomes))
    try:
        await bot.call_api(
            "send_group_msg", group_id=event.group_id, message="\n".join(lines)
        )
    except Exception as exc:
        logger.warning(
            f"退群自动封禁通知发送失败：group_id={event.group_id}，user_id={user_id}，reason={exc}"
        )
