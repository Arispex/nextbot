# 删除 我的背包/用户背包 的 send_link 参数

## Goal

删除 `我的背包` 与 `用户背包` 的 `send_link`（"发送链接"）参数及其相关功能 —— 截图前不再额外发送背包页面链接。

## 现状（`nextbot/plugins/player_query.py`）

| 命令 | param 定义 | public URL 计算 | 发送 if 块 |
|---|---|---|---|
| `用户背包` (`handle_user_inventory`) | L387-393 | L513 | L518-519 |
| `我的背包` (`handle_my_inventory`) | L555-561 | L662 | L667-668 |

`public_page_url = _to_public_render_url(page_url)` 仅在 if 块里被用（发链接消息），删 if 后变 dead code，应一并删除。

## 实现

### `nextbot/plugins/player_query.py` 每个 handler 3 处删除

**1. 用户背包** (`handle_user_inventory`)：
- 删 L387-393 param 定义（包括尾部逗号 / 缩进对齐）
- 删 L513 `public_page_url = _to_public_render_url(page_url)`
- 删 L518-519 if 块（2 行）

**2. 我的背包** (`handle_my_inventory`)：
- 删 L555-561 param 定义
- 删 L662 `public_page_url = ...`
- 删 L667-668 if 块（2 行）

### 不动

- `_to_public_render_url` helper 函数本身（可能 / 未来有其他用途；本任务仅删调用方）
- screenshot 截图流程 / render_and_send_screenshot 调用 / 业务逻辑

### CommandConfig DB 兼容

- 现有 DB 里如果有用户设置了 `send_link=true`，下次 `sync_registered_commands_to_db` 会自动 drop 这个不再在 schema 里的字段（已有的行为）
- 无须 migration

## Out of Scope

- 不改其他参数（`show_stats` / `show_index` 等）
- 不改截图流程
- 不删 `_to_public_render_url` 函数定义（其他调用方可能存在或未来需要）
- 不动 `category="查询系统"` 等元信息

## Acceptance Criteria

- [ ] `grep -n "send_link\|发送链接\|背包链接" nextbot/plugins/player_query.py` → 0 matches
- [ ] `grep -n "public_page_url" nextbot/plugins/player_query.py` → 0 matches（或仅在其他不相关 handler 残留，需 implement agent 判断）
- [ ] `grep -n "_to_public_render_url" nextbot/plugins/player_query.py` → 仍有 helper 定义；可能 0 调用方
- [ ] 两个 command decorator 的 param 字典里没有 send_link
- [ ] `python3 -m py_compile nextbot/plugins/player_query.py` 通过
- [ ] handler 业务逻辑（截图、错误处理、log）未受影响
- [ ] `git diff --name-only` 仅 `nextbot/plugins/player_query.py`
