import nonebot
from pathlib import Path
from nonebot.adapters.console import Adapter as ConsoleAdapter
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.adapters import Event
from nonebot.exception import IgnoredException
from nonebot.log import logger
from nonebot.message import event_preprocessor

from next_bot.command_config import sync_registered_commands_to_db
from next_bot.signin_reset import start_signin_reset_worker
from server.web_server import start_web_server
from next_bot.access_control import get_group_ids, get_owner_ids
from next_bot.db import (
    DB_PATH,
    Base,
    ensure_command_config_schema,
    ensure_default_groups,
    ensure_default_stats,
    ensure_user_signin_schema,
    get_engine,
    init_db,
)

ENV_PATH = Path(__file__).resolve().parent / ".env"
DEFAULT_ENV_CONTENT = (
    "DRIVER=~websockets\n"
    "LOCALSTORE_USE_CWD=true\n"
    "\n"
    "COMMAND_START=[\"/\", \"\"]\n"
    "\n"
    "ONEBOT_WS_URLS=[\"ws://127.0.0.1:3001\"]\n"
    "ONEBOT_ACCESS_TOKEN=MyOneBotAccessToken\n"
    "\n"
    "OWNER_ID=[\"\"]\n"
    "GROUP_ID=[\"\"]\n"
    "\n"
    "WEB_SERVER_HOST=0.0.0.0\n"
    "WEB_SERVER_PORT=18081\n"
    "WEB_SERVER_PUBLIC_BASE_URL=http://127.0.0.1:18081\n"
    "COMMAND_DISABLED_MODE=reply\n"
    "COMMAND_DISABLED_MESSAGE=该命令暂时关闭~\n"
)


def ensure_env_file() -> None:
    if ENV_PATH.exists():
        return

    ENV_PATH.write_text(DEFAULT_ENV_CONTENT, encoding="utf-8")
    logger.warning(".env 不存在，已创建默认 .env 文件：%s", ENV_PATH)


ensure_env_file()
nonebot.init()

driver = nonebot.get_driver()
# driver.register_adapter(ConsoleAdapter)
driver.register_adapter(OneBotV11Adapter)


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
        ensure_default_groups()
        ensure_default_stats()
        logger.info("表结构检查完成")

    sync_registered_commands_to_db()
    logger.info("命令配置同步完成")
    start_signin_reset_worker()
    start_web_server()

nonebot.load_plugins("next_bot/plugins")

nonebot.run()
