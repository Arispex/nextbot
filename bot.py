import json
import nonebot
from nonebot.adapters.console import Adapter as ConsoleAdapter
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.adapters import Event
from nonebot.exception import IgnoredException
from nonebot.log import logger
from nonebot.message import event_preprocessor

from nextbot.command_config import sync_registered_commands_to_db
from nextbot.data_dir import DATA_DIR
from server.web_server import start_web_server
from nextbot.access_control import get_group_ids, get_owner_ids
from nextbot.db import (
    DB_PATH,
    Base,
    ensure_command_config_schema,
    ensure_default_groups,
    ensure_default_stats,
    ensure_red_packet_schema,
    ensure_warehouse_schema,
    ensure_sign_record_schema,
    ensure_sign_record_unique_schema,
    ensure_user_ban_schema,
    ensure_user_dice_schema,
    ensure_user_guess_schema,
    ensure_user_rob_schema,
    ensure_user_signin_schema,
    get_engine,
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

    ENV_PATH.write_text(DEFAULT_ENV_CONTENT, encoding="utf-8")
    logger.warning(".env 不存在，已创建默认 .env 文件：%s", ENV_PATH)


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


@event_preprocessor
async def _filter_allowed_messages(event: Event) -> None:
    if event.get_type() != "message":
        return

    owner_ids = get_owner_ids()
    group_ids = get_group_ids()
    message_type = getattr(event, "message_type", "")
    if message_type == "private":
        user_id = event.get_user_id()
        if user_id in owner_ids:
            return
        logger.info(f"消息被过滤：type=private user_id={user_id}")
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

    if event.get_user_id() == "user":
        return

    user_id = event.get_user_id()
    group_id = str(getattr(event, "group_id", "")).strip()
    logger.info(
        f"消息被过滤：type={message_type or 'unknown'} group_id={group_id} user_id={user_id}"
    )
    raise IgnoredException("message blocked by access allowlist")


@driver.on_startup
async def _init_database() -> None:
    if not DB_PATH.exists():
        logger.info("app.db 不存在，开始初始化数据库")
        init_db()
        logger.info("数据库初始化完成")
    else:
        logger.info("检测到 app.db，检查表结构")
        Base.metadata.create_all(get_engine())
        ensure_command_config_schema()
        ensure_user_signin_schema()
        ensure_sign_record_schema()
        ensure_sign_record_unique_schema()
        ensure_user_ban_schema()
        ensure_user_rob_schema()
        ensure_user_guess_schema()
        ensure_user_dice_schema()
        ensure_red_packet_schema()
        ensure_warehouse_schema()
        ensure_default_groups()
        ensure_default_stats()
        logger.info("表结构检查完成")

    sync_registered_commands_to_db()
    logger.info("命令配置同步完成")
    from nextbot.command_config import register_alias_matchers
    register_alias_matchers()
    start_web_server()

nonebot.load_plugins("nextbot/plugins")

nonebot.run()
