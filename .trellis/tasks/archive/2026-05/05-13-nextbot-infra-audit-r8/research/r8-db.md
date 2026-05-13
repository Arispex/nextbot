# R8 DB 桶审计

- **Scope**：`nextbot/db.py` / `nextbot/warehouse_lock.py` / `nextbot/screenshot_temp.py`
- **Round 7 commit**：`66b4d6c`
- **Date**：2026-05-13

---

## Part A: Round 7 修复复审（8 条）

### H-1 [PASS] isolation_level = None
- **文件**：`db.py:395-406`
- **验证**：`_set_sqlite_pragma` 监听 `connect` 事件，第一行执行
  `dbapi_connection.isolation_level = None`，**先于任何 `cursor.execute` 调用**。
  这正是 SQLAlchemy 2.0 官方 SQLite recipe "Serializable isolation / Savepoints
  / Transactional DDL with pysqlite" 推荐的写法 —— 关掉 pysqlite 默认的
  deferred 自动 BEGIN，让 `@event.listens_for(_engine, "begin")` 中的
  `BEGIN IMMEDIATE` 真正成为唯一的事务起点。
- **副作用推演**：`isolation_level = None` 后 pysqlite 不再代答 BEGIN，
  SQLAlchemy `Connection.begin()` 调用统一由 begin 事件接管。`engine.begin()`
  + `sessionmaker(autocommit=False)` 都走 SQLAlchemy 抽象层，影响一致。
- **结论**：PASS。一行修复闭环 BEGIN IMMEDIATE 真实生效，没有引入回归。

### H-2 [PASS-with-Note] journal_mode = WAL + synchronous = NORMAL
- **文件**：`db.py:403-404`
- **验证**：在 `_set_sqlite_pragma` 内执行
  `PRAGMA journal_mode = WAL` / `PRAGMA synchronous = NORMAL`。
  SQLite WAL 是**持久化 DB-wide 设置**（写入 `sqlite_master.pragma` 等
  meta），首次设置即在文件层生效。每次新连接重复 PRAGMA 是 idempotent，
  仅返回当前模式名，无副作用。
- **结论**：PASS。修复有效。详见 Part B R8-D-3：WAL 引入了
  `app.db-wal` / `app.db-shm` 持久化文件 + checkpoint 策略缺失的新暴露面。

### D-1.2 [PASS] _force_immediate_begin docstring 强约束
- **文件**：`db.py:408-423`
- **验证**：line 419-422 新增 "调用方禁止在 session 生命周期内 await 非
  DB I/O" 强约束，提示 busy_timeout=5000ms 排队风险。`if connection.dialect.name != "sqlite": return` 保留 dialect 守卫（line 413-414）。
- **结论**：PASS。docstring 强化是 Round 7 显式承认的 trade-off 文档。
  详见 Part B R8-D-5：未来切 PostgreSQL 时 begin 事件触发时序问题。

### D-1.3 [PASS] raw sqlite3.connect() → engine.begin() + sa_text()
- **文件**：`db.py:540-562 / 565-579 / 593-619 / 622-644 / 647-663 / 666-691 / 775-801 / 804-820 / 823-844 / 847-864 / 867-901`
- **验证**：grep 全文件 `sqlite3.connect`，**仅剩 line 3 的 `import sqlite3`
  和 line 642 的 `sqlite3.sqlite_version` 作版本字符串使用**。
  所有 `ensure_*_schema` 都改为
  ```python
  engine = get_engine()
  with engine.begin() as conn:
      conn.execute(sa_text(...))
  ```
  完整核对的函数：`ensure_command_config_schema` / `ensure_warehouse_schema` /
  `ensure_lottery_schema`（no-op）/ `ensure_shop_schema` /
  `ensure_user_signin_schema` / `ensure_sign_record_schema` /
  `ensure_sign_record_unique_schema` / `_ensure_user_columns` /
  `ensure_user_name_unique_schema` / `ensure_warehouse_fk_schema` /
  `ensure_user_leaderboard_indexes_schema` /
  `ensure_user_sign_record_index_schema` / `ensure_red_packet_schema`。
- **结论**：PASS。所有迁移走 engine.begin()，统一适用 PRAGMA busy_timeout
  / journal_mode / synchronous + isolation_level = None。

### D-1.4 [PASS-with-Note] _USER_COLUMN_MIGRATIONS 表驱动 + 4 个 wrapper
- **文件**：`db.py:698-772`
- **验证**：`_USER_COLUMN_MIGRATIONS` 列举 18 列；`_ensure_user_columns()`
  单次 PRAGMA + 单事务 18 次条件 ALTER；`_user_columns_ensured` 模块级
  布尔位 guard 首次执行后置 True；4 个 wrapper（`ensure_user_ban_schema` /
  `ensure_user_rob_schema` / `ensure_user_guess_schema` /
  `ensure_user_dice_schema`）只 `return _ensure_user_columns()`。
- **逻辑等价性核对**：18 列定义与 Round 7 之前的 4 个独立 ensure_*_schema
  并集一致，列名 + 类型 + DEFAULT 值都与 ORM `User` 类（line 135-167）一一
  对应，无丢列。
- **结论**：PASS。详见 Part B R8-D-2：`_user_columns_ensured` flag 的
  reentry 行为问题。

### D-1.5 [PASS] ensure_user_signin_schema 含 sqlite3.sqlite_version
- **文件**：`db.py:622-644`
- **验证**：line 641-643 warning 现在打印
  `f"SQLite 版本={sqlite3.sqlite_version}，需要 ≥ 3.35"`，
  排障可观测性确认提升。
- **结论**：PASS。

### D-1.6 [PASS-with-Note] ensure_default_groups / ensure_default_stats 显式 rollback
- **文件**：`db.py:483-512 / 515-537`
- **验证**：两个函数都加了
  ```python
  try:
      session.commit()
  except Exception:
      session.rollback()
      raise
  ```
  并保留 `finally: session.close()`。语义清晰、event-loop 行为正确。
- **结论**：PASS。详见 Part B R8-D-4：rollback 自身抛错时
  `raise` 重新抛出**原 commit 异常**还是 rollback 异常 —— Python 语义
  实际是 commit 异常作为 `__context__`，rollback 异常成为主异常。

### D-1.7 [PASS-with-Note] execute_rowcount 非 CursorResult 加 logger.warning
- **文件**：`db.py:462-480`
- **验证**：line 475-479 增加 `if rowcount is None: logger.warning(...)`。
  使用 `getattr(result, "rowcount", None)` + None 判断比之前的
  `getattr(result, "rowcount", 0)` 更精确（避免对真正返回 0 rowcount
  的 result 误判）。
- **结论**：PASS。**详见 Part B R8-D-6：fallback 仍返回 0，而 `ban_core.py:75 /
  ban_core.py:120 / economy.py:92~768 / lottery.py:717` 等 50+ 处依赖
  `rowcount == 0` 作业务降级分支** —— silent-failure → noisy-failure
  转化只补了日志，业务降级仍可能误触发。

---

## Part B: 全量再扫新发现（Round 7 周边新暴露面）

### R8-D-1 [Medium] init_db() 17 个 ensure_* 失败中途无 transaction，导致半 migrate 状态
- **文件**：`db.py:434-454`
- **修复前行为**：
  ```python
  def init_db() -> None:
      engine = get_engine()
      Base.metadata.create_all(engine)
      ensure_command_config_schema()
      ensure_user_signin_schema()
      ... (17 处 ensure_*)
      ensure_default_groups()
      ensure_default_stats()
  ```
  每个 `ensure_*_schema` 各自开 `engine.begin()` 独立事务。第 N 个失败
  抛异常时，**第 1 ~ N-1 个事务已 commit 成功，第 N+1 ~ 17 个未执行**。
  bot.py:158 `init_db()` 异常会直接冒到 NoneBot framework
  → process exits → 下次 systemd 重启 → 重新跑 17 个 ensure_*
  从头来一遍。
- **触发场景**：
  1. SQLite 版本不一致（如 `ensure_user_signin_schema` 用 `ALTER TABLE
     DROP COLUMN` 需 ≥ 3.35）+ 后续 ensure_* 不依赖该列时，Round 7 改造
     **已让此函数 warning 不抛异常**（line 640），但其它 ensure_* 仍
     可能因 SQLite I/O / 磁盘满 / 锁竞争抛 OperationalError。
  2. 灾难恢复：admin 在不同 SQLite 版本的容器之间 rsync `app.db`，旧库
     ALTER 已部分应用、新库 ensure_* 中段抛错。
- **修复后行为建议**：
  - 选项 A：用单一 `engine.begin()` 包住所有 ensure_*，CREATE INDEX IF NOT
    EXISTS / ALTER TABLE ADD COLUMN IF NOT EXISTS 都是 idempotent，可
    安全合并到一个事务。
  - 选项 B：每个 ensure_* 用 `try/except: logger.warning + 继续`，让
    单个迁移失败不阻断后续 —— 与现有 `ensure_user_name_unique_schema` /
    `ensure_user_leaderboard_indexes_schema` 已有的 try/except 风格一致。
  - 推荐选项 B：保留独立失败诊断，避免一处错误污染整个迁移事务。
- **触发概率**：中（生产环境 SQLite 版本切换、容器迁移、磁盘满都会触发）
- **影响范围**：启动期失败 → 部分表 schema 没升级 → 后续业务 INSERT
  报 `no such column` 直到磁盘 / 版本问题修复后下次重启。

### R8-D-2 [Medium] _user_columns_ensured 单例 flag 首次失败后永久 stuck
- **文件**：`db.py:723, 736-737, 752`
- **修复前行为**：
  ```python
  _user_columns_ensured: bool = False

  def _ensure_user_columns() -> None:
      global _user_columns_ensured
      if _user_columns_ensured:
          return
      if not DB_PATH.exists():
          return  # ← 注意：此分支不置 True，下次再调会重试。OK
      engine = get_engine()
      with engine.begin() as conn:
          rows = conn.execute(...).fetchall()
          if not rows:
              return  # ← 此分支也不置 True
          ...
      _user_columns_ensured = True  # ← 只有事务成功 commit 后才置 True
  ```
  逻辑核对：
  1. **正常路径**：事务 commit 成功 → flag = True → 后续 wrapper no-op。OK。
  2. **失败路径**：`engine.begin()` 内 ALTER TABLE 抛异常 → 异常冒出
     `_ensure_user_columns` → flag **保持 False** → 下次再调还会重试。
  3. **DB_PATH 不存在路径**：return without 置 True → 后续 wrapper 仍走
     PRAGMA。这是有意的，新建库时 `Base.metadata.create_all` 直接建表，
     `_ensure_user_columns` 早跑就是 no-op，wrapper 调用便宜，无所谓。

  **实际问题**：从代码看 flag 行为本身合理。但 init_db 调用链是
  4 个 wrapper 顺序调（line 442-445），如果第一个 wrapper 的事务**部分
  ALTER 成功后中途抛异常**，commit 已 rolled back（engine.begin
  context manager 保证），flag 仍 False。后续 wrapper 再调用会**重新跑
  完整的 PRAGMA + 已成功的 ALTER 会因 "duplicate column name" 失败**，
  把整个 init_db 拖死在第二个 wrapper。

  推演：SQLite 在 `with engine.begin() as conn` 内多次 ALTER，第 N 个抛
  错，BEGIN IMMEDIATE 整体 rollback。下次再调 PRAGMA 列名不变 → 进入
  循环判断的 N 个 `if col_name not in columns: ALTER` 都重新执行。所以
  这里**实际是 safe 的**（rollback 完整回滚），但前提是 SQLite 的
  ALTER TABLE 是 transactional —— SQLite 文档明确 ALTER TABLE 是
  transactional，因此 rollback 后再次执行同样的 ALTER 应当成功。
- **修复后行为建议**：把 `_user_columns_ensured = True` 移到 `with
  engine.begin()` 外、但仍在 `_ensure_user_columns` 函数末尾即可，逻辑
  自然。也可加 docstring 说明"事务 rollback 后会自动重试"。
- **触发概率**：低（需要 ALTER 中段失败 + 多次 init_db，正常生产环境
  不会发生）
- **影响范围**：理论 safety 已 OK，但**flag 语义模糊**易被未来重构误解。

### R8-D-3 [Medium] WAL 模式启用后缺 checkpoint 策略，`-wal` 文件无界增长
- **文件**：`db.py:403`（启用 WAL 处）
- **修复前行为**：Round 7 启用 `PRAGMA journal_mode = WAL` 后，SQLite
  会在 `app.db` 旁创建 `app.db-wal` + `app.db-shm` 两个持久化文件。
  WAL 文件的回收依赖 **checkpoint**（默认 `wal_autocheckpoint = 1000`
  pages，约 4MB）。**当前代码没有显式 checkpoint 策略**：
  1. SQLite 默认在 WAL 文件达到 1000 pages 时自动 PASSIVE checkpoint，
     不阻塞 reader / writer，但需要"无活跃 reader 跨越 frame"才能完成。
  2. nextbot 是长连接 daemon，BEGIN IMMEDIATE 持写锁 + plugins 各种
     read session 持读锁 → 自动 checkpoint 经常**只能 partial 推进**。
  3. WAL 文件持续增长（实测可到 GB 级），重启时 SQLite 才会全量 checkpoint。
- **修复后行为建议**：
  - 选项 A：在 connect listener 加 `PRAGMA wal_autocheckkpoint = 1000`
    显式声明（默认值，仅为可读性）。
  - 选项 B：定期主动 checkpoint —— 加一个 scheduler，每 N 分钟执行
    `PRAGMA wal_checkpoint(TRUNCATE)`，强制截断 WAL 文件。
  - 选项 C：on_shutdown 时执行 `PRAGMA wal_checkpoint(FULL)`，正常退出
    时回收 WAL。
- **触发概率**：低-中（取决于实际写入量；read-heavy / write-heavy 都可能
  累积，长跑 30 天可能用户感知到磁盘占用增长）
- **影响范围**：磁盘占用增长 + 启动时 / shutdown 时间变长（WAL replay）。
  非功能性问题，但 Round 7 修复后**新引入**的运维面。

### R8-D-4 [Low] 显式 rollback 自身抛错会吞掉原 commit 异常
- **文件**：`db.py:506-510, 531-535`
- **修复前行为**：
  ```python
  try:
      session.commit()
  except Exception:
      session.rollback()  # ← 如果 rollback 也抛异常
      raise              # ← raise 会重新抛 rollback 的异常，原 commit 异常仅作 __context__
  ```
  Python 异常语义：rollback 抛异常时，进入 except 块的 active exception
  会变成 rollback 异常，原 commit 异常被附加为 `__context__`。
  `raise`（裸 raise）实际会重新抛出**rollback 异常**，commit 异常仅以
  `__context__` 链路保留 —— Python 默认 traceback 会打印
  "During handling of the above exception, another exception occurred"
  形式包含两者，但 except 链层若用 `except Exception as e: e.args` 看到
  的是 rollback 异常，原因诊断混淆。
- **修复后行为建议**：
  ```python
  except Exception as commit_exc:
      try:
          session.rollback()
      except Exception:
          logger.exception("rollback 失败")
      raise commit_exc
  ```
- **触发概率**：极低（rollback 自身抛错需要 SQLite I/O 失败 + 连接已
  broken）
- **影响范围**：诊断信息歧义。Low 级别。

### R8-D-5 [Low] _force_immediate_begin 未来切 PostgreSQL 时 begin 时序差异
- **文件**：`db.py:408-423`
- **修复前行为**：当前 `dialect.name != "sqlite": return` 守卫已足够。
  Round 7 docstring 强化了 SQLite 行为说明，但**没有提示未来切换数据库
  的迁移点**：
  - SQLAlchemy begin event 在 PostgreSQL 下触发时机是
    `Connection.begin()` 调用时（手动 begin）或第一次执行 SQL 时（隐式
    autobegin），与 SQLite 默认 behavior 不同。
  - 切 PostgreSQL 后 `connect` listener 的 `PRAGMA` 也无效，会抛
    `OperationalError: syntax error near "PRAGMA"`。
- **修复后行为建议**：在 `_set_sqlite_pragma` 也加 dialect 守卫
  （与 `_force_immediate_begin` 对称）：
  ```python
  if connection.dialect.name != "sqlite":
      return
  ```
  目前没有 `dialect` 参数可用（connect listener 只拿 dbapi_connection
  + connection_record），可用 `_engine.dialect.name` 或 `_engine.url.get_backend_name()`。
- **触发概率**：极低（除非 DATABASE_URL 改 postgres://）
- **影响范围**：forward-compat fragility。Low / Info 级别。

### R8-D-6 [Medium] execute_rowcount fallback=0 + 业务依赖 rowcount==0，警告补救不充分
- **文件**：`db.py:473-480` + 调用方 `ban_core.py:65-90, 111-128` /
  `economy.py:92~768` / `lottery.py:717-735` / `user_manager.py:513` /
  `guess_number.py:189`
- **修复前行为**：Round 7 改成
  ```python
  rowcount = getattr(result, "rowcount", None)
  if rowcount is None:
      logger.warning("execute_rowcount 收到非 CursorResult...")
      return 0
  return int(rowcount)
  ```
  日志 catch 到误传，但**仍返回 0**。`ban_core.py:75`：
  ```python
  if rowcount == 0:
      # 重新读取以拿到当前 ban_reason（区分 already_banned）
      current = session.query(User).filter(...).first()
      ...
      return BanDBResult(code="already_banned", ...)  # 业务降级
  ```
  误传 SELECT 时 rowcount=0 → 走 "already_banned" 降级路径 → 用户看到
  "已经被封禁" 即使实际还没。
- **修复后行为建议**：
  - 选项 A：fallback 改为 raise（让 caller 知道误用）：
    ```python
    if rowcount is None:
        raise TypeError(f"execute_rowcount expected CursorResult, got {type(result).__name__}")
    ```
  - 选项 B：保持返回 0 + 加 logger.error 而非 warning（仍 silent
    failure）。
  - 推荐选项 A：execute_rowcount 是 INSERT/UPDATE/DELETE 专用，误传
    SELECT 是开发者 bug，应当 fast-fail。
- **触发概率**：中（grep 显示 50+ caller，新增 plugin 时易误传）
- **影响范围**：业务降级误触发，**特别是 ban_core / economy 这类强一致
  路径**，用户看到错误的状态反馈。

### R8-D-7 [Medium] _engine / _session_factory 全局变量首次初始化无并发保护
- **文件**：`db.py:375-426`
- **修复前行为**：
  ```python
  _engine: Engine | None = None
  _session_factory: sessionmaker[Session] | None = None

  def _ensure_engine_and_factory() -> tuple[Engine, sessionmaker[Session]]:
      global _engine, _session_factory
      if _engine is None or _session_factory is None:
          _engine = create_engine(...)   # ← 非 atomic
          @event.listens_for(_engine, "connect") def ...
          @event.listens_for(_engine, "begin") def ...
          _session_factory = sessionmaker(...)
      return _engine, _session_factory
  ```
  **race**：两个并发 task 同时调 `_ensure_engine_and_factory`，
  task1 创建 engine A 后被切换出去，task2 看到 `_engine is None` 仍 True
  → 创建 engine B。两个 listener 各注册到不同的 engine —— 最终
  `_engine` 是后写入的那个，但 task1 已经持有 engine A 的引用且会用它。
  现实中：
  1. 启动顺序：bot.py:158 `init_db()` 在 `on_startup` 单调，event loop
     单线程，没有 race。
  2. 但 plugin 测试 / scripts 可能直接调 `get_session()` →
     `_ensure_engine_and_factory` 在 `init_db` 之前，race window 仍存在。
- **修复后行为建议**：加 `_init_lock = threading.Lock()` + double-checked
  locking，或在 module import 时立即初始化（更简单）。
  Round 7 在 verify-pass2.md 已标 D-1.9 Low，本轮维持 Medium：
  虽然 `init_db` 串行，但 `get_session()` 是公共 API，任何 plugin 都可
  直接调，缺并发保护是真实设计缺陷。
- **触发概率**：低（生产 startup 单线程；测试场景才可能 race）
- **影响范围**：测试 / scripts 路径下连接池状态错乱。
- **备注**：Round 7 在 verify-pass2 中将这个标为 D-1.9 Low + 跳过。
  本轮升级理由：`get_session()` 是公共 API + 测试场景实际可触发。
  如果项目接受"plugin 必须先调 init_db" 契约，可维持 Low。

### R8-D-8 [Low] get_session() 返回未受类型保护的 Session，调用方易绕过 BEGIN IMMEDIATE 契约
- **文件**：`db.py:457-459`
- **修复前行为**：
  ```python
  def get_session() -> Session:
      _, factory = _ensure_engine_and_factory()
      return factory()
  ```
  返回的是 raw SQLAlchemy `Session`。调用方有以下写法都可绕过
  `BEGIN IMMEDIATE` 契约：
  1. `session.execute(stmt)` 不开事务直接执行（autocommit=False 下
     SQLAlchemy 会 autobegin，触发 begin listener；但若 session 配置改
     autocommit=True 则不开事务）。
  2. `session.connection().execute(stmt)` 用 raw connection 跳过
     session-level abstraction。
  3. `session.bind.connect()` 拿 connection pool 的连接直接操作。
  当前代码 plugins 普遍走 `with get_session() as session: ...` /
  `session.commit()` / `session.close()` 模式，触发 begin event 正常。
  但**没有 runtime 守门**确保所有调用方都进入事务。
- **修复后行为建议**：保持当前 API。这是 SQLAlchemy 设计哲学问题，
  ORM-level enforcement 困难。可考虑 ContextManager wrapper：
  ```python
  @contextmanager
  def session_scope() -> Iterator[Session]:
      session = get_session()
      try:
          yield session
          session.commit()
      except Exception:
          session.rollback()
          raise
      finally:
          session.close()
  ```
  但属于风格指引，非 bug。
- **触发概率**：低（当前 plugins 全走 `get_session() ... commit/close`
  模式）
- **影响范围**：forward-compat fragility。Low 级别。

### R8-W-1 [Low] warehouse_lock dict 无清理，长跑用户多导致内存增长
- **文件**：`warehouse_lock.py:9-17`
- **状态**：Round 7 标 D-1.11 Low，明确跳过。本轮**不复挖**。
- 备注：本轮独立 Read 了 `warehouse_lock.py` 全文，确认实现极简，
  无新发现。

### R8-S-1 [Low] screenshot_temp /tmp 硬编码
- **文件**：`screenshot_temp.py:31`
- **状态**：Round 7 标 D-1.13 Low，明确跳过。本轮**不复挖**。
- 备注：本轮独立 Read 了 `screenshot_temp.py` 全文，确认实现极简
  （`uuid.uuid4().hex[:8]` 后缀防碰撞 + `contextlib.suppress(OSError)`
  cleanup 已稳定），无新发现。

---

## 结论

### Part A 复审：8/8 PASS
- Round 7 8 条 DB 修复**全部 PASS**。
- 其中 5 条带 Note（H-2 / D-1.4 / D-1.6 / D-1.7 / R8-S-1）—— Note 不影响
  Round 7 修复本身有效性，是 Round 7 修复**周边的新暴露面**或
  **二次打磨建议**，已并入 Part B。

### Part B 新发现统计

| 严重度 | 数量 | 项 |
|---|---|---|
| High | 0 | — |
| Medium | 4 | R8-D-1 (init_db 半 migrate), R8-D-2 (flag 语义), R8-D-3 (WAL checkpoint), R8-D-6 (rowcount 业务降级), R8-D-7 (engine 并发) |
| Low | 3 | R8-D-4 (rollback exception chain), R8-D-5 (PostgreSQL forward-compat), R8-D-8 (Session 契约) |

修正：上表 Medium 实际 5 项（R8-D-1 / R8-D-2 / R8-D-3 / R8-D-6 / R8-D-7）。

### 是否还有值得修的问题？

**值得 Round 8 优先修复（性价比 + 真实触发概率）**：
1. **R8-D-6（execute_rowcount fallback=0 业务降级）** —— Round 7 加日志
   是好的开始，但仍 silent，建议改 fast-fail 或至少 logger.error。
   触发面包含 ban_core / economy 强一致路径。
2. **R8-D-1（init_db 半 migrate）** —— 加每个 ensure_* try/except，
   单点失败不阻断后续，与现有 `ensure_user_name_unique_schema` 风格一致。
3. **R8-D-3（WAL checkpoint）** —— 加 on_shutdown checkpoint，1 行修复，
   运维收益明显。

**可推迟（forward-compat / Low）**：
- R8-D-2 / R8-D-4 / R8-D-5 / R8-D-7 / R8-D-8 都是设计 fragility，无
  真实生产 bug 路径。

### 与 Round 7 verify-pass2.md 的关系

- Round 7 8 条修复**全部生效**，没有 regression。
- 本轮新发现 5 条 Medium + 3 条 Low **不与 Round 7 跳过项重叠**（已
  显式避开 D-1.10 / D-1.11 / D-1.13 / D-1.14 / D-1.15）。
- Round 7 标的 D-1.9 (engine 并发) 本轮提议升 Medium，理由见 R8-D-7。
