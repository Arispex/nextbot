# DB / 锁 / 持久化 桶审计

## 覆盖范围

- `nextbot/db.py` [931 行] — engine / session factory / 17 个 `ensure_*_schema` / `init_db` / ORM 模型 / `execute_rowcount` / `ensure_default_*`
- `nextbot/warehouse_lock.py` [17 行] — 全局 `dict[str, asyncio.Lock]` per-user 互斥
- `nextbot/screenshot_temp.py` [39 行] — `/tmp` 截图临时路径 + cleanup 上下文管理器

排除（按任务说明）：BEGIN IMMEDIATE dialect guard 本身（已审）、`ensure_*_schema` 内的 `CREATE INDEX IF NOT EXISTS` 语法、`RESERVED_GROUP_NAMES` / `GROUP_DELETE_FALLBACK`、`execute_rowcount` 的 typing helper、`uuid4().hex[:8]` 唯一性。

---

## 发现列表

### D-1.1 [High] BEGIN IMMEDIATE 事件监听器可能被 pysqlite 默认隔离级别旁路

- 文件：`nextbot/db.py:382-413`
- 修复前行为：
  ```python
  _engine = create_engine(
      DATABASE_URL,
      future=True,
      echo=False,
      connect_args={"check_same_thread": False},
  )

  @event.listens_for(_engine, "connect")
  def _set_sqlite_pragma(dbapi_connection, _connection_record):
      cursor = dbapi_connection.cursor()
      try:
          cursor.execute("PRAGMA busy_timeout = 5000")
      finally:
          cursor.close()

  @event.listens_for(_engine, "begin")
  def _force_immediate_begin(connection):
      if connection.dialect.name != "sqlite":
          return
      connection.exec_driver_sql("BEGIN IMMEDIATE")
  ```
  pysqlite 的 `isolation_level` 默认是 `""`（deferred），这意味着 DBAPI 层会在第一条 DML 之前自动 emit `BEGIN`，**早于** SQLAlchemy 的 `begin` 事件触发。后果是：当 SQLAlchemy 在 `begin` 事件里执行 `BEGIN IMMEDIATE` 时，pysqlite 可能已经隐式开了一个 deferred 事务，导致 `BEGIN IMMEDIATE` 报错 "cannot start a transaction within a transaction" 或被静默忽略（取决于 SQLAlchemy / pysqlite 版本协同）。SQLAlchemy 2.0 官方 SQLite 文档明确给出的 recipe 要求 **必须** 同时在 `connect` 事件里设置 `dbapi_connection.isolation_level = None` 来关闭 pysqlite 的自动 BEGIN，本仓库缺失这一行。
- 修复后行为：在 `_set_sqlite_pragma` 内追加 `dbapi_connection.isolation_level = None`：
  ```python
  @event.listens_for(_engine, "connect")
  def _set_sqlite_pragma(dbapi_connection, _connection_record):
      dbapi_connection.isolation_level = None  # 禁用 pysqlite 自动 BEGIN
      cursor = dbapi_connection.cursor()
      try:
          cursor.execute("PRAGMA busy_timeout = 5000")
      finally:
          cursor.close()
  ```
  这样 SQLAlchemy 的 `begin` 事件成为唯一的事务起点，`BEGIN IMMEDIATE` 真正生效。参考 SQLAlchemy 2.0 docs "Serializable Isolation / Savepoints / Transactional DDL with Pysqlite"。
- 触发概率：高 — 任何走 `engine.connect()` / `with engine.begin()` / `Session.execute()` 的路径都受影响。具体表现取决于 pysqlite/SQLAlchemy 内部协同，可能为：
  1. BEGIN IMMEDIATE 报错被吞 → 实际仍是 deferred → read-modify-write race window 重新出现；
  2. 上层日志里偶发 `OperationalError: cannot start a transaction within a transaction`；
  3. 在某些 SQLAlchemy / pysqlite 版本上 SQLAlchemy 已自适应而无可见症状，但语义不可靠。
- 影响范围：所有 `get_session()` / `engine.begin()` 调用方，即整个项目的 DB 路径。前 4 轮 plugin 审计基于"BEGIN IMMEDIATE 已序列化写"假设作出的修复（lottery / rob / sign / gift 等 read-modify-write 收口）如果该假设不成立，则 race window 实际仍存在。

---

### D-1.2 [High] `_force_immediate_begin` 在事务内 await 导致全局 DB 串行化（设计层放大）

- 文件：`nextbot/db.py:403-413`（policy 源头）— 在 plugin 层放大，例：`nextbot/plugins/warehouse.py:1776-1853`
- 修复前行为：
  ```python
  @event.listens_for(_engine, "begin")
  def _force_immediate_begin(connection):
      if connection.dialect.name != "sqlite":
          return
      connection.exec_driver_sql("BEGIN IMMEDIATE")
  ```
  policy = "每一个 session（含纯读）都持写锁直到 commit/rollback"。这本身是有意为之（消除 read-modify-write race），但配合 plugin 层在事务期间 `await bot.send(...)` 的高频用法，**整个数据库 writer 串行化在最慢的网络 await 上**。代表性证据 `nextbot/plugins/warehouse.py:1776-1853`：
  ```python
  session = get_session()
  try:
      item = session.query(WarehouseItem)...first()
      if item is None:
          await bot.send(event, at + " " + reply_failure("赠送", "该格子为空"))   # ← 持锁 await
          return
      ...
      if quantity_arg is not None and quantity_arg > current_qty:
          await bot.send(event, at + " " + reply_failure(...))                    # ← 持锁 await
          return
      target_slot = _find_first_empty_slot(session, target_user_id)
      if target_slot is None:
          await bot.send(event, at + " " + reply_failure("赠送", "对方仓库已满"))  # ← 持锁 await
          return
      ...
      session.commit()
  finally:
      session.close()
  ```
  每个 `await bot.send` 在 OneBot 反向 WebSocket 慢/抖动时可能阻塞 100ms~数秒。期间全 DB 写入串行 → 任何其他 `get_session()` 触发的 `BEGIN IMMEDIATE` 在 `busy_timeout=5000ms` 内排队 → 超时则 `OperationalError: database is locked`。
- 修复后行为：两条路线任选其一：
  1. **db.py 层**：弱化策略 — 提供 `get_read_session()`（不强制 BEGIN IMMEDIATE）与 `get_write_session()`（强制 BEGIN IMMEDIATE）双入口，by-default 读不持写锁；
  2. **约束 + 文档**：在 db.py 的模块 docstring / `get_session()` docstring 明确 "禁止在 session 生命周期内 await 非 DB I/O"，并在 plugin 层把 `await bot.send` 全部移出 `try: ... finally: session.close()` 块（先把数据 copy 到本地变量，close session 再 send）。
- 触发概率：中 — 取决于：
  - OneBot 适配器的网络抖动频率；
  - 并发命令数（10+ 并发用户、节假日 / 红包 / 抽奖活跃时段）；
  - 单事务内是否有 `bot.send`（实测多处存在）。
  在低 QPS 玩家群可能完全无感，但在大群活动时段会肉眼可见 — 命令"卡住"几秒后批量返回 / 个别命令报 `database is locked`。
- 影响范围：
  - 写入路径：仓库 gift / drop / claim / recycle、抽奖 draw、红包 grab、签到、rob、guess、dice、shop buy；
  - 读路径：仓库 list、leaderboard 查询、user info、permission 校验 —— 任何 `get_session()` 路径同样持写锁因此互相阻塞。

---

### D-1.3 [High] `ensure_*_schema` 混用 `sqlite3.connect()` 与 `engine.begin()`，第二条路径不走 BEGIN IMMEDIATE / busy_timeout

- 文件：
  - `nextbot/db.py:509-562, 576-609, 637-684, 687-799, 894-931` —— `sqlite3.connect(str(DB_PATH))` 共 10 处
  - `nextbot/db.py:612-634, 659-684, 802-828, 831-847, 850-871, 874-891` —— `engine.begin()` 共 6 处
- 修复前行为：
  ```python
  # 第一类：raw sqlite3
  def ensure_command_config_schema() -> None:
      if not DB_PATH.exists():
          return
      conn = sqlite3.connect(str(DB_PATH))
      try:
          rows = conn.execute('PRAGMA table_info("command_config")').fetchall()
          ...
          conn.execute('ALTER TABLE "command_config" ADD COLUMN "usage" ...')
          ...
          conn.commit()
      finally:
          conn.close()

  # 第二类：engine.begin（走 BEGIN IMMEDIATE 监听器）
  def ensure_user_signin_schema() -> None:
      engine = get_engine()
      with engine.begin() as conn:
          rows = conn.execute(sa_text('PRAGMA table_info("user")')).fetchall()
          ...
          conn.execute(sa_text('ALTER TABLE "user" DROP COLUMN "signed_today"'))
  ```
  raw `sqlite3.connect()` 路径：
  1. 完全绕过 SQLAlchemy `connect` / `begin` 事件 → 没有 `PRAGMA busy_timeout = 5000` 设置（pysqlite 默认 `busy_timeout=0`）；
  2. 没有 `BEGIN IMMEDIATE` 序列化；
  3. 如果 `Base.metadata.create_all(engine)`（`init_db()` 第 426 行）已经把引擎 connection pool 里的 conn 留在 idle 状态，**那条 conn 与新开的 raw conn 仍然在抢同一个数据库文件的写锁**；写锁竞争失败时 raw conn 立即抛 `sqlite3.OperationalError: database is locked`（无 5000ms 等待）。
- 修复后行为：所有 `ensure_*_schema` 统一走 `engine.begin()` + `sa_text()`，或者在 raw 路径里至少加 `conn.execute("PRAGMA busy_timeout = 5000")` 作为防御。建议统一 SQLAlchemy 路径：
  ```python
  def ensure_command_config_schema() -> None:
      engine = get_engine()
      with engine.begin() as conn:
          rows = conn.execute(sa_text('PRAGMA table_info("command_config")')).fetchall()
          if not rows:
              return
          columns = {str(row[1]) for row in rows}
          if "usage" not in columns:
              conn.execute(sa_text(
                  'ALTER TABLE "command_config" ADD COLUMN "usage" TEXT NOT NULL DEFAULT ""'
              ))
          ...
  ```
- 触发概率：低（启动时序）— `init_db()` 在 `nonebot.run()` 之前的 `driver.on_startup`（`bot.py:136-145`）单线程执行，理论上无并发竞争；但**所有 `ensure_*_schema` 用 raw sqlite 都是在 `create_all` 之后**，pool 里第一条连接已建立，存在跨连接的潜在锁竞争（在繁忙 SSD 实测概率低，但 NFS / 网络盘上可能复现）。重启时若 systemd 旧进程的 SQLite 锁没释放（极少数 SIGKILL 场景），新进程的 raw conn 会立即报 `database is locked`，被 raw `conn.execute` 抛出来。
- 影响范围：启动失败 / 启动期 schema migrate 不完整。raw 路径的 `try/finally` 也未对 `conn.execute(ALTER)` 失败做日志，失败时静默 close（line 538-539 等的 `if changed: conn.commit()` 之前抛出会跳过 commit，但没人记录哪一列加失败了）。

---

### D-1.4 [Medium] `init_db()` 调用 17 个 `ensure_*_schema`，部分函数同时 read + alter `user` 表存在写放大风险

- 文件：`nextbot/db.py:424-444, 687-799`
- 修复前行为：
  ```python
  def init_db() -> None:
      engine = get_engine()
      Base.metadata.create_all(engine)
      ensure_command_config_schema()
      ensure_user_signin_schema()
      ensure_sign_record_schema()
      ensure_sign_record_unique_schema()
      ensure_user_sign_record_index_schema()
      ensure_user_ban_schema()
      ensure_user_rob_schema()
      ensure_user_guess_schema()
      ensure_user_dice_schema()
      ensure_red_packet_schema()
      ensure_warehouse_schema()
      ensure_shop_schema()
      ensure_lottery_schema()
      ensure_user_name_unique_schema()
      ensure_user_leaderboard_indexes_schema()
      ensure_warehouse_fk_schema()
      ensure_default_groups()
      ensure_default_stats()
  ```
  其中 `ensure_user_ban_schema` / `ensure_user_rob_schema` / `ensure_user_guess_schema` / `ensure_user_dice_schema` 都是**对同一张 user 表**做 `PRAGMA table_info → ALTER TABLE ADD COLUMN`。每次都重新 open raw sqlite3 connection、重新 PRAGMA、重新决策。在已经存在所有列的稳定库上，每次启动至少 4 次冗余 `PRAGMA table_info("user")`（每个 ensure_*_schema 各扫一次表）。
- 修复后行为：合并成单次 PRAGMA + 多列条件 ALTER（伪代码）：
  ```python
  def _ensure_user_columns() -> None:
      engine = get_engine()
      with engine.begin() as conn:
          rows = conn.execute(sa_text('PRAGMA table_info("user")')).fetchall()
          columns = {str(row[1]) for row in rows}
          for spec in USER_COLUMN_MIGRATIONS:  # dataclass list
              if spec.name not in columns:
                  conn.execute(sa_text(spec.alter_sql))
  ```
  顺带把 `init_db` 拆成 `_migrate_schema()` / `_seed_defaults()` 两段，便于单元测试和未来 alembic 迁移。
- 触发概率：中 — 性能层面每次启动多 ~10ms 累计延迟，体感无感；但**逻辑层面**这种分散 ALTER 让 `init_db` 的顺序依赖变成"必须按当前顺序调用否则可能漏列"的隐藏契约，回归风险高。
- 影响范围：启动时长、未来 schema migrate 维护成本、潜在的"某轮 PR 加列只改了一个 `ensure_*_schema` 没改另一个对应模型"的回归路径。

---

### D-1.5 [Medium] `ensure_user_signin_schema` 在 SQLite < 3.35 上吞 ALTER TABLE DROP COLUMN 异常但不向 logger 暴露 SQLite 版本

- 文件：`nextbot/db.py:612-634`
- 修复前行为：
  ```python
  def ensure_user_signin_schema() -> None:
      engine = get_engine()
      with engine.begin() as conn:
          rows = conn.execute(sa_text('PRAGMA table_info("user")')).fetchall()
          columns = {str(row[1]) for row in rows}
          if "signed_today" not in columns:
              return
          try:
              conn.execute(sa_text('ALTER TABLE "user" DROP COLUMN "signed_today"'))
              logger.info("已清理废弃字段 user.signed_today")
          except Exception as exc:  # noqa: BLE001
              logger.warning(
                  f"清理 user.signed_today 字段失败（可能 SQLite 版本 < 3.35），"
                  f"保留列不阻断启动: {exc}"
              )
  ```
  warning 只把异常 `str(exc)` 写出，**没有同时记录 sqlite3 模块报告的 SQLite 版本号**。运维拿到日志后无法直接判断"是 SQLite 版本太低，还是其他原因（外键约束 / 不可空 / 索引依赖）导致 DROP 失败"。
- 修复后行为：在 warning 里追加 `sqlite3.sqlite_version` 字段：
  ```python
  import sqlite3
  ...
  logger.warning(
      f"清理 user.signed_today 字段失败（SQLite 版本={sqlite3.sqlite_version}，"
      f"需要 ≥ 3.35），保留列不阻断启动: {exc}"
  )
  ```
- 触发概率：低 — 仅在 SQLite < 3.35 或 user 表存在残留 `signed_today` 列时触发。生产 OS（Ubuntu 22.04+ / Debian 12+）默认 SQLite 已 ≥ 3.37。
- 影响范围：可观测性 — 升级窗口 / 容器迁移期间排障速度。

---

### D-1.6 [Medium] `ensure_default_groups` / `ensure_default_stats` 在 `session.commit()` 失败时不显式 rollback

- 文件：`nextbot/db.py:464-506`
- 修复前行为：
  ```python
  def ensure_default_groups() -> None:
      session = get_session()
      try:
          guest = session.query(Group).filter(Group.name == "guest").first()
          if guest is None:
              session.add(Group(name="guest", ...))

          default = session.query(Group).filter(Group.name == "default").first()
          if default is None:
              session.add(Group(name="default", ...))
          session.commit()       # ← 此处抛异常不会 rollback
      finally:
          session.close()        # session.close() 内部会 implicit rollback，但语义不够明确
  ```
  虽然 `session.close()` 对未 commit 的事务隐式 rollback，但：
  1. 显式 `try/except: session.rollback()` 更能让 reader 理解"commit 失败是预期之内的退路"；
  2. BEGIN IMMEDIATE 持锁失败（5s 超时 → `OperationalError`）时，没有显式 rollback 会让该 connection 在 pool 里多停留一个 close 周期才被回收 / 重置事务状态。其他 plugin 启动期 `get_session()` 看到的 pool 可能短暂减少 1 个可用连接。
- 修复后行为：
  ```python
  def ensure_default_groups() -> None:
      session = get_session()
      try:
          ...
          try:
              session.commit()
          except Exception:
              session.rollback()
              raise
      finally:
          session.close()
  ```
- 触发概率：低 — `init_db()` 启动单线程顺序执行，理论上不会 commit 失败；只有磁盘满 / SQLite corruption / `BEGIN IMMEDIATE` 卡死 5s 才会触发。
- 影响范围：启动期可观测性 + connection pool 健康。

---

### D-1.7 [Medium] `execute_rowcount` 对 SELECT 等非 CursorResult 返回 0 而非显式报错，silent failure 路径

- 文件：`nextbot/db.py:452-461`
- 修复前行为：
  ```python
  def execute_rowcount(session: Session, stmt: Executable) -> int:
      """执行 INSERT/UPDATE/DELETE 并返回 rowcount。
      ...
      session.execute() 在类型 stub 中返回 Result[Any]，
      但实际 INSERT/UPDATE/DELETE 返回 CursorResult，这里通过 getattr
      让 pyright 接受 .rowcount 属性，同时对非 CursorResult（如 SELECT）
      回退为 0，避免崩溃。
      """
      result = session.execute(stmt)
      return int(getattr(result, "rowcount", 0))
  ```
  注释承认"对非 CursorResult 回退为 0 避免崩溃"，但 caller 通常用 `if rowcount == 0: ...` 判断"未命中行"。如果未来某次重构不小心把 UPDATE 写成 SELECT（例：`stmt = select(User).where(...)`），`execute_rowcount` 不会报错而是返回 0，调用方以为"没匹配到行"走入业务降级分支，**实际是开发者类型错误**。
- 修复后行为：把 fallback 改为显式 assert + 类型守卫：
  ```python
  from sqlalchemy.engine import CursorResult

  def execute_rowcount(session: Session, stmt: Executable) -> int:
      result = session.execute(stmt)
      if not isinstance(result, CursorResult):
          raise TypeError(
              f"execute_rowcount expects INSERT/UPDATE/DELETE, got {type(result).__name__}"
          )
      return int(result.rowcount)
  ```
  或保留 `getattr` fallback 但加 logger.warning：
  ```python
  rowcount = getattr(result, "rowcount", None)
  if rowcount is None:
      logger.warning("execute_rowcount 收到非 CursorResult，可能传入了 SELECT；返回 0")
      return 0
  return int(rowcount)
  ```
- 触发概率：低 — 现有 ~25 处 caller 全部传 UPDATE/INSERT/DELETE，但是任何重构都可能引入退化。
- 影响范围：silent failure 类的回归 — bug 表现是"代码看起来跑通了但没改 DB"。

---

### D-1.8 [Medium] SQLite 未启用 WAL 模式，readers 与 writers 互相阻塞

- 文件：`nextbot/db.py:395-413`（缺失 PRAGMA）
- 修复前行为：`_set_sqlite_pragma` 只设置了 `PRAGMA busy_timeout = 5000`，**没有** `PRAGMA journal_mode = WAL`。SQLite 默认 `journal_mode = DELETE`（rollback journal），在 rollback journal 模式下：
  1. 同一时刻只有一个 writer 或多个 reader；
  2. 任何 reader 持 SHARED lock 期间，writer 拿不到 RESERVED → EXCLUSIVE 升级，必须等所有 reader 结束；
  3. 配合 `BEGIN IMMEDIATE` 策略（每次开 session 立刻持 RESERVED），实质把全 DB 退化为完全串行；
- 修复后行为：在 connect listener 里追加：
  ```python
  cursor.execute("PRAGMA journal_mode = WAL")
  cursor.execute("PRAGMA synchronous = NORMAL")  # WAL + NORMAL 是常见组合
  ```
  开启 WAL 后：
  - reader 永不阻塞 writer，writer 也不阻塞 reader；
  - 单 writer 仍然是串行的（这是 SQLite 设计）；
  - 配合 BEGIN IMMEDIATE 仍然能消除写-写 race，但读路径不再被写路径阻塞。
- 触发概率：高 — 任何 `查询 仓库` / `查询 user` / `排行榜` 命令在并发期都会触发 reader 与 writer 冲突。在大群活动期间体感为"查询响应变慢"。
- 影响范围：全体 read-heavy 命令（leaderboard、查询、user_info 截图等）的尾延迟，特别是同一时刻多人 gift / claim 时；磁盘写入模式（WAL 写 `app.db-wal` 副边文件，需要 checkpoint 策略）。

---

### D-1.9 [Low] `_engine` / `_session_factory` global 缺并发初始化保护

- 文件：`nextbot/db.py:375-416`
- 修复前行为：
  ```python
  _engine: Engine | None = None
  _session_factory: sessionmaker[Session] | None = None


  def _ensure_engine_and_factory() -> tuple[Engine, sessionmaker[Session]]:
      global _engine, _session_factory
      if _engine is None or _session_factory is None:
          _engine = create_engine(...)
          ...
          _session_factory = sessionmaker(...)
      return _engine, _session_factory
  ```
  两个变量分两步赋值（line 382 `_engine = create_engine(...)` → line 415 `_session_factory = sessionmaker(...)`）。在多线程场景（NoneBot 的 FastAPI 同步 endpoint 走 threadpool）下，线程 A 进入到 `_engine = ...` 完成但 `_session_factory` 还未赋值时，线程 B 进入 `if _engine is None or _session_factory is None` 判定为 True（因为 `_session_factory is None`），又走一次 `create_engine`，**第二个 engine 不会被使用但会覆盖第一个**，第一个 engine 的 connect listener 永远绑定在已被替换的 engine 上 → 那些 listener 永远不再触发新的 session 流。
- 修复后行为：用 `threading.Lock` 包裹整个判定 + 初始化：
  ```python
  _engine_lock = threading.Lock()

  def _ensure_engine_and_factory() -> tuple[Engine, sessionmaker[Session]]:
      global _engine, _session_factory
      if _engine is not None and _session_factory is not None:
          return _engine, _session_factory
      with _engine_lock:
          if _engine is None or _session_factory is None:
              _engine = create_engine(...)
              ...
              _session_factory = sessionmaker(...)
      return _engine, _session_factory
  ```
- 触发概率：低 — `init_db()` 在 `on_startup` 钩子里单线程先调用一次，之后 `_engine` 和 `_session_factory` 都不再是 None。但**WebUI / FastAPI 同步 endpoint** 通过 threadpool 提前并发触发的极端场景仍存在（特别是冷启动期间）。
- 影响范围：冷启动期间的极少数请求；listener 失活后 BEGIN IMMEDIATE 也会失效（关联 D-1.1 风险）。

---

### D-1.10 [Low] `Base.metadata.create_all(engine)` 与手写 DDL（raw `sqlite3.connect()` 内的 `CREATE TABLE IF NOT EXISTS`）双源，未来 schema 漂移风险

- 文件：`nextbot/db.py:426`（create_all） vs `nextbot/db.py:637-656, 894-931`（raw `CREATE TABLE IF NOT EXISTS`）
- 修复前行为：
  - `init_db()` line 426 `Base.metadata.create_all(engine)` 创建 `Base` 子类对应的所有表（含 `red_packet`、`red_packet_claim`、`user_sign_record`）；
  - 同时 `ensure_sign_record_schema()`（line 637）、`ensure_red_packet_schema()`（line 894）又用 raw `sqlite3.connect()` 执行 `CREATE TABLE IF NOT EXISTS` 维护一份**字段定义**（line 645-651 sign_record / line 902-928 red_packet 与 claim）。两份 schema 字段必须**手工同步**：
    ```python
    # ORM 定义（209-215）
    class UserSignRecord(Base):
        ...
        streak: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
        created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=db_now_utc_naive)

    # raw DDL（645-651）
    """CREATE TABLE IF NOT EXISTS "user_sign_record" (
        "id" INTEGER PRIMARY KEY AUTOINCREMENT,
        "user_id" TEXT NOT NULL,
        "sign_date" TEXT NOT NULL,
        "streak" INTEGER NOT NULL DEFAULT 1,
        "created_at" DATETIME NOT NULL
    )"""
    ```
  - 风险：未来给 ORM 加字段忘了同步 raw DDL → 在没有 `app.db` 文件的全新部署上由 `create_all` 创建（带新字段），在有 `app.db` 的老库上由 raw DDL CREATE IF NOT EXISTS 无操作（已存在）→ 老库实际跑的是无新字段的表，新代码访问该字段抛 `OperationalError: no such column`。
- 修复后行为：删除 raw DDL `CREATE TABLE`，统一让 `Base.metadata.create_all()` 负责"建表"，让 `ensure_*_schema` 只负责"加列 / 加索引"（ALTER），并在文档 docstring 里明确分工。
- 触发概率：低（需要未来开发者忘记同步两份 DDL）。
- 影响范围：未来重构事故路径。

---

### D-1.11 [Low] `warehouse_lock` dict 无限增长，长期运行内存泄漏

- 文件：`nextbot/warehouse_lock.py:9, 12-17`
- 修复前行为：
  ```python
  _WAREHOUSE_LOCKS: dict[str, asyncio.Lock] = {}

  def warehouse_lock(user_id: str) -> asyncio.Lock:
      lock = _WAREHOUSE_LOCKS.get(user_id)
      if lock is None:
          lock = asyncio.Lock()
          _WAREHOUSE_LOCKS[user_id] = lock
      return lock
  ```
  每个有过仓库操作的用户都在 dict 里留下一个 Lock 对象。新用户多了不删旧 entry，长期跑（半年 / 一年）会累积数千到数万 entry。每个 `asyncio.Lock` 本身 ~200B，10000 entry ~2MB —— 不算大，但属于"只增不减"的资源。
- 修复后行为：选 1：
  1. 改 `weakref.WeakValueDictionary` —— 当没有 coroutine 在持锁时自动回收 Lock；但 `asyncio.Lock` 必须有强引用否则 acquire 完释放即被 GC，等下次 acquire 又是新 lock。所以单纯 weakref 不行；
  2. 加 LRU 上限（例 10000）+ 仅在 lock 未被持有时淘汰；
  3. 接受当前 "leak"，每个用户最多一个 lock 入口 —— 视为可控。
- 触发概率：低 — 7×24 长跑 + 用户量大才显著。
- 影响范围：内存占用 / 极端情况下的 RSS 持续上涨告警。

---

### D-1.12 [Low] `warehouse_lock(user_id)` 在没有 running event loop 时构造 Lock 在 Python 3.9 / 3.10 边界版本可能异常

- 文件：`nextbot/warehouse_lock.py:13-17`
- 修复前行为：`asyncio.Lock()` 在 Python 3.10 之前会捕获 `asyncio.get_event_loop()` 的返回值。如果 `warehouse_lock("u1")` 在没有 running loop 的同步上下文（例如 module-level import 阶段、test fixture 同步分支）首次调用，3.9 会拿到 deprecation warning + 默认 loop，3.10+ 会 lazy 绑定。当前代码假定 3.10+ 才安全。
- 修复后行为：在 docstring 里写明"调用方必须在 event loop 内调用"，或加 `assert asyncio.get_event_loop_policy().get_event_loop().is_running()`，或对 `asyncio.Lock()` 包一层 lazy 工厂。
- 触发概率：低 — `pyproject.toml` 需要 ≥ 3.11（待确认），无问题；若降到 3.9 则触发。
- 影响范围：跨 Python 版本可移植性。

---

### D-1.13 [Low] `screenshot_temp.temp_screenshot_path` 硬编码 `/tmp`，不可移植 + 不验证 prefix 内容

- 文件：`nextbot/screenshot_temp.py:30-33`
- 修复前行为：
  ```python
  path = (
      Path("/tmp")
      / f"{prefix}-{beijing_filename_timestamp()}-{uuid.uuid4().hex[:8]}{suffix}"
  )
  ```
  问题 1：`/tmp` 在 Windows 不存在（Win 上为 `C:\Users\<name>\AppData\Local\Temp` 或 `tempfile.gettempdir()`）。`/tmp` 是 Linux/macOS 假设，未来若在 Windows 上做 dev 会失败。
  问题 2：`prefix` 未做字符过滤。若 `prefix` 含 `/` 或 `..`，Python `Path / str` 会跟随分隔符。例：`prefix = "../etc/passwd"` → `path = Path("/etc/passwd-2024...-abcd1234.png")`。当前所有 caller 都使用受信任的字面量或纯数字 ID（server.id / pool_id / user_id 是 QQ 号），**没有实际 traversal 风险**，但是 forward-compat 隐患 —— 一旦未来引入 `file_prefix=f"user-{user.name}"`（player_name 来自 chat 输入），就有 traversal CVE 风险（写入 / 删除任意路径文件）。
- 修复后行为：
  ```python
  import re
  import tempfile

  _PREFIX_SAFE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

  @contextlib.asynccontextmanager
  async def temp_screenshot_path(prefix: str, *, suffix: str = ".png") -> AsyncIterator[Path]:
      if not _PREFIX_SAFE_RE.fullmatch(prefix):
          raise ValueError(f"unsafe prefix: {prefix!r}")
      tmp_root = Path(tempfile.gettempdir())
      path = tmp_root / f"{prefix}-{beijing_filename_timestamp()}-{uuid.uuid4().hex[:8]}{suffix}"
      try:
          yield path
      finally:
          with contextlib.suppress(OSError):
              path.unlink(missing_ok=True)
  ```
- 触发概率：低 — 当前所有 caller 都 trusted；只有未来扩展时才命中。
- 影响范围：可移植性（Windows dev）、forward-compat 安全护栏。

---

### D-1.14 [Info] `RedPacket.status` / `RedPacket.sender_user_id` 无索引，未来扩展时排行 / 列表慢

- 文件：`nextbot/db.py:228-243`（模型）+ `nextbot/plugins/red_packet.py:85, 609, 619, 710, 720`（查询）
- 修复前行为：模型未声明 `index=True`，`ensure_red_packet_schema()` 也没有 `CREATE INDEX IF NOT EXISTS`。当前查询 `RedPacket.status == "active"` + `RedPacket.sender_user_id == user_id` 在 red_packet 表行数小（<1000）时全表扫描可接受。
- 修复后行为：在 `ensure_red_packet_schema` 或新增 `ensure_red_packet_indexes_schema()` 加：
  ```sql
  CREATE INDEX IF NOT EXISTS "ix_red_packet_status" ON "red_packet" ("status");
  CREATE INDEX IF NOT EXISTS "ix_red_packet_sender" ON "red_packet" ("sender_user_id");
  ```
- 触发概率：极低 — 取决于业务红包 lifetime 累计行数。
- 影响范围：未来扩展性。

---

### D-1.15 [Info] `ensure_lottery_schema` 是 no-op，与其他 ensure_*_schema 风格不一致

- 文件：`nextbot/db.py:565-573`
- 修复前行为：
  ```python
  def ensure_lottery_schema() -> None:
      """No-op forward-compat hook for lottery schema migrations.

      Tables themselves are created by ``Base.metadata.create_all``; this
      placeholder exists so future column additions can ALTER without dropping
      data. Currently no migration is needed, so we deliberately do nothing
      (avoid opening a sqlite3 connection that does no work).
      """
      return
  ```
  这本身**正确**（避免空 connection），但**和其他 ensure_*_schema 行为不对称**：未来若给 lottery 加字段，开发者会照着 `ensure_shop_schema` / `ensure_warehouse_schema` 复制粘贴 raw sqlite3 模式（参考 D-1.3 / D-1.10），引入新的不一致路径。
- 修复后行为：要么删掉这个 no-op（在 `init_db()` 里也删除调用），要么在 docstring 里加一行 "future ALTERs SHOULD use engine.begin() + sa_text, not raw sqlite3.connect" 的指引。
- 触发概率：N/A — 不是 bug，是 forward-compat 风格指引。
- 影响范围：未来重构一致性。

---

## 结论

- Critical：**0**
- High：**3**（D-1.1 isolation_level 缺失 / D-1.2 持锁 await / D-1.3 raw sqlite3 旁路 busy_timeout）
- Medium：**5**（D-1.4 init_db 重复 PRAGMA / D-1.5 SQLite 版本可观测性 / D-1.6 ensure_default_* 缺显式 rollback / D-1.7 execute_rowcount silent failure / D-1.8 缺 WAL 模式）
- Low：**5**（D-1.9 engine init 多线程 race / D-1.10 双 DDL 源 / D-1.11 warehouse_lock dict 无限增长 / D-1.12 跨 Python 版本 Lock 兼容 / D-1.13 screenshot_temp 硬编码 + prefix 不校验）
- Info：**2**（D-1.14 red_packet 无索引 / D-1.15 ensure_lottery_schema no-op 风格不一致）
- 无问题的文件：无（三个文件均有发现，但 `warehouse_lock.py` 仅 Low 级别 forward-compat 隐患，`screenshot_temp.py` 仅 Low 级别）

**优先级建议（主代理二次审核时排序参考）**：
1. D-1.1（isolation_level = None）—— 仅 1 行修复，但直接决定整套 BEGIN IMMEDIATE 策略是否真实生效，反推前 4 轮 plugin 审计的有效性；
2. D-1.8（WAL 模式）—— 仅 1 行修复，吞吐立竿见影；
3. D-1.2（持锁 await）—— 修复面大但收益高，需要 plugin 层配合；
4. D-1.3 / D-1.4 —— 启动期 schema 路径统一化，预防未来 schema migrate 事故；
5. 其他 Medium / Low 视维护节奏择期。
