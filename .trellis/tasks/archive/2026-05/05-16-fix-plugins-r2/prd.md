# fix(plugins): 服务器列表移到查询系统 + 玩家查询分类改名为查询系统

## 改动

### 1) `nextbot/plugins/server_manager.py:21`

`category="服务器管理"` → `category="查询系统"`（服务器列表是该文件唯一保留命令；前序 commit a9f50fe 已下线 admin 命令，分类只剩这一条）

### 2) `nextbot/plugins/player_query.py`

7 处 `category="玩家查询"` → `category="查询系统"`（line 190 / 395 / 563 / 689 / 818 / 970 / 1093）

## Scope

仅 2 文件。

## Acceptance

- `服务器列表` 命令在 WebUI 命令配置 / 菜单 中归属"查询系统"
- 原"玩家查询"分类下 7 条命令全部归属"查询系统"
- 现网已有"服务器管理"/"玩家查询"分类的 DB 配置行不破坏（**category 字段不是 PK**，DB 会按新值显示）
- `python3 -m py_compile` 两文件通过

## DO NOT

- 不动其它 plugin / 分类
- 不动命令名 / param key / display_name
- 不 commit
