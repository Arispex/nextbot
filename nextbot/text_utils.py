from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nonebot.adapters import Event

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


def at_prefix(event: "Event", content: Any, *, sep: str = " ") -> Any:
    """在 reply 内容前面拼上 @<发送者> 前缀。

    sep 默认是单空格（用于行内 reply_failure / reply_success 等单行回复）；
    多行 reply_block 输出建议传 sep="\\n" 以让正文从下一行开始。

    放在这里而不是 server_tools.py 里是因为同样的拼装模式分散在 ≥ 8 处 handler，
    集中后调整 separator / 适配器都只需改一处。OBV11 import 延迟到调用时，避免
    text_utils 在测试或非 OBV11 环境下被强制依赖 OneBot 包。
    """
    from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment

    return OBV11MessageSegment.at(int(event.get_user_id())) + sep + content
