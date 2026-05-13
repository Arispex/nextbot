# R9 DB 桶审计

- **Scope**：`nextbot/db.py`（961 行）/ `nextbot/warehouse_lock.py`（17 行）/ `nextbot/screenshot_temp.py`（39 行）
- **Round 7 commit**：`66b4d6c`（Round 7 已在 Round 8 全部 PASS）
- **Round 8 修复点**：M-1 / M-2 / M-3 + R8-D-4 / R8-D-5
- **Date**：2026-05-13

---

## Part A: Round 8 修复复审

### M-1 [PASS-with-Note] execute_rowcount fast-fail（R8-D-6 升级）
- **文件**：`db.py:490-507`
- **修复前行为**（Round 7）：
  ```python
  rowcount = getattr(result, "rowcount", None)
  if rowcount is None:
      logger.warning("...")
      return 0   # ← silent fallback，ban_core 等 50+ caller 误走 already_banned 业务降级
  ```
- **修复后行为**（Round 8）：
  ```python
  result = session.execute(stmt)
  rowcount = getattr(result, "rowcount", None)
  if rowcount is None:
      raise TypeError(  # noqa: TRY003
          f"execute_rowcount 仅支持 INSERT/UPDATE/DELETE，"
          f"收到 stmt={type(stmt).__name__}（可能误传 SELECT）"
      )
  return int(rowcount)
  ```
- **全仓 caller 核查**：grep 出 14 个文件 × 41 处 `execute_rowcount(` 调用，逐处确认 stmt 类型：
  - `ban_core.py:65, 111` —— 都是 `update(User).where(...).values(...)`
  - `economy.py:92, 114, 158, 176, 331, 360, 522, 545, 768` —— 全部 `update(User).where(...).values(...)`
  - `red_packet.py:93, 189, 366, 486` —— `sa_update(RedPacket|User)`，line 93 是 `sa_update(RedPacket)`
  - `guess_number.py:189` / `dice.py:159` / `rob.py:282, 309, 362, 404, 419, 441, 456` / `rob_protection.py:90`
    / `lottery.py:717, 735` / `user_manager.py:513` / `permission_manager.py:270, 411, 567, 820, 985`
    / `group_manager.py:343, 373, 514, 603, 720, 825` / `shop.py:626, 809` —— grep 确认全是 `update(...)` 或 `sa_update(...)`
  - **无任何 SELECT 误传**。M-1 在现有调用面上**不会触发新 TypeError**。
- **新暴露面**（已在 Round 8 R8-D-6 推荐选项 A 中显式接受）：
  - 没有 caller 用 `try/except TypeError` 兜底。未来若新 plugin 误写 `execute_rowcount(session, select(User))` →
    TypeError 直接冒到 plugin matcher → NoneBot framework 打印 traceback → 用户在群里看到原始 traceback
    （而不是友好的 `操作失败，<原始错误>` 文案）。
  - 这是 fail-fast 设计的**本意**：开发者期 bug 不靠 silent 0 掩盖。生产期发现也比 silent 业务降级更易定位。
  - Note：`# noqa: TRY003` 说明开发者已主动放行 ruff TRY003（"long messages outside exception class"），
    错误信息里携带 `type(stmt).__name__` 是必要诊断信息，合理。
- **结论**：PASS。Round 7 R8-D-6 的 silent failure 已实质闭合。所有现存 caller 都传 mutation stmt，
  TypeError 路径不会在生产期触发。fail-fast 选择优于 silent 0。

### M-2 [PASS-with-Note] _run_migration helper 包裹 16 个 ensure_*_schema（R8-D-1）
- **文件**：`db.py:441-483`
- **修复前行为**（Round 7）：17 个 `ensure_*_schema` 顺序裸调，中段抛异常 → init_db 整体挂掉 →
  第 N+1 ~ 17 个未执行，留下半 migrate 状态。
- **修复后行为**（Round 8）：
  ```python
  def _run_migration(name: str, func: Callable[[], None]) -> None:
      try:
          func()
      except Exception as exc:  # noqa: BLE001
          logger.warning(
              f"迁移失败：name={name} reason={exc!r}（保留旧 schema 不阻断启动）"
          )

  def init_db() -> None:
      engine = get_engine()
      Base.metadata.create_all(engine)
      _run_migration("command_config", ensure_command_config_schema)
      _run_migration("user_signin", ensure_user_signin_schema)
      # ... 16 次 _run_migration
      ensure_default_groups()    # 不包：seeding 失败必阻断
      ensure_default_stats()     # 不包：seeding 失败必阻断
  ```
- **核对 16 处包装**（line 464-479）：command_config / user_signin / sign_record / sign_record_unique /
  user_sign_record_index / user_ban / user_rob / user_guess / user_dice / red_packet / warehouse /
  shop / lottery / user_name_unique / user_leaderboard_indexes / warehouse_fk。
  ✅ 全部 16 项命名清晰，函数顺序与 Round 7 一致。
- **核对 default 函数不走 helper**：`ensure_default_groups()` / `ensure_default_stats()` 走原生 raise 路径，
  docstring (line 450-451) 明确"`ensure_default_*` 是 seeding 不是 migration，失败必须阻断启动"。✅
- **新暴露面**：
  - **R9-D-1 [Info]**：`ensure_user_signin_schema` 内部已有 try/except + logger.warning（line 678-682），
    永远不会抛异常 → `_run_migration("user_signin", ensure_user_signin_schema)` 的外层 except 永远不会进
    → 不会出现"双重 warning"。✅ 这一层设计是**幂等无害的过保护**。
  - **R9-D-2 [Info]**：`ensure_sign_record_unique_schema` / `ensure_user_name_unique_schema` /
    `ensure_warehouse_fk_schema` / `ensure_user_leaderboard_indexes_schema` /
    `ensure_user_sign_record_index_schema` 同样**内部已 try/except**，外层 `_run_migration` 同样不会进。
    保留 `_run_migration` 包装是**防御性冗余**，未来若有人删掉内部 try/except 仍有外层兜底。✅
  - **R9-D-3 [Low]**：若某个 ensure 持续失败（例如数据损坏 / SQLite 版本不兼容），每次启动 log 一行 ERROR/WARN，
    **无自动告警 / 无次数熔断**。运维需要定期 grep 日志。文档未要求自动告警，**Round 8 边界明确，不算 bug**。
- **结论**：PASS。R8-D-1 半 migrate 状态问题闭合。外层防御性冗余是合理工程姿态。

### M-3 [PASS-with-Note] wal_checkpoint_truncate() + on_shutdown 接线（R8-D-3）
- **文件**：`db.py:942-961` + `bot.py:168-184`
- **修复前行为**（Round 7）：启用 `PRAGMA journal_mode = WAL` 后无显式 checkpoint 策略，长跑 daemon
  下 `app.db-wal` 可累积到 GB 级。
- **修复后行为**（Round 8）：
  ```python
  def wal_checkpoint_truncate() -> None:
      if not DB_PATH.exists():
          return
      try:
          engine = get_engine()
          with engine.begin() as conn:
              conn.execute(sa_text("PRAGMA wal_checkpoint(TRUNCATE)"))
      except Exception as exc:  # noqa: BLE001
          logger.warning(f"WAL checkpoint 失败：reason={exc!r}")
  ```
  接线（`bot.py:168-184`）：
  ```python
  # NoneBot Lifespan 以 LIFO 顺序执行 shutdown 钩子，先注册的后执行
  @driver.on_shutdown
  async def _wal_checkpoint() -> None:    # 先注册 → 后执行
      wal_checkpoint_truncate()
  @driver.on_shutdown
  async def _close_shared_http_client() -> None:  # 后注册 → 先执行
      await close_shared_client()
  ```
  执行顺序：close HTTP → WAL checkpoint。**避免 in-flight 请求触发的 DB 写在 checkpoint 之后产生新 WAL 帧**。
  bot.py:168-172 已明确写出此契约。✅
- **新暴露面**：
  - **R9-D-4 [Medium]** TRUNCATE 阻塞行为：`PRAGMA wal_checkpoint(TRUNCATE)` 在 SQLite 内部需要"无活跃 reader"
    才能完整推进；若 nonebot shutdown 时仍有 plugin 慢命令的 session 未关闭（持读锁），TRUNCATE 会**阻塞**
    等待 reader 退出。SQLite 默认 checkpoint 会等到 `busy_timeout` 超时（PRAGMA 设了 5000ms）后返回失败。
    实际行为：
    1. 若 5s 内 reader 退出 → TRUNCATE 成功。
    2. 若 5s 后仍有 reader → `PRAGMA wal_checkpoint(TRUNCATE)` 返回 `(busy=1, ...)`，SQLite **不抛异常**
       仅返回状态码 → `conn.execute(sa_text(...))` 不会触发 `try/except`，shutdown hook 静默"成功"，但
       WAL 未实际 truncate。
    3. 这是符合预期的降级路径（避免 shutdown 永久卡死），但**没有日志记录降级**：
       ```python
       with engine.begin() as conn:
           result = conn.execute(sa_text("PRAGMA wal_checkpoint(TRUNCATE)"))
           # ← 应当 result.fetchone() → (busy, log, checkpointed) 三元组；
           #    busy=1 表示有阻塞 reader 未完成 checkpoint。当前代码丢弃返回值。
       ```
  - **修复建议（Low 优先级）**：fetchone() + 若 `busy != 0` 则 `logger.warning("WAL checkpoint 部分推进 busy={busy} log={log} checkpointed={checkpointed}")`。
  - **触发概率**：低-中（shutdown 时确实可能有 plugin 命令在跑）
  - **影响范围**：可观测性丢失 —— `app.db-wal` 未 truncate 时用户不知道为什么文件还在。功能上无回归（下次启动自动 checkpoint 收尾）。
- **接线顺序额外验证**：bot.py:168-172 注释清晰说明 LIFO + 顺序意图（HTTP → WAL），与代码实际注册顺序匹配。✅
- **结论**：PASS-with-Note。M-3 闭合 R8-D-3 主问题（长跑 WAL 累积），但 checkpoint busy 状态未观测，
  见 R9-D-4。

### R8-D-4 [PASS] rollback 异常链路保留原 commit 异常
- **文件**：`db.py:536-543, 566-573`
- **修复前行为**（Round 7）：
  ```python
  try:
      session.commit()
  except Exception:
      session.rollback()  # ← 若 rollback 也抛，重新进入 except 链
      raise              # ← Python 语义：rollback 抛错时，裸 raise 抛 rollback 异常，commit 异常仅作 __context__
  ```
- **修复后行为**（Round 8）：
  ```python
  try:
      session.commit()
  except Exception as commit_exc:
      try:
          session.rollback()
      except Exception:  # noqa: BLE001
          logger.exception("rollback 自身抛错（已保留原 commit 异常）")
      raise commit_exc  # noqa: TRY201
  ```
  Python 语义对照：
  - 内层 except 捕获 rollback 异常 → logger.exception 在内层打印 rollback 的 traceback（含 chain）。
  - 外层 `raise commit_exc` 显式抛**原 commit 异常**对象 → caller 拿到的 `e` 是 commit 异常。
  - rollback 异常通过 `__context__` 链保留（Python 自动设置），traceback 仍可追溯。
  - `# noqa: TRY201` 显式放行 ruff TRY201（"verbose raise"），合理。
- **核对 `ensure_default_groups()`（line 510-545）** 和 **`ensure_default_stats()`（line 548-575）** 模式完全相同。✅
- **变量名 lint 冲突**（PRD 提出）：`commit_exc` 而非通用 `e`，避开了 ruff 的 `EM101` 或 `BLE001` 与
  `except Exception as e: raise e` 的常见双重报告。`# noqa: TRY201` 已直接放行，无 lint 冲突。✅
- **traceback 显示差异**：`raise commit_exc` 与裸 `raise` 在 traceback 上的区别 ——
  - 裸 `raise`：active exception 此时是 rollback 异常（因为 inner except 块处理过），裸 `raise` 抛 rollback 异常，
    commit 异常作 `__context__`。
  - `raise commit_exc`：显式抛 commit 异常对象。但 commit 异常的 `__context__` 已被 inner except 改写为
    rollback 异常（Python "exception chaining during handling"），traceback 顺序：
    ```
    <rollback exc traceback>
    During handling of the above exception, another exception occurred:
    <commit exc traceback>
    ```
    实际显示**反了**：traceback 顶部是 rollback 异常，底部才是 commit 异常。
  - 但 caller 拿到的 `e` / `type(e)` 是 commit 异常 —— 业务诊断**类型正确**，traceback 仍可追双重 chain。
- **结论**：PASS。Round 8 修复正确闭合 Python 异常链路混淆。`raise commit_exc` 在 traceback 显示上与裸 `raise`
  顺序不同但**信息完整**，且 caller `type(e)` 拿到的是 commit 异常（业务期望的根因）。

### R8-D-5 [PASS] _set_sqlite_pragma dialect guard
- **文件**：`db.py:402-403`
- **修复前行为**（Round 7）：`_set_sqlite_pragma` 监听 `connect` 事件，无 dialect 守卫；未来切 PostgreSQL/MySQL 时
  `isolation_level = None` + `PRAGMA` 都会抛 `OperationalError`。
- **修复后行为**（Round 8）：
  ```python
  @event.listens_for(_engine, "connect")
  def _set_sqlite_pragma(dbapi_connection, _connection_record):  # noqa: ANN001
      if _engine is not None and _engine.dialect.name != "sqlite":
          return
      dbapi_connection.isolation_level = None
      cursor = dbapi_connection.cursor()
      ...
  ```
- **`_engine is not None` 必要性**（PRD 提出）：
  - listener 在 `@event.listens_for(_engine, "connect")` 装饰**那一刻**就完成绑定。在 line 395 `@event.listens_for`
    执行前，line 382 `_engine = create_engine(...)` 已 assign。装饰执行时 `_engine is not None` 必为真。
  - 回调（_set_sqlite_pragma）触发时，`_engine` 闭包捕获的是模块级全局名 —— 通过 `global _engine` 在 Python 语义
    下闭包按"by name"捕获，回调内访问 `_engine` 会读全局当前值。如果 caller 在创建 engine 后 reset `_engine = None`
    再触发 connect 事件，理论上能读到 None。但实际**没有任何路径会 reset `_engine = None`**（grep 全仓只有
    line 376 初值 + line 382 写入），所以 `is not None` 是**冗余守卫**。
  - 保留更稳：未来若新增 dispose + reset 路径，守卫已就位。设计姿态合理。✅
- **结论**：PASS。`_engine is not None` 冗余但无害，符合"未来 dispose 路径"的防御。dialect guard 与
  `_force_immediate_begin` 对称（line 420）。

---

## Part B: 全量再扫新发现

### R9-D-4 [Medium] wal_checkpoint_truncate 静默忽略 busy 返回，运维不可观测
- **文件**：`db.py:954-961`
- **现状**：见 Part A M-3 Note。`PRAGMA wal_checkpoint(TRUNCATE)` 返回 `(busy, log, checkpointed)` 三元组，
  当前代码 `conn.execute(sa_text(...))` 不消费返回值。
- **修复后行为建议**：
  ```python
  with engine.begin() as conn:
      row = conn.execute(sa_text("PRAGMA wal_checkpoint(TRUNCATE)")).fetchone()
      if row is not None:
          busy, log_pages, checkpointed = int(row[0]), int(row[1]), int(row[2])
          if busy != 0:
              logger.warning(
                  f"WAL checkpoint 部分推进：busy={busy} log_pages={log_pages} checkpointed={checkpointed}"
              )
          else:
              logger.info(
                  f"WAL checkpoint 完成：log_pages={log_pages} checkpointed={checkpointed}"
              )
  ```
- **触发概率**：中（shutdown 时 plugin 慢命令未结束 → reader 持锁 → busy=1）
- **影响范围**：可观测性。Round 8 修复主问题（长跑 WAL 累积）已闭合，剩此 polish 项。

### R9-D-5 [Low] _user_columns_ensured 模块级 flag 在 shutdown / 测试 reload 后状态残留
- **文件**：`db.py:761-790`
- **现状**：`_user_columns_ensured` 是模块级布尔，进程级生命周期。生产单进程下无问题。但：
  1. 测试 reload module（importlib.reload 或 pytest fixture）后，flag 残留 True → ALTER 不再执行。
     测试若先创建库 → reload module → 第二轮调用 `_ensure_user_columns()` 直接 no-op，**不会执行 ALTER**，
     可能让测试库与 ORM schema 不一致。
  2. 与 `_engine` / `_session_factory` 不同——后两者在 reload 后会重新创建（None 初值再走 init 分支），
     但 flag 也回到 False，所以 **flag 状态本身随 reload 同步重置**。逻辑无误。
- **结论**：实际无 bug，Round 8 R8-D-2 已标 Info。本轮**维持 Info**，记录避免未来重构改 flag 生命周期时
  引入 reload 残留问题。

### R9-D-6 [Low] DATABASE_URL SQLite timeout 参数缺失，与 PRAGMA busy_timeout 表达力不重叠
- **文件**：`db.py:382-387`
- **现状**：
  ```python
  _engine = create_engine(
      DATABASE_URL,
      future=True,
      echo=False,
      connect_args={"check_same_thread": False},
  )
  ```
  pysqlite 默认 `timeout=5.0`（秒），与 `PRAGMA busy_timeout = 5000` 表达**相同**意图（5000ms 内对锁竞争重试），
  实际 SQLite 内部以 PRAGMA 为准。无重叠 bug。
  - `check_same_thread=False`：允许跨线程使用 connection。结合 `BEGIN IMMEDIATE` + session-level 串行化，
    nonebot async 模型下安全。
  - 未在 `connect_args` 设 `timeout`：pysqlite 用默认 5.0，与 PRAGMA 5000 一致，安全。
- **修复建议**：可在 `connect_args` 显式 `timeout=5.0` 作"可读性同步"（与 PRAGMA busy_timeout 一致），
  但**功能上无差异**。Low / Info 级别。
- **触发概率**：极低
- **影响范围**：可读性。

### R9-D-7 [Info] _engine.dispose() 未在 on_shutdown 调用
- **文件**：`db.py:942-961` / `bot.py:168-184`
- **现状**：wal_checkpoint_truncate docstring 显式说"不负责 engine.dispose（engine 会在进程退出时自然释放）"
  （line 952）。**这是 Round 8 显式的设计决定**：
  - SQLAlchemy `engine.dispose()` 主要用于显式归还连接池 / 跨进程 fork 场景。
  - nextbot 单进程 daemon，进程退出时 OS 回收所有 sqlite3 文件句柄，无 leak 风险。
  - on_shutdown 加 dispose() 会让最后一次 checkpoint 之后的连接池清理排队，引入额外 shutdown 时间。
- **结论**：Info / 不修复。Round 8 决定合理。

### R9-D-8 [Info] 模型层字段类型与 SQLAlchemy 2.0 Mapped 一致性
- **文件**：`db.py:117-372`
- **核对维度**：
  1. **ORM `User` 类（line 135-167）与 `_USER_COLUMN_MIGRATIONS`（line 736-759）一一对应**：
     - is_banned / banned_at / ban_reason（ban 3 列）✅
     - rob_total_count / rob_success_count / rob_total_gain / rob_total_loss / rob_total_penalty /
       last_rob_time / rob_protected（rob 7 列）✅
     - guess_total_count / guess_win_count / guess_total_gain / guess_total_loss（guess 4 列）✅
     - dice_total_count / dice_win_count / dice_total_gain / dice_total_loss（dice 4 列）✅
     - 共 18 列，与 ORM 完全对齐。无新增 / 漏迁。✅
  2. **BOOLEAN 字段在 SQLite 下存储约定**：
     - ORM `Boolean` 在 SQLite 下走 `INTEGER 0/1`（SQLAlchemy 默认 type adapter）。
     - 但 `_USER_COLUMN_MIGRATIONS` line 738 / 748 写的是 `"is_banned" INTEGER NOT NULL DEFAULT 0` /
       `"rob_protected" INTEGER NOT NULL DEFAULT 0` —— 与 ORM Boolean→INTEGER 映射一致。✅
     - 但 `ensure_shop_schema`（line 644-649）的 ALTER 用了 `BOOLEAN NOT NULL DEFAULT 0`（line 644 `show_command`、
       line 647 `require_online`、line 655 `is_mystery`）—— SQLite 的 BOOLEAN 是**类型亲和性 NUMERIC**，存储
       仍是 INTEGER。SQLAlchemy reflection 读出来一致。**ALTER 用 BOOLEAN vs INTEGER 字面量 mismatch 不致命**，
       但**风格不统一**：
       - `db.py:738`（user.is_banned）：`INTEGER NOT NULL DEFAULT 0`
       - `db.py:644`（shop_item.show_command）：`BOOLEAN NOT NULL DEFAULT 0`
     - 都映射到同一 SQLite 类型亲和组，但未来 reflection / 跨 dialect migration 时风格差异会让人困惑。
- **结论**：Info。字段语义一致，命名风格不统一是**轻微 polish 项**，不影响功能。
- **触发概率**：极低
- **影响范围**：可读性。

### R9-D-9 [Info] ShopItem.shop_id / LotteryPrize.pool_id 索引设计
- **文件**：`db.py:300, 345`
- **现状**：
  - `ShopItem.shop_id` 已 `index=True`（line 300）—— ✅ 覆盖 `WHERE shop_id = ?` 列表查询
  - `LotteryPrize.pool_id` 已 `index=True`（line 345）—— ✅ 覆盖 `WHERE pool_id = ?` 列表查询
  - `WarehouseItem.user_id` 已 `index=True`（line 271）+ `ensure_warehouse_fk_schema` 显式创建 index ✅
- **未覆盖**：
  - `RedPacketClaim.red_packet_id`（line 253）—— 没有显式 index，但有 `uq_redpacket_claimer (red_packet_id, claimer_user_id)` 复合唯一索引，
    覆盖 `WHERE red_packet_id = ?` 查询前缀（B-tree leading column）。✅
  - `RedPacketClaim.claimer_user_id` —— 复合索引的非前缀，`WHERE claimer_user_id = ?` 不走索引。`red_packet.py` 是否有此查询模式？
    grep 显示 `red_packet.py` 主要按 red_packet_id 查询，少量 claimer 查询走 `claim.amount.sum()` 等聚合，
    在小红包 (<=100 claim) 量下全表扫无感知。**Round 7 / 8 已隐含接受**，本轮维持 Info。
  - `UserSignRecord.user_id`（line 210）—— 没有显式 index，但有 `uq_sign_record_user_date (user_id, sign_date)` 复合唯一索引
    + `ix_sign_record_date_created (sign_date, created_at)` 复合索引。`WHERE user_id = ?` 查询走复合索引前缀。✅
- **结论**：Info。索引覆盖度合理，无新发现。

### R9-W-1 [Info] warehouse_lock dict 内存增长
- **文件**：`warehouse_lock.py:9-17`
- **状态**：Round 7 标 D-1.11 Low、Round 8 复扫维持跳过。本轮 Read 全文确认实现极简（7 行核心逻辑）。
- **核对**：
  - `_WAREHOUSE_LOCKS: dict[str, asyncio.Lock]` 模块级 dict，never cleared。
  - 长跑场景：注册用户数线性增长 → dict 大小线性增长。单 `asyncio.Lock` ≈ 200 bytes（CPython 实现）。
    10k 用户 ≈ 2 MB。100k 用户 ≈ 20 MB。**生产规模可控**。
  - 已有用例：`async with warehouse_lock(user_id):` 11 处 + `_acquire_two_warehouse_locks` 双锁排序（line 241-246），
    避免 deadlock。
- **结论**：Info / 不修复。Round 7 / 8 决定合理。

### R9-S-1 [Info] screenshot_temp /tmp 硬编码
- **文件**：`screenshot_temp.py:30-32`
- **状态**：Round 7 标 D-1.13 Low、Round 8 维持跳过。本轮 Read 全文确认实现极简。
- **核对**：
  - `Path("/tmp")` 硬编码 —— Linux 标准 / macOS 可用 / Windows 无 `/tmp`。nextbot 生产部署在 Linux，OK。
  - `beijing_filename_timestamp() + uuid.uuid4().hex[:8]` —— 时间戳 + 8 位 uuid 后缀，碰撞概率极低（同秒 2^32 才可能）。
  - `contextlib.suppress(OSError)` —— 清理失败不抛，避免 cleanup 异常污染主流程。
  - 异步上下文管理器签名正确，`AsyncIterator` 在 `TYPE_CHECKING` 内 import 避免运行时开销。
- **结论**：Info / 不修复。

---

## 结论

### Part A 复审：5/5 PASS（+ 2 PASS-with-Note）

| 项 | 文件 | 状态 |
|---|---|---|
| M-1 execute_rowcount fast-fail | db.py:490-507 | PASS-with-Note（fail-fast 是本意，41 处 caller 无 SELECT 误传） |
| M-2 _run_migration helper | db.py:441-483 | PASS-with-Note（外层防御性冗余合理） |
| M-3 wal_checkpoint_truncate | db.py:942-961 + bot.py:173-177 | PASS-with-Note（busy 返回值未观测，见 R9-D-4） |
| R8-D-4 rollback 异常链路 | db.py:536-543, 566-573 | PASS（commit 异常正确重抛，`# noqa: TRY201` 无 lint 冲突） |
| R8-D-5 dialect guard | db.py:402-403 | PASS（`_engine is not None` 冗余但无害） |

**Round 7 8 条修复在 Round 8 已全部 PASS，Round 9 本轮独立 Read 验证保留性，无 regression**：
H-1 (isolation_level=None, line 405) / H-2 (WAL+synchronous, line 410-411) / D-1.2 (docstring) /
D-1.3 (engine.begin()) / D-1.4 (_USER_COLUMN_MIGRATIONS) / D-1.5 (sqlite_version) / D-1.6 (显式 rollback) /
D-1.7（已升级为 M-1 fast-fail）。

### Part B 新发现统计

| 严重度 | 数量 | 项 |
|---|---|---|
| High | 0 | — |
| Medium | 1 | R9-D-4（WAL checkpoint busy 返回值未观测） |
| Low | 2 | R9-D-5（_user_columns_ensured reload 残留）、R9-D-6（DATABASE_URL timeout 显式化） |
| Info | 4 | R9-D-7（engine.dispose 不修是合理设计）、R9-D-8（BOOLEAN vs INTEGER 风格不统一）、R9-D-9（索引覆盖度复核）、R9-W-1 / R9-S-1（保留 Round 7 / 8 跳过决定） |

### 是否还有值得修的问题？

**值得 Round 9 优先修复（性价比 + 真实触发概率）**：
1. **R9-D-4（WAL checkpoint busy 观测）** —— 一行 `fetchone()` + 条件 `logger.warning`，可观测性提升明显，
   防止 shutdown 时 WAL 未 truncate 但运维不知情。

**可推迟（Info / 风格 polish）**：
- R9-D-5（flag reload 残留，无生产 bug）
- R9-D-6（timeout 显式化，纯可读性）
- R9-D-8（BOOLEAN vs INTEGER ALTER 字面量风格统一）
- R9-D-9（已确认索引覆盖度足够）

**Round 8 跳过 / 下调项本轮均不复挖**（R8-D-2 / R8-D-7 / R8-D-8 / D-1.10~15 / R8-W-1 / R8-S-1）。

### 与 Round 8 audit 的差异

- Round 8 提了 5 Medium + 3 Low；Round 8 实施修复了其中 3 项（M-1 / M-2 / M-3）+ 2 项小修（R8-D-4 / R8-D-5）。
- 本轮发现的 R9-D-4 是 M-3 修复**周边新暴露面**（busy 返回值丢弃），优先级 Medium 但工作量极小（1 行）。
- 其余 4 项 Info / Low 都是设计 polish / forward-compat，无真实生产 bug 路径。

**整体评估**：Round 8 修复质量高，Round 9 仅剩 1 项 Medium 值得动手（R9-D-4），其余均可关闭审计循环。
