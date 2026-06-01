"""关键词自动回复 plugin。

注册一个低优先级（priority=100）的 on_message matcher，对所有走过
bot.py 门面（白名单群 / 白名单群临时会话 / owner 好友私聊）的消息做
不区分大小写的"包含匹配"，命中第一条 enabled 规则后按规则配置回复。

规则数据通过 WebUI CRUD 写入 ``keyword_reply`` 表，运行时通过 30s TTL
缓存 + WebUI 写后主动 invalidate 双保险拉取。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from nonebot import on_message
from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import GroupMessageEvent as OBV11GroupMessageEvent
from nonebot.adapters.onebot.v11 import Message as OBV11Message
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger

from nextbot.db import KeywordReply, get_session

# 30 秒 TTL：缓存避免每条消息都查 DB，同时让 WebUI 即使忘记调
# invalidate_cache() 也能在 30s 内把变更反映到 handler。
_CACHE_TTL_SECONDS = 30.0


@dataclass(frozen=True)
class _Rule:
    """ORM 行的不可变快照，避免 session.close() 后访问 ORM 对象触发
    DetachedInstanceError，且天然线程/协程安全（frozen dataclass 不可变）。
    """

    id: int
    keyword: str
    reply: str
    at_user: bool
    quote_reply: bool


_cache: tuple[float, list[_Rule]] | None = None


def _load_rules_from_db() -> list[_Rule]:
    """从 DB 拉所有 enabled=True 的规则，按 created_at ASC 排序。

    早创建的规则优先命中（与 PRD"同关键词只第一条生效"语义一致）。
    每行立刻拷贝为 frozen ``_Rule``，cache 不持有 ORM 对象。
    """
    session = get_session()
    try:
        rows = (
            session.query(KeywordReply)
            .filter(KeywordReply.enabled.is_(True))
            .order_by(KeywordReply.created_at.asc())
            .all()
        )
        return [
            _Rule(
                id=int(row.id),
                keyword=str(row.keyword or ""),
                reply=str(row.reply or ""),
                at_user=bool(row.at_user),
                quote_reply=bool(row.quote_reply),
            )
            for row in rows
        ]
    finally:
        session.close()


def _get_rules_cached() -> list[_Rule]:
    """获取缓存的规则列表；超过 TTL 或未初始化时重新加载。

    单 event loop 模型，cache 读写不会撕裂；不加锁以避免不必要的开销。
    """
    global _cache
    now = time.monotonic()
    if _cache is None or (now - _cache[0]) > _CACHE_TTL_SECONDS:
        try:
            rules = _load_rules_from_db()
        except Exception as exc:  # noqa: BLE001
            # DB 异常时降级到空规则，并保留旧缓存（如有）避免每条消息都报错。
            logger.warning(
                f"加载自动回复规则失败：reason={exc!r}（降级到空规则集）"
            )
            if _cache is not None:
                return _cache[1]
            return []
        _cache = (now, rules)
    return _cache[1]


def invalidate_cache() -> None:
    """主动失效缓存：WebUI CRUD endpoint 写完 DB 后调，下次 handler
    进入时立即重新加载，无需等 30s TTL。"""
    global _cache
    _cache = None


def _mask_user_id(user_id: str) -> str:
    """与 webui_users.py 的 _mask_qq 行为一致：保留首尾 2 位，中间打码。

    本地写一份避免跨 plugin 引用（user_manager.py 的内部 helper 不应被
    其它 plugin 当公共 API 依赖）。
    """
    text = str(user_id or "")
    if len(text) < 4:
        return text
    return text[:2] + "***" + text[-2:]


auto_reply_matcher = on_message(priority=100, block=False)


@auto_reply_matcher.handle()
async def handle_auto_reply(bot: Bot, event: Event) -> None:
    # 仅处理 message 事件；其它类型由 event_preprocessor 兜底过滤。
    try:
        text = event.get_message().extract_plain_text().strip()
    except Exception:  # noqa: BLE001
        # 极端：消息体异常无法提取纯文本（例如纯图片消息），跳过。
        return

    if not text:
        return

    # 命令消息跳过：以 / 开头视为命令尝试，不触发自动回复。
    # PRD 简化做法：覆盖 99% 场景。`/签到` `/在线` 等命令都以 / 开头；
    # 部分无前缀别名（如 "签到"）也由优先级更低的命令 matcher 先拦截，
    # 走 on_message(priority=100, block=False) 仍可能进入本 handler，但
    # 此时命令本身已经处理过，本 handler 命中后再追加一条自动回复属于
    # 预期外冗余 —— 本期接受该极小概率，PRD 已注明可后续完善。
    if text.startswith("/"):
        return

    rules = _get_rules_cached()
    if not rules:
        return

    text_lower = text.lower()
    matched_rule: _Rule | None = None
    for rule in rules:
        keyword_lower = rule.keyword.strip().lower()
        if not keyword_lower:
            continue
        if keyword_lower in text_lower:
            matched_rule = rule
            break

    if matched_rule is None:
        return

    is_group_event = isinstance(event, OBV11GroupMessageEvent)
    message = OBV11Message()

    if matched_rule.quote_reply:
        # 取 event.message_id 构造 OneBot v11 reply 段；非 OneBot 适配
        # 器（例如 console）可能没有 message_id，做防御性处理。
        message_id = getattr(event, "message_id", None)
        if message_id is not None:
            message += OBV11MessageSegment.reply(message_id)

    if matched_rule.at_user and is_group_event:
        # 仅群消息追加 @<user>；私聊场景 @ 无意义，PRD 明确要求跳过。
        user_id_raw = event.get_user_id()
        try:
            at_target: int | str = int(user_id_raw)
        except (TypeError, ValueError):
            at_target = user_id_raw
        message += OBV11MessageSegment.at(at_target)
        # @ 与回复文本之间补一个空格，避免黏连。
        message += OBV11MessageSegment.text(" " + matched_rule.reply)
    else:
        message += OBV11MessageSegment.text(matched_rule.reply)

    try:
        await bot.send(event, message)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"自动回复发送失败：rule_id={matched_rule.id} "
            f"keyword={matched_rule.keyword!r} reason={exc!r}"
        )
        return

    group_id = getattr(event, "group_id", None)
    logger.info(
        f"自动回复触发：rule_id={matched_rule.id} "
        f"keyword={matched_rule.keyword!r} "
        f"user_id={_mask_user_id(event.get_user_id())} "
        f"group_id={group_id} matched_text_len={len(text)}"
    )
