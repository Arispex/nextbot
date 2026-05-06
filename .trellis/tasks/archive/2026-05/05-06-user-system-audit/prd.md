# 用户系统命令安全与性能审计

## Goal

审计 `nextbot/plugins/user_manager.py` 中的 5 个 `category="用户系统"` 命令，找出安全漏洞、缺陷、性能瓶颈，按严重级别整理报告（不直接修复）。

## 审计范围

| 命令 | display_name | command_key | 行号 |
|---|---|---|---|
| 1 | 注册账号 | `user.register` | 125–183 |
| 2 | 同步白名单 | `user.whitelist.sync` | 184–286 |
| 3 | 用户信息 | `user.info.user` | 287–333 |
| 4 | 我的信息 | `user.info.self` | 334–364 |
| 5 | 更改用户名称 | `admin.rename` | 365–490 |

## 审计维度

### 安全
- SQL 注入 / 命令注入（`request_server_api` 调用、TShock RawCmd 拼接）
- 权限校验是否覆盖所有路径（`require_permission` + 内部分支）
- 用户输入校验（QQ / 用户名 / 长度 / 字符集）
- 越权风险（修改他人账号、跳过身份组检查）
- 信息泄露（错误消息透出敏感数据）
- 竞态条件（多人同时注册同名 / 同步白名单 / 重命名）

### 缺陷
- 错误处理路径是否完整（API 失败 / DB 异常 / 部分服务器不可达）
- session 是否正确 close（try/finally）
- 部分成功 / 部分失败的回滚或回复
- 边界条件（空字符串 / 极长字符串 / 重复输入）

### 性能
- N+1 查询
- 多服务器循环里的同步等待 vs `asyncio.gather`
- DB 索引利用
- 不必要的全表扫
- 内存中处理可下沉到 SQL 的过滤

## Acceptance Criteria

- [ ] 5 个命令逐一过一遍
- [ ] 每个发现按 严重级别（🔴 必修 / 🟠 应修 / 🟡 建议 / 🟢 观察）分类
- [ ] 每个问题包含：现象描述 / 影响 / 复现步骤 / 修复方案 / 严重级别
- [ ] 误报由主代理二次复查后剔除（trellis-research 写完后我读源码确认）
- [ ] 所有发现持久化到 `research/*.md`，主代理读取后向用户输出最终报告

## Non-goals

- 不修复任何代码（这是审计任务）
- 不审计其他 category（小游戏 / 排行榜 / 玩家查询 等）
- 不做依赖库 CVE 扫描

## Definition of Done

- 输出最终报告给用户，问题已经过主代理复核
