# `/nextbot/online-players` API 契约（原样保留字段名）

> 用户提供的新 API 文档。**字段名 / 结构 / 值一律原样使用，不翻译、不业务化改写**（CLAUDE.md 规则 5）。

## GET `/nextbot/online-players`

返回服务器**当前在线且已登录账号**的玩家列表。每个玩家附带角色外观 `appearance`、装备 `equipment`（head/body/legs）、装饰 `vanity`（head/body/legs）、护甲染料 `dye`（head/body/legs）、功能配饰 `accessories`（定长 7 数组）、社交配饰 `vanityAccessories`（定长 7 数组）、配饰染料 `accessoryDyes`（定长 7 数组）。数据全部来自 SSC 存档（`tsCharacter`）。

**权限：** `nextbot.online_players.view` — **服务端权限**，由 `request_server_api` 携带的 server token 处理。命令侧无需新增权限，命令权限沿用 `player_query.online`。

## 列表语义（关键边界）

- 仅纳入**已登录账号**（`Account != null`）的在线玩家；未登录账号的在线玩家被跳过。
- 在线但**尚无 SSC 存档行**（罕见，如刚连入）→ `appearance`/`equipment`/`vanity`/`dye`/`accessories`/`vanityAccessories`/`accessoryDyes` 均为 `null`，该玩家**仍列入**（`name` 照常返回）。
  - → 本任务图片模式：`appearance == null` 的玩家**跳过不渲染**（与 `_build_character_sprite_uri` 对 `appearance` 为 null 的处理一致）。
- `equipment` 对应 SSC inventory 槽位 head=59、body=60、legs=61；`vanity` head=69/body=70/legs=71；`dye`（护甲染料）head=79/body=80/legs=81。
- `accessories`（功能配饰，激活/第一套 loadout）定长 7，下标 `i` → 槽位 `62+i`（62–68）。
- `vanityAccessories`（社交/时装配饰）定长 7，下标 `i` → 槽位 `72+i`（72–78）。
- `accessoryDyes`（配饰染料）定长 7，下标 `i` → 槽位 `82+i`（82–88）。
- 空装备槽返回 `{ "netId": 0, "stack": 0, "prefixId": 0 }`，形状稳定。
- `sessionOnlineSeconds`：该玩家**本次会话自登录起的在线时长（秒）**，独立于 SSC 存档。无活动会话（如插件加载前就在线）时为 `null`。与 `/stats` 的累计 `onlineSeconds` 语义不同。

## 颜色字段说明

7 个颜色字段（`hairColor`/`skinColor`/`eyeColor`/`shirtColor`/`underShirtColor`/`pantsColor`/`shoeColor`）为 FNA `Color.PackedValue` 的**有符号 packed int**，**原样返回**（可为负，如 `-3270602`），字节序 `0xAABBGGRR`（R 最低字节）。服务端不解码。解码由 `render_character` 侧处理（已兼容，与 `/users/{user}/appearance` 同形状）。

## 响应 200 形状

```json
{
  "players": [
    {
      "name": "PlayerName",
      "appearance": {
        "skinVariant": 7, "hair": 112, "hairDye": 0,
        "hairColor": -3270602, "skinColor": -10059269, "eyeColor": -15100654,
        "shirtColor": -4021652, "underShirtColor": -4639811,
        "pantsColor": -12772014, "shoeColor": -4963208
      },
      "equipment": {
        "head": { "netId": 0, "stack": 0, "prefixId": 0 },
        "body": { "netId": 0, "stack": 0, "prefixId": 0 },
        "legs": { "netId": 0, "stack": 0, "prefixId": 0 }
      },
      "vanity": { "head": {...}, "body": {...}, "legs": {...} },
      "dye":    { "head": {...}, "body": {...}, "legs": {...} },
      "accessories":       [ {netId,stack,prefixId} ×7 ],
      "vanityAccessories": [ {netId,stack,prefixId} ×7 ],
      "accessoryDyes":     [ {netId,stack,prefixId} ×7 ],
      "sessionOnlineSeconds": 1234
    }
  ]
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `players` | array | 在线已登录玩家列表，无人在线时为空数组 `[]` |
| `players[].name` | string | 玩家账号名（`Account.Name`） |
| `players[].appearance` | object \| null | 角色外观；无 SSC 存档时为 `null` |
| `players[].appearance.skinVariant` | integer | 肤色变体 |
| `players[].appearance.hair` | integer | 发型 |
| `players[].appearance.hairDye` | integer | 染发剂 |
| `players[].appearance.{hairColor,skinColor,eyeColor,shirtColor,underShirtColor,pantsColor,shoeColor}` | integer | 有符号 packed int（`0xAABBGGRR`），原样返回，可为负 |
| `players[].equipment` | object \| null | 功能装备 head/body/legs（槽位 59/60/61）；无 SSC 时 `null` |
| `players[].vanity` | object \| null | 装饰 head/body/legs（槽位 69/70/71）；无 SSC 时 `null` |
| `players[].dye` | object \| null | 护甲染料 head/body/legs（槽位 79/80/81）；无 SSC 时 `null` |
| `players[].accessories` | array \| null | 功能配饰，定长 7，下标 `i`→槽位 `62+i`；无 SSC 时 `null` |
| `players[].vanityAccessories` | array \| null | 社交配饰，定长 7，下标 `i`→槽位 `72+i`；无 SSC 时 `null` |
| `players[].accessoryDyes` | array \| null | 配饰染料，定长 7，下标 `i`→槽位 `82+i`；无 SSC 时 `null` |
| `players[].sessionOnlineSeconds` | integer \| null | 本次会话在线时长（秒）；无活动会话时 `null` |

## 渲染映射（→ `render_character` / `_build_character_sprite_uri`）

`render_character(appearance, equipment, vanity, dye, *, accessories, vanity_accessories, accessory_dyes)` 已兼容此形状（`/users/{user}/appearance` 同形）。
注意字段名映射：API `vanityAccessories` → kwarg `vanity_accessories`；API `accessoryDyes` → kwarg `accessory_dyes`。

`sessionOnlineSeconds` → `nextbot.time_utils.format_online_seconds(int)`（`None` 时显示占位，如「—」）。
