# refactor: server_manager 仅保留「服务器列表」，删除 admin 命令

## 决议（方案 B）

`nextbot/plugins/server_manager.py` 4 条命令：
- `添加服务器` / `删除服务器` / `测试连通性` — admin 命令，已被 `/webui/servers` 完整覆盖 → **删除**
- `服务器列表` — 玩家也用（查 IP / 端口），新手教程 `tutorial_data.py:26` 依赖 → **保留**

## 改动

`nextbot/plugins/server_manager.py`：

### 删除
- 3 个 matcher 声明：`add_matcher` (line 32) / `delete_matcher` (line 33) / `test_matcher` (line 35)
- 3 个 handler 函数（共 ~250 行）：
  - `handle_add_server` line 37-152
  - `handle_delete_server` line 155-235
  - `handle_test_server` line 277-329

### 保留
- `list_matcher` 声明（原 line 34）
- `handle_list_servers` 函数（原 line 238-274）

### 清理 import
保留：
```python
from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.log import logger
from nonebot.params import CommandArg
from nextbot.command_config import command_control, raise_command_usage
from nextbot.db import Server, get_session
from nextbot.message_parser import parse_command_args_with_fallback
from nextbot.permissions import require_permission
```

删除（仅 admin handler 用）：
- `from sqlalchemy import func`（add 用 max id）
- `from sqlalchemy.exc import IntegrityError`（add）
- `from nextbot.audit import audit_permission_change`（add/delete）
- `from nextbot.large_image import release_server_semaphores_all`（delete）
- `from nextbot.server_validation import ServerPayloadValidationError, validate_server_payload`（add）
- `from nextbot.tshock_api import TShockRequestError, get_error_reason, is_success, request_server_api`（test）
- `from nextbot.text_utils import EMOJI_SERVER, at_prefix, reply_block, reply_failure, reply_success`（add/delete/test — list 直接 `bot.send(event, message)` 不用这些 helper）

## Scope

仅 `nextbot/plugins/server_manager.py`。

## Acceptance

- bot 重启后 QQ 群发 `添加服务器` / `删除服务器` / `测试连通性` 不响应
- `服务器列表` 仍正常输出 IP / 端口
- 新手教程第 2 步不变
- `/webui/servers` 所有功能不受影响（WebUI 独立后端）
- `python3 -m py_compile nextbot/plugins/server_manager.py` 通过
- `tutorial_data.py` 不动

## DO NOT

- 不动 `nextbot/server_validation.py` / `tshock_api.py` / `audit.py` / `large_image.py` 等被删 handler 引用过但其他插件仍在用的共享模块
- 不动 `/webui/servers` 后端 / 前端
- 不动 `tutorial_data.py`
- 不动 `command_config` 的 server.list 注册（保留）
- 不 commit

## Out of Scope

- 删 user_manager / permission_manager / ban / economy 等其它 admin-only 命令 — 单独任务
- 把 server.list 权限改为 guest 默认（如果还想"所有玩家都能查 IP"是单独议题）
