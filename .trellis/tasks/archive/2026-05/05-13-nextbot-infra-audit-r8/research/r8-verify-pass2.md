# Round 8 主代理二次审核日志

## Round 7 复审：26/26 PASS

| 桶 | Round 7 修复数 | PASS | 备注 |
|---|---|---|---|
| DB | 8 (H-1, H-2, D-1.2~7) | 8 | 0 regression |
| Permission | 4 (H-3, P-1.6, P-1.9, P-1.13) | 4 | 0 regression |
| IO | 7 (H-4, I-1.1, I-1.3, I-1.4, I-1.5, I-2.1, I-3.1) | 7 | 0 regression |
| Utils | 7 (MH-1, MH-2, U-1.1, U-2.3, U-2.4, U-2.12, I-1.3 hook) | 7 | 0 regression（MH-2 留下 1 个 edge case → R8-U-B-1） |
| **总计** | **26** | **26** | **Round 7 全部生效，无回归** |

## R8 新发现验证（主代理逐条 Read 行号验证）

### 终判 Medium 级别（5 项）

| ID | 子代理判 | 主代理终判 | 验证 |
|---|---|---|---|
| R8-D-6 | Medium | **Medium** | ✅ CONFIRMED `db.py:473-480`，与 50+ `rowcount == 0` 业务降级 caller 冲突 |
| R8-D-1 | Medium | **Medium** | ✅ CONFIRMED `db.py:434-454`，17 个 ensure 独立事务，中段失败半 migrate |
| R8-D-3 | Medium | **Medium** | ✅ CONFIRMED `db.py:395-406`，WAL 启用但无 checkpoint 策略 |
| R8-D-7 | Medium | **Low**（下调） | ✅ CONFIRMED `db.py:375-426`，但 `init_db` 单线程顺序调用，`get_session` 实际无并发触发面（plugin 都在 `on_startup` 之后） → 下调 Low |
| R8-U-B-1 | Medium | **Medium** | ✅ CONFIRMED `command_config.py:809-833`，line 809 `command_key != normalized_key` 排除 self 后 conflict_names 不含自己的 command_key |

### 下调说明 / False positive

- **R8-D-2 `_user_columns_ensured` flag 语义模糊**：子代理推演后已自己澄清"实际 safe"（SQLite ALTER 是 transactional + `engine.begin()` rollback 完整），仅语义模糊。**不视为 finding，归 Info**。
- **R8-D-7 engine 并发 init**：触发面是测试 / scripts 场景，生产 `on_startup` 单线程不触发。**下调 Low**。
- **R8-IO-A-3.1 DCL fast-path threadpool 风险**：grep 确认无 sync endpoint 调 `request_server_api`。**不视为 finding**。
- **R8-IO-A-3.2 shutdown vs in-flight**：nonebot 取消 task 在前，理论不触发。**不视为 finding**。
- **R8-IO-A-1.1 / B-3 bytes(chunks) 内存峰值**：Round 7 修复后引入，但触发面要 240MB 接近 cap 才有意义；优化空间存在但非 bug。**Low 但合并到 B-3**。
- **R8-P-1.x（permission 桶 7 条 Low/Info）**：全部 forward-compat 防御加固，无真实触发路径。**Low/Info 保留但不强烈推荐修**。

### 终判 Low 级别（按价值优先级）

| ID | 文件 | 一句话 |
|---|---|---|
| R8-U-B-2 | `command_config.py:445-477` | DB 故障时每条消息触发一次完整 stack trace 日志，无 throttle → 日志风暴 |
| R8-D-4 | `db.py:506-510, 531-535` | 显式 rollback 自身抛错时吞掉原 commit 异常（异常链路歧义） |
| R8-D-5 | `db.py:408-423` | `_set_sqlite_pragma` 未加 dialect 守卫，未来切 PostgreSQL 立即报错 |
| R8-D-7 | `db.py:375-426` | engine init 无 threading.Lock（下调自 Medium） |
| R8-D-8 | `db.py:457-459` | `get_session()` 返回 untyped Session，调用方可绕开 BEGIN IMMEDIATE 契约 |
| R8-IO-B-1 | `webui_servers.py:206` + `server_manager.py:195` | `release_server_semaphores` 接线缺失（Round 7 承诺保留） |
| R8-IO-B-2 | `tshock_api.py:18` + `large_image.py:17` | `MAX_RESPONSE_BYTES >= MAX_BASE64_BYTES * 5/4` invariant 无 assert |
| R8-IO-B-3 + A-1.1 | `tshock_api.py:161, 178` | `json.loads(bytearray)` 可省 `bytes(chunks)` 一份内存复制 |
| R8-P-1.14 | `permissions.py:299-320` | `add_permission` 缺逗号 sanitization（owner 自伤型 foot-gun） |
| R8-P-1.15 | `audit.py:30-43` | `_coerce_snapshot` 不递归检查 dict / list 内 nested ORM |
| R8-P-1.16 | `permissions.py:34-49` | `_get_effective_permissions_in_session` 缺 session 非 None 守卫 |

### Info（不修，仅记录）

| ID | 一句话 |
|---|---|
| R8-P-1.17~1.20 | already_banned race / `_parse_id_list` nested / `is_dangerous_permission("")` 边界 / `event.get_user_id()` type guard |
| R8-IO-B-4~B-6 | broadcast 默认值 OK / server_validation 干净 / screenshot_render 干净 |
| R8-U-B-3 | `resolve_user_id_arg_with_fallback` 在 BEGIN IMMEDIATE 下持写锁（设计性，归 DB 层讨论） |
| R8-U-B-4 | `get_dashboard_metrics` fail-hard（已被 caller 隔离，非新 outage） |
| R8-U-B-5/6 | time_utils / progression / data_dir / text_utils 再扫干净 |

## 主代理终判

**Round 7 修复 100% 闭环，零回归。** 新一轮全量再扫发现：

- **0 Critical**
- **0 High**
- **5 Medium**（4 个 DB + 1 个 Utils）
- **~11 Low**（多为防御加固 / 工程纪律）
- **~10 Info**

Round 7 修复后基础设施层的攻击面 / 资源管理 / 业务正确性已稳定。剩余 Medium 都属于 **Round 7 修复周边暴露的工程加固面** 或 **Round 7 修复尚未覆盖到的次要触发路径**：

- **R8-D-6** Round 7 加日志不够，应改 fast-fail（防业务降级误触发）
- **R8-D-1** Round 7 改造 ensure_*_schema 后，全局 init_db 仍缺中段失败兜底
- **R8-D-3** Round 7 启用 WAL 引入的运维新面，需要 checkpoint 策略
- **R8-U-B-1** Round 7 MH-2 修了"alias 撞他人 command_key"，但漏了"alias 撞自己 command_key"

每条都是 **≤ 10 行代码 + 触发概率明确** 的真问题。
