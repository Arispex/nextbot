# refactor: 删除 group_manager 命令插件（WebUI 已完整覆盖）

## 背景

`nextbot/plugins/group_manager.py` 共 7 条 QQ 命令：身份组列表 / 添加身份组 / 删除身份组 / 继承身份组 / 取消继承身份组 / 添加身份组权限 / 删除身份组权限。

`/webui/groups`（路由 `server/routes/webui_groups.py` + JS `groups.js` + template `groups_content.html`）已完整覆盖这 7 个动作（列表 / CRUD / inherits 字段 / permissions 字段编辑）。用户决定下线 QQ 命令端，**保留并不破坏 WebUI 功能**。

## 改动

### 1) 删除插件文件

`nextbot/plugins/group_manager.py` — 整文件删除。

NoneBot auto-discovery 扫描 `nextbot/plugins/` 目录，文件移除后 7 条命令自动从 bot 注销，无需改注册表（项目无 `__init__.py` 显式注册）。

### 2) 顺手清理 doc 注释残留（避免后续读者看到死链）

4 处 prior-art 注释引用 `group_manager.py`：

- `nextbot/permissions.py:41` — 注释 "2 个 caller（permission_manager / group_manager）..." → 改为 "permission_manager"（删除 / group_manager）
- `nextbot/plugins/permission_manager.py:91` — 注释 "与 group_manager 保持一致" → 改为 "（沿用既有重试上限）"
- `server/webui/static/js/groups.js:563` — 注释 "与 bot 端 group_manager.py:302 对齐" → 改为 "提示用户删除会把 N 个成员回退到 default 组"
- `server/routes/webui_groups.py:523` — 注释 "与 bot 端 group_manager.py:302 行为对齐" → 改为 "删除组前先采样实际受影响用户数 → 写日志（便于事后审计回滚估算）"

注释文案改动**不影响行为**，只去掉指向已删文件的死链。

## 不动

- `nextbot/db.py` 的 `Group` / `GroupPermission` ORM 模型 + `GROUP_DELETE_FALLBACK` / `RESERVED_GROUP_NAMES`（如果存在）— WebUI 依赖
- `server/routes/webui_groups.py`、`server/webui/static/js/groups.js`、`server/webui/templates/groups_content.html` — WebUI 业务逻辑不动
- `nextbot/permissions.py` 业务逻辑、`nextbot/plugins/permission_manager.py` 的命令注册不动（**仅改其中 1 行注释**）
- 其它命令插件（user_manager / server_manager / ban / economy / ...）— 不在本任务

## Scope

5 个文件触及：
- `nextbot/plugins/group_manager.py`（删除）
- `nextbot/permissions.py`（注释）
- `nextbot/plugins/permission_manager.py`（注释）
- `server/webui/static/js/groups.js`（注释）
- `server/routes/webui_groups.py`（注释）

## Acceptance

- bot 重启后，QQ 群发 `身份组列表` / `添加身份组` 等 7 条命令不再响应（NoneBot 不再注册它们）
- `/webui/groups` 页面所有动作（列表 / 新建 / 编辑 / 删除 / 继承 / 权限）正常
- `python3 -m py_compile` 对 4 个保留文件全部通过
- grep `group_manager` 在仓库根（排除 `.trellis/` / 归档 / pycache）应仅剩 git history

## DO NOT

- 不动 ORM 模型 / DB schema
- 不重写 WebUI 端的 group 逻辑
- 不动 permission_manager 业务功能
- 不 commit

## Out of Scope

- user_manager / server_manager / ban / economy 等其它"WebUI 已覆盖"的命令插件下线 — 单独任务
- WebUI groups 文案 / a11y 改进
