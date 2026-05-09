from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nonebot.log import logger

if TYPE_CHECKING:
    from nonebot.adapters import Event
    from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment

# 状态 emoji（固定语义）
STATUS_SUCCESS = "✅"
STATUS_FAILURE = "❌"
STATUS_WARNING = "⚠️"
STATUS_INFO = "ℹ️"
STATUS_HINT = "💡"

# 场景 emoji（按业务复用）
EMOJI_LIST = "📋"
EMOJI_USER = "👤"
EMOJI_GROUP = "👥"
EMOJI_COIN = "💰"
EMOJI_RED_PACKET = "🧧"
EMOJI_GAME = "🎲"
EMOJI_FIRE = "🔥"
EMOJI_TIME = "⏰"
EMOJI_BAN = "🚫"
EMOJI_LOCK = "🔒"
EMOJI_SECURE = "🔐"
EMOJI_SERVER = "🖥️"
EMOJI_TARGET = "🎯"
EMOJI_CHART = "📊"
EMOJI_CALENDAR = "📅"
EMOJI_GUIDE = "📚"
EMOJI_WAREHOUSE = "📦"
EMOJI_SHOP = "🛒"
EMOJI_LOTTERY = "🎰"


def reply_success(action: str, detail: str | None = None) -> str:
    text = f"{STATUS_SUCCESS} {action}成功"
    if detail:
        text += f"，{detail}"
    return text


def reply_failure(action: str, reason: str) -> str:
    return f"{STATUS_FAILURE} {action}失败，{reason}"


def reply_warning(text: str) -> str:
    return f"{STATUS_WARNING} {text}"


def reply_info(text: str) -> str:
    return f"{STATUS_INFO} {text}"


def reply_hint(text: str) -> str:
    return f"{STATUS_HINT} {text}"


def reply_block(
    head: str,
    lines: list[str] | None = None,
    *,
    hint: str | None = None,
) -> str:
    parts = [head]
    if lines:
        parts.extend(lines)
    if hint:
        parts.append(reply_hint(hint))
    return "\n".join(parts)


def reply_list(
    title: str,
    items: list[str],
    *,
    title_emoji: str = EMOJI_LIST,
    hint: str | None = None,
) -> str:
    return reply_block(f"{title_emoji} {title}", items, hint=hint)


def safe_at_segment(user_id: str) -> "OBV11MessageSegment | None":
    """把 OBV11MessageSegment.at(int(user_id)) 包一层异常防御。

    PC-4.1：项目目前主要用 OBV11，user_id 总是数字；但非 V11 适配器
    （V11-shim / Telegram bridge / 自研协议）若 push 非数字 user_id，
    此处返回 None，调用方退化为不带 @ 的发送。

    OBV11 import 延迟到调用时，避免 text_utils 在测试或非 OBV11 环境下
    被强制依赖 OneBot 包。
    """
    from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment

    try:
        return OBV11MessageSegment.at(int(user_id))
    except (TypeError, ValueError):
        logger.warning(f"无法将 user_id 解析为整数 @ 段：user_id={user_id}")
        return None


def safe_at_segment_or_empty(user_id: str) -> "OBV11MessageSegment":
    """与 ``safe_at_segment`` 相同的防御，但 None 时返回空 text 段，便于直接
    参与 ``at + " " + content`` 拼装而无需调用方 None-check。

    PC-4.1：项目内 ≥17 处 handler 直接用 ``OBV11MessageSegment.at(int(user_id))``
    并赋值给本地 ``at`` 变量后做字符串拼装；这些 callsite 不便迁到 ``at_prefix``
    （结构上只有部分回复需要 @ 前缀）。本 helper 让其能以最小改动接入防御。
    """
    from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment

    seg = safe_at_segment(user_id)
    if seg is None:
        return OBV11MessageSegment.text("")
    return seg


def at_prefix(event: "Event", content: Any, *, sep: str = " ") -> Any:
    """在 reply 内容前面拼上 @<发送者> 前缀。

    sep 默认是单空格（用于行内 reply_failure / reply_success 等单行回复）；
    多行 reply_block 输出建议传 sep="\\n" 以让正文从下一行开始。

    放在这里而不是 server_tools.py 里是因为同样的拼装模式分散在 ≥ 8 处 handler，
    集中后调整 separator / 适配器都只需改一处。OBV11 import 延迟到调用时，避免
    text_utils 在测试或非 OBV11 环境下被强制依赖 OneBot 包。

    PC-4.1：内部使用 safe_at_segment，user_id 非数字时退化为不带 @ 的内容直发。
    """
    at_seg = safe_at_segment(event.get_user_id())
    if at_seg is None:
        return content
    return at_seg + sep + content
