# 更新背包 REST API 接口

## Goal

将"用户背包"和"我的背包"命令从旧的 TShock 原生接口迁移到新的 NextBotAdapter 接口。

## 旧接口 vs 新接口

### 背包数据

| | 旧 | 新 |
|---|---|---|
| 路径 | `/v2/users/inventory?user=<name>` | `/nextbot/users/{user}/inventory` |
| 响应结构 | `{ "response": [ { "netID": 4, "prefix": 82, "stack": 1 } ] }` | `{ "items": [ { "slot": 0, "netId": 4, "stack": 1, "prefixId": 82 } ] }` |
| 索引方式 | list 顺序即 slot 索引 | 每个 item 含 `slot` 字段 |

### 角色属性

| | 旧 | 新 |
|---|---|---|
| 路径 | `/v2/users/info?user=<name>` | `/nextbot/users/{user}/stats` |
| 响应结构 | `{ "response": { "当前生命值": 400, "最大生命值": 500, ... } }` | `{ "health": 400, "maxHealth": 500, "mana": 100, "maxMana": 200, "questsCompleted": 7, "deathsPve": 12, "deathsPvp": 3 }` |

### 错误响应

新接口：HTTP 状态码为 400/500，body 为 `{ "error": "..." }`，无 `status` 字段。

## Requirements

- `basic.py` 中 `handle_user_inventory` 和 `handle_my_inventory` 使用新路径
- 解析新背包响应：从 `payload["items"]` 取 list，字段改为 `netId`、`prefixId`、`stack`、`slot`
- 解析新属性响应：从 payload 顶层取 `health`、`maxHealth`、`mana`、`maxMana`、`questsCompleted`、`deathsPve`、`deathsPvp`
- `_parse_user_info_texts` 改为解析新 stats 响应格式
- `inventory_page._normalize_slots` 改为按 `slot` 字段索引，字段名改为 `netId`、`prefixId`
- 错误处理：新接口 error 在 `payload["error"]` 中，失败时展示该原因

## Acceptance Criteria

- [ ] 用户背包命令调用 `/nextbot/users/{user}/inventory`
- [ ] 我的背包命令调用 `/nextbot/users/{user}/inventory`
- [ ] 属性数据调用 `/nextbot/users/{user}/stats`
- [ ] `_normalize_slots` 按 slot 字段映射，支持稀疏 items
- [ ] `_parse_user_info_texts` 使用新英文字段名
- [ ] API 返回 error 时展示 `payload["error"]` 内容

## Files to Modify

- `nextbot/plugins/basic.py`
- `server/pages/inventory_page.py`
