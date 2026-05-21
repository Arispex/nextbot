import json
import nonebot
from nonebot.adapters.console import Adapter as ConsoleAdapter
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.adapters import Bot, Event
from nonebot.exception import IgnoredException
from nonebot.log import logger
from nonebot.message import event_preprocessor

from nextbot.command_config import sync_registered_commands_to_db
from nextbot.data_dir import DATA_DIR
from server.web_server import start_web_server
from nextbot.access_control import get_group_ids, get_owner_ids
from nextbot.db import (
    DB_PATH,
    init_db,
)

ENV_PATH = DATA_DIR / ".env"
DEFAULT_ENV_CONTENT = (
    "DRIVER=~websockets\n"
    "LOCALSTORE_USE_CWD=true\n"
    "\n"
    "COMMAND_START=[\"/\", \"\"]\n"
    "\n"
    "ONEBOT_WS_URLS=[]\n"
    "ONEBOT_ACCESS_TOKEN=MyOneBotAccessToken\n"
    "\n"
    "OWNER_ID=[]\n"
    "GROUP_ID=[]\n"
    "\n"
    "WEB_SERVER_HOST=0.0.0.0\n"
    "WEB_SERVER_PORT=18081\n"
    "WEB_SERVER_PUBLIC_BASE_URL=http://127.0.0.1:18081\n"
    "COMMAND_DISABLED_MODE=reply\n"
    "COMMAND_DISABLED_MESSAGE=该命令暂时关闭~\n"
    "LOGIN_NOTIFY_ALL_GROUPS=false\n"
    "PLAYER_NOTIFY_MODE=all\n"
    "PLAYER_NOTIFY_GROUP_ID=\n"
    "PLAYER_NOTIFY_ONLINE_TEMPLATE=[{server}]{player} 上线了\n"
    "PLAYER_NOTIFY_OFFLINE_TEMPLATE=[{server}]{player} 下线了\n"
    "CHAT_SYNC_MODE=all\n"
    "CHAT_SYNC_GROUP_ID=\n"
    "CHAT_SYNC_TEMPLATE=[{server}]{player}：{message}\n"
    "GROUP_WELCOME_ENABLED=false\n"
    "GROUP_WELCOME_TEMPLATE={at} 欢迎加入本群！\\n请先阅读群公告~\n"
    "GROUP_FAREWELL_ENABLED=false\n"
    "GROUP_FAREWELL_TEMPLATE={nickname}（{user_id}）离开了本群\n"
    "GROUP_AUTO_BAN_ON_LEAVE_ENABLED=false\n"
    "GROUP_AUTO_BAN_ON_LEAVE_NOTIFY=false\n"
)


def _has_onebot_ws_urls() -> bool:
    raw_value = getattr(nonebot.get_driver().config, "onebot_ws_urls", None)
    if raw_value is None:
        return False

    if isinstance(raw_value, (list, tuple, set)):
        return any(str(item).strip() for item in raw_value)

    text = str(raw_value).strip()
    if not text:
        return False

    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return bool(text)
        if isinstance(parsed, list):
            return any(str(item).strip() for item in parsed)

    return bool(text)


def ensure_env_file() -> None:
    if ENV_PATH.exists():
        return

    try:
        ENV_PATH.write_text(DEFAULT_ENV_CONTENT, encoding="utf-8")
    except OSError as exc:
        logger.error(
            f".env 创建失败（可能权限不足 / 磁盘满 / RO mount）：path={ENV_PATH} reason={exc}"
        )
        return

    logger.warning(f".env 不存在，已创建默认 .env 文件：{ENV_PATH}")


ensure_env_file()
# Explicitly point NoneBot at our resolved .env path. By default NoneBot
# reads `.env` from the current working directory, which would miss
# NEXTBOT_DATA_DIR-relocated state in containerised deployments.
nonebot.init(_env_file=str(ENV_PATH))

driver = nonebot.get_driver()
# driver.register_adapter(ConsoleAdapter)
if _has_onebot_ws_urls():
    driver.register_adapter(OneBotV11Adapter)
    logger.info("检测到 OneBot WS 配置，已启用 OneBot V11 适配器")
else:
    logger.warning("未配置 ONEBOT_WS_URLS，已跳过 OneBot V11 连接")


def _extract_temp_source_group_id(event: Event) -> str | None:
    """从 OneBot v11 PrivateMessageEvent 中提取群临时会话的来源群 ID。

    OneBot v11 spec 临时会话事件（sub_type='group'）没有顶层 group_id 字段，
    但 NapCat / Lagrange 等实现把它放在 sender.group_id（Sender pydantic
    extra='allow' 保留扩展字段）；少数实现放在事件顶层 group_id。本 helper
    依次尝试两个位置，返回非空 str 或 None。'0' 视为 absent。
    """
    sender = getattr(event, "sender", None)
    if sender is not None:
        gid = getattr(sender, "group_id", None)
        if gid is None:
            extra = getattr(sender, "model_extra", None) or {}
            gid = extra.get("group_id")
        if gid is not None:
            text = str(gid).strip()
            if text and text != "0":
                return text
    gid = getattr(event, "group_id", None)
    if gid is not None:
        text = str(gid).strip()
        if text and text != "0":
            return text
    return None


@event_preprocessor
async def _filter_allowed_messages(bot: Bot, event: Event) -> None:
    if event.get_type() != "message":
        return

    owner_ids = get_owner_ids()
    group_ids = get_group_ids()
    message_type = getattr(event, "message_type", "")
    if message_type == "private":
        user_id = event.get_user_id()
        # owner 任何形态私聊都放行（含好友、群临时会话）
        if user_id in owner_ids:
            return

        sub_type = str(getattr(event, "sub_type", "")).strip()
        if sub_type == "group":
            # 群临时会话：源群必须在 GROUP_ID 白名单
            source_group_id = _extract_temp_source_group_id(event)
            if source_group_id is not None and source_group_id in group_ids:
                logger.info(
                    f"消息放行：type=private sub_type=group user_id={user_id} "
                    f"source_group_id={source_group_id}"
                )
                return
            logger.info(
                f"消息被过滤：type=private sub_type=group user_id={user_id} "
                f"source_group_id={source_group_id}"
            )
            raise IgnoredException("group temp message blocked by group_id allowlist")

        # 好友私聊或其它子类型：仍仅 owner 可用
        logger.info(
            f"消息被过滤：type=private sub_type={sub_type or 'friend'} "
            f"user_id={user_id}"
        )
        raise IgnoredException("private message blocked by owner_id allowlist")

    if message_type == "group":
        group_id = str(getattr(event, "group_id", "")).strip()
        user_id = event.get_user_id()
        if group_id in group_ids:
            return
        logger.info(
            f"消息被过滤：type=group group_id={group_id} user_id={user_id}"
        )
        raise IgnoredException("group message blocked by group_id allowlist")

    # MH-1 (U-1.2): console bypass 增加 adapter guard，防止第三方 adapter 推出
    # user_id="user" 绕过 owner / group allowlist。
    adapter_name = ""
    try:
        adapter_name = bot.adapter.get_name()
    except Exception:  # noqa: BLE001
        pass
    if adapter_name == "Console" and event.get_user_id() == "user":
        return

    user_id = event.get_user_id()
    group_id = str(getattr(event, "group_id", "")).strip()
    logger.info(
        f"消息被过滤：type={message_type or 'unknown'} group_id={group_id} user_id={user_id}"
    )
    raise IgnoredException("message blocked by access allowlist")


@driver.on_startup
async def _init_database() -> None:
    # 单一入口：init_db() 内部 create_all + 全部 ensure_* 都是 IF NOT EXISTS
    # 幂等，新建库 / 旧库升级走同一路径，避免漏调 ensure_* 导致缺列 / 缺索引。
    if not DB_PATH.exists():
        logger.info("app.db 不存在，开始初始化数据库")
    else:
        logger.info("检测到 app.db，检查表结构")
    init_db()
    logger.info("数据库初始化 / 表结构检查完成")

    sync_registered_commands_to_db()
    logger.info("命令配置同步完成")
    from nextbot.command_config import register_alias_matchers
    register_alias_matchers()
    start_web_server()


# NoneBot Lifespan 以 LIFO 顺序执行 shutdown 钩子
# （_lifespan.py:80-81 `reversed(_shutdown_funcs)`），先注册的后执行。
# 为了让 HTTP 客户端先关再做 WAL checkpoint（避免 in-flight 请求触发的 DB 写
# 在 checkpoint 之后产生新 WAL 帧），把 _wal_checkpoint 注册在前、
# _close_shared_http_client 注册在后，运行时 HTTP → WAL 顺序生效。
@driver.on_shutdown
async def _wal_checkpoint() -> None:
    # R8 M-3：进程正常退出时 truncate WAL，防止 app.db-wal 长跑累积
    from nextbot.db import wal_checkpoint_truncate
    wal_checkpoint_truncate()


@driver.on_shutdown
async def _close_shared_http_client() -> None:
    # I-1.3：释放 tshock_api 模块级 httpx.AsyncClient 连接池
    from nextbot.tshock_api import close_shared_client
    await close_shared_client()


nonebot.load_plugins("nextbot/plugins")

nonebot.run()
