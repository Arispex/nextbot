# Research: Bucket E — scripts + Docker Audit

- **Query**: 审计 scripts/ + Dockerfile + docker-compose.yml，覆盖 security / performance / UX / copy
- **Scope**: internal
- **Date**: 2026-05-15
- **Files audited**:
  - `scripts/migrate_add_user_coins.py`
  - `scripts/package_release.py`
  - `Dockerfile`
  - `docker-compose.yml`
- **Reference assets (read for context, not audited)**:
  - `.dockerignore`
  - `.gitignore`

---

## Severity legend

- **P0** — High impact, must fix before next release (data loss / secret leak / breakage)
- **P1** — Should fix this audit cycle (privilege, leak, perf regression, broken UX)
- **P2** — Nice-to-have / polish

---

## Findings

### F1 [P0 / Security] Dockerfile 以 root 用户运行整个进程

- **File**: `Dockerfile:21-47`
- **Issue**: 整个运行时阶段没有 `USER` 指令；容器以 `root` 启动 `python bot.py`，包含 webui、外部网络监听 (`EXPOSE 18081`)、Playwright Chromium 子进程。Chromium 在 root 下还需要传 `--no-sandbox`，攻击面进一步放大；任何 webui RCE / template injection / Playwright crash → 容器 root → 宿主 bind-mount `./data` 的文件 owner 全部变 root，宿主侧脚本无法清理。
- **Fix sketch**:
  1. Stage 2 末尾 `RUN groupadd -r nextbot && useradd -r -g nextbot -d /app -s /sbin/nologin nextbot`，`chown -R nextbot:nextbot /app /app/.venv /app/data`，再 `USER nextbot`。
  2. `playwright install --with-deps` 需 root → 必须在切换 USER **之前**完成，且 chromium 的 cache 目录 (`/ms-playwright` 或 `~/.cache/ms-playwright`) 也要 chown 给 nextbot。
  3. compose 增加 `read_only: true` + `tmpfs: /tmp`（可选 P1）。

---

### F2 [P0 / Security] migration 无事务保护 / 无 dry-run / 无 backup 提示

- **File**: `scripts/migrate_add_user_coins.py:21-32`
- **Issue**:
  - `sqlite3.connect()` 在 Python sqlite3 默认 `isolation_level=""` 下，DDL (`ALTER TABLE`) 会触发自动 commit，**`conn.commit()` 是 no-op**；同时该脚本完全没有 `BEGIN ... ROLLBACK` 包裹，没有任何"出错回滚"路径。
  - 没有 `--dry-run`，没有 backup 提示，没有 `PRAGMA quick_check / integrity_check` 预校验。
  - 没有日志/审计：执行了什么、何时、对哪个 DB 文件无记录（合规风险，spec 要求加日志）。
  - 没有运行前确认 schema 版本，未来若 schema 已用 ORM 自带 migration 工具改过，将出现重复列冲突或 schema drift。
- **Fix sketch**:
  1. 加 `--dry-run` flag，仅打印将执行的 SQL。
  2. 用 `with conn:` + `conn.execute("BEGIN")` 包裹；出错 raise 后由 contextmanager rollback；最终 commit 在 `with` 退出时自动完成。
  3. 在 `ALTER TABLE` 前先 `print("backup recommended: cp app.db app.db.bak-<ts>")` 并 exit 非零如果用户没传 `--i-have-a-backup` flag。
  4. 套用项目统一 logger（global CLAUDE.md 强制要求），输出 `[INFO] 添加列 user.coins 成功，db=<path>` 而非裸 print。

---

### F3 [P1 / Security] migration 默认硬编码 `app.db` 路径，不接受参数 → 易在错误环境执行

- **File**: `scripts/migrate_add_user_coins.py:6-7`、`16-19`
- **Issue**: `DB_PATH = BASE_DIR / "app.db"`。容器内 `NEXTBOT_DATA_DIR=/app/data`，真实 DB 在 `/app/data/app.db`；脚本 hardcode 到 `/app/app.db`，进入容器执行会 silently 输出 `database not found` 然后 return 0。运维以为"已跳过"，实际并未跑到目标 DB。
- **Fix sketch**:
  - 接受 `--db PATH` 参数，缺省读 env `NEXTBOT_DATA_DIR/app.db`（与 `server/utils/paths.py` 解析逻辑保持一致）。
  - "database not found" 应 exit 非零（见 F12）。

---

### F4 [P1 / Security] `package_release.py` 用 `git ls-files -co --exclude-standard` 仍可能打包敏感运行时文件

- **File**: `scripts/package_release.py:11-31`
- **Issue**: `-co` = cached + others；only `--exclude-standard` 排除 `.gitignore` 命中的。问题：
  1. 仓库 `.gitignore` 排除了 `app.db / .webui_auth.json / .env`，所以这些**不会进 zip** —— OK。
  2. 但 `__pycache__/` 也被 gitignore，所以**也不会进 zip** —— OK。
  3. **真正的风险点**：`-o`（others）包含所有未被 ignore 的 untracked 文件。如果开发者本地有 `secrets.json`、`.env.staging`、`*.pem`、`credentials.json` 等且未加到任何 ignore，**会被打包进 release**。
  4. 当前实现没有"显示要打包的清单 → 用户确认 → 实际打包"的二阶段流程，也没有针对常见 secret 文件名/后缀（`*.pem *.key *.p12 .env*`）的硬阻塞。
- **Fix sketch**:
  - 增加 `--list` flag，先打印将打包的文件列表，要求 `--confirm` 才真正打包。
  - 维护内置 deny-pattern（`*.pem`, `*.key`, `*.p12`, `.env`, `.env.*`, `id_rsa*`, `*credentials*`, `*secret*`），命中即 exit 非零并打印命中文件。
  - 推荐改用 `git archive HEAD --format=zip` 只打包 tracked + committed 文件，最安全；当前实现保留 untracked 是为了打包未提交的修改，但需要权衡安全性。

---

### F5 [P1 / Security] Dockerfile `COPY . .` 在 .dockerignore 下虽已排除 `.env / app.db / .webui_auth.json`，但仍打包了 docs/notes/dev fixtures

- **File**: `Dockerfile:39`
- **Issue**: 当前 `.dockerignore` 排除了运行时 secret/DB 文件（好），但仍允许 `tests/`、`docs/`、`AGENTS.md`、`CLAUDE.md`、`pyproject.toml`、`uv.lock`、各类 fixture、内部脚本（包括 `scripts/migrate_add_user_coins.py` 全文）进入镜像。其中：
  - 测试代码 / 内部文档不应在生产镜像里增加攻击面 + image size。
  - `pyproject.toml` / `uv.lock` 已经被 builder stage 复用，runtime stage 不需要。
- **Fix sketch**: 在 `.dockerignore` 增加：

  ```
  tests/
  docs/
  AGENTS.md
  CLAUDE.md
  *.md
  ```

  并把 README/LICENSE 显式从忽略中放回（如果想要随镜像分发）。

---

### F6 [P1 / Security] Dockerfile 没有 HEALTHCHECK；compose 也没有

- **File**: `Dockerfile`（全文）、`docker-compose.yml`（全文）
- **Issue**: 缺 HEALTHCHECK → restart 策略形同虚设；容器即便已陷入 webui hang / websocket 重连风暴，也不会被自愈拉起。napcat 同样无 healthcheck，导致 `depends_on` 只能保证启动顺序而非就绪顺序。
- **Fix sketch**:
  - Dockerfile：`HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:18081/healthz',timeout=3).status==200 else 1)"`（前提：webui 暴露 `/healthz`，目前需确认；若无，先实现 health 端点）。
  - compose：用 `depends_on: napcat: condition: service_healthy` 替代当前裸 `depends_on`。

---

### F7 [P1 / Security] docker-compose 将 napcat 端口 6099/3001 默认绑定到 0.0.0.0

- **File**: `docker-compose.yml:39-41`
- **Issue**: `ports: ["6099:6099", "3001:3001"]` 默认绑 `0.0.0.0`。6099 是 NapCat WebUI（带二维码登录、token 交互），3001 是 OneBot WS（带 access_token 鉴权）。如果宿主直接挂公网，等于把 QQ 登录后台 + WS 鉴权端口暴露到全网。
- **Fix sketch**:
  - 在注释里和默认值都改成 `127.0.0.1:6099:6099`、`127.0.0.1:3001:3001`，要求用户显式把宿主放公网时再去掉绑定。
  - 同理 nextbot 的 `18081:18081`（`docker-compose.yml:27`）也应该提示"线上请走 nginx/cloudflare，不要直接暴露"。
  - 文档 `docker-compose.yml:10-13` 已经让用户填 `WEB_SERVER_PUBLIC_BASE_URL=http://<host>:18081`，但没提醒最小化暴露范围。

---

### F8 [P1 / Security] `data/` bind-mount 权限隐患

- **File**: `docker-compose.yml:29`、`Dockerfile:42-43`
- **Issue**: `./data:/app/data`，容器 root 写入后，宿主 `./data/.webui_auth.json / app.db / .env` 文件 owner 是 root。运维如果不用 sudo 就改不动；备份/复制工具默认也无法读。Auth 文件 `.webui_auth.json` 没有显式 chmod 600，容器 umask 默认 0022 → world-readable。
- **Fix sketch**:
  - F1 切到非 root user 后，bind-mount 的 owner 是固定 UID；推荐 docker-compose 加 `user: "1000:1000"`（或 build-arg 指定）。
  - 写 auth file 的逻辑（不在本 bucket，scope-out backlog → server/utils/paths）应显式 `os.chmod(path, 0o600)`。

---

### F9 [P1 / Performance] Dockerfile 多阶段没有把 Playwright chromium 放到 builder

- **File**: `Dockerfile:33-34`
- **Issue**: `playwright install --with-deps chromium` 是 stage 2 才做的，意味着每次 `COPY . .` 后只要源码变化，**虽然不会重装 chromium**（它在更上一层），但因为 `playwright install` 紧贴在 `COPY --from=builder /app/.venv` 之后，apt 列表 cleanup 在同一 RUN，本身合理。但是它和 stage 1 没有共享 cache —— builder stage 完全没装 chromium，导致 stage 2 没法享受多阶段隔离的好处：runtime 镜像里既有 chromium 又有 apt 安装的依赖库，体积仍然非常大。
- **Sub-issue**: `playwright install --with-deps` 执行 `apt-get update && apt-get install` 但**没有 `apt-get clean`**，仅 `rm -rf /var/lib/apt/lists/*`，对应的 `/var/cache/apt/archives/*.deb` 可能仍然占据空间（取决于 playwright 内部实现，需 verify）。
- **Fix sketch**:
  - 短期：在同一 RUN 加 `&& apt-get clean && rm -rf /var/cache/apt/archives/*`.
  - 长期：考虑 builder 里也安装 chromium，runtime 用 `COPY --from=builder /ms-playwright /ms-playwright`，可能省 100MB+；但 chromium 二进制依赖系统库，需保留 apt 的 deps，复杂度提升，可作为 P2 backlog。

---

### F10 [P1 / Performance] Dockerfile builder 没用 BuildKit cache mount

- **File**: `Dockerfile:17`
- **Issue**: `RUN uv sync --frozen --no-dev --no-install-project` 没用 `--mount=type=cache,target=/root/.cache/uv`。每次 `uv.lock` 变化都会重新从 PyPI 拉所有 wheel，CI/本地构建慢。
- **Fix sketch**：

  ```dockerfile
  RUN --mount=type=cache,target=/root/.cache/uv \
      uv sync --frozen --no-dev --no-install-project
  ```

  顶部 `# syntax=docker/dockerfile:1.7` 已经支持。

---

### F11 [P1 / Performance] migration 不兼容 SQLite WAL 模式下的 long-running readers

- **File**: `scripts/migrate_add_user_coins.py:21-32`
- **Issue**: `ALTER TABLE ... ADD COLUMN` 在 SQLite 是 schema-mutating，**默认会获取 `EXCLUSIVE` lock**。若 webui / bot 还在运行（compose 容器没停），WAL reader 也会被阻塞或 migration 抛 `database is locked`。脚本没有：
  - 优先用 `PRAGMA busy_timeout = 30000` 给一个等待窗口。
  - 明确提示用户先停服务。
- **Fix sketch**:
  - 加 `conn.execute("PRAGMA busy_timeout = 30000")`。
  - 输出/README 强制要求先 `docker compose stop nextbot`。

---

### F12 [P1 / UX] migration & package script 退出码不区分成功 / skip / 错误

- **File**:
  - `scripts/migrate_add_user_coins.py:17-19`、`24-26`
  - `scripts/package_release.py:67-69`
- **Issue**:
  - migration `database not found` 与 "skip: 已存在" 都 `return`（exit 0）；CI/编排脚本无法区分"我应该报错"和"幂等跳过"。
  - package_release 任何错误（git 没装、ls-files 失败）都会 raise，但**没有 try/except**，python traceback 直接糊脸；正常完成时只有 print，没有 exit code 语义。
- **Fix sketch**:
  - migration：not-found → `sys.exit(2)`；skip → `sys.exit(0)` + 明确 `[INFO] skip`；ALTER 成功 → `sys.exit(0)`；异常 → `sys.exit(1)`。
  - package_release：包装 `try / except subprocess.CalledProcessError, FileNotFoundError, OSError`，输出友好错误并 `sys.exit(1)`。

---

### F13 [P2 / UX] migration 输出无时间戳、无 db 路径、无文件大小

- **File**: `scripts/migrate_add_user_coins.py:18`、`25`、`30`
- **Issue**: print 文案是裸字符串，缺时间戳、缺 db 绝对路径、缺修改前后行数。运维事后排障无线索。
- **Fix sketch**:
  - 套统一 logger（CLAUDE.md 要求），输出形如 `2026-05-15T10:23:45.123+08:00 [INFO] 添加列 user.coins 成功，db=/app/data/app.db，user 行数=1234`。
  - 在 commit 前后分别 `SELECT count(*) FROM user`，输出对比，方便确认 DDL 没误伤。

---

### F14 [P2 / UX] package_release 没有进度提示

- **File**: `scripts/package_release.py:38-43`
- **Issue**: 当文件数大时（含 node_modules-style 资源、模型文件、static），zip 阶段静默。用户看不到进度，无法判断是 hang 还是正常执行。
- **Fix sketch**:
  - 每写入 100 个文件 print 一次 `[INFO] zipped <N>/<total>`；或在 verbose 模式下打每个文件。

---

### F15 [P2 / UX] package_release 缺少打包后 size 信息

- **File**: `scripts/package_release.py:68-69`
- **Issue**: 完成提示只有路径和文件数，没有 zip 体积。判断"是否漏打 / 多打"无依据。
- **Fix sketch**:
  - 增加 `output_path.stat().st_size` → 人类可读（KB/MB）打印。

---

### F16 [P2 / Copy] 文案语气与项目风格不统一（CLAUDE.md：中英文混排留空格 + 动作 + 结果）

- **Files & lines**:
  - `scripts/migrate_add_user_coins.py:18` — `f"database not found: {db_path}"` 全英文且无动作主体，建议改成 `数据库不存在: {db_path}` 或 `[ERROR] 找不到数据库文件: {db_path}`。
  - `scripts/migrate_add_user_coins.py:25` — `"skip: column user.coins already exists"` 改 `[INFO] 跳过：列 user.coins 已存在`。
  - `scripts/migrate_add_user_coins.py:30` — `"done: added column user.coins INTEGER NOT NULL DEFAULT 0"` 改 `[INFO] 添加列 user.coins 成功`（不在用户面板，是脚本日志，按"日志规则"非 toast）。
  - `scripts/package_release.py:47` — `"打包当前项目为 release zip（排除 ignore 文件）"` 中英文括号风格不一致；建议 `打包当前项目为 release zip (排除 ignore 文件)` 或全用中文括号统一；优先全中文。
  - `scripts/package_release.py:50` — `"输出 zip 路径，默认在项目根目录自动生成"` 与 line 47 风格 OK，但同上括号风格如要统一可对齐。
  - `scripts/package_release.py:68-69` — `打包完成：{output_path}` / `文件数量：{file_count}` 风格正常，但建议补 `大小：xx MB`（F15）。
- **Issue**: CLAUDE.md 全局要求中英文之间留空格 + 中文标点使用中文全角；上述文案部分混用半角冒号 (`:`)，中文环境应统一改成 `：`，且 `database not found` 这种纯英文输出在面向中国维护者的项目里不一致。

---

### F17 [P2 / Security] Docker base image 仅 pin minor `python:3.11-slim-bookworm`，未 pin digest

- **File**: `Dockerfile:4`、`Dockerfile:21`
- **Issue**: `python:3.11-slim-bookworm` 是 movable tag；`uv:0.5.4` 是版本号 tag（更稳但仍非 digest）。重新 build 在不同时间可能拿到不同二进制 → 可重现性差，且若上游被入侵不会立刻察觉。
- **Fix sketch**:
  - `FROM python:3.11-slim-bookworm@sha256:<digest> AS builder` / runtime；docs 写"如何更新 digest"。
  - 同理 `ghcr.io/astral-sh/uv:0.5.4@sha256:<digest>`。

---

### F18 [P2 / Security] compose 把 `mlikiowa/napcat-docker:latest` 直接用 `latest` 标签

- **File**: `docker-compose.yml:36`
- **Issue**: `image: mlikiowa/napcat-docker:latest`。该镜像不在自己控制下，每次 `docker compose pull` 都可能跳到任意新版本；NapCat 升级历史上有过破坏性变化。`nextbot` 自己的镜像 `ghcr.io/arispex/nextbot:latest` 同问题（line 21）。
- **Fix sketch**:
  - 改用具体 tag `mlikiowa/napcat-docker:v3.x.y` 并在 README 标注"经过本项目测试通过"。
  - nextbot 镜像通过 release workflow 打具体版本，compose 默认 pin 到当前发布版（如 `v0.5.0`），`latest` 作为可选 override。

---

### F19 [P2 / Security] compose 缺 `restart: on-failure` 的最大重启次数 / 错误处理

- **File**: `docker-compose.yml:23`、`38`
- **Issue**: `restart: unless-stopped` 不限重启次数；如果应用进入"启动即崩溃"循环（如配置错误），会以最快速度无限重启，刷爆日志和 CPU。
- **Fix sketch**:
  - 用 swarm-style `deploy.restart_policy.max_attempts` 或考虑在 entrypoint 加 backoff；compose `restart: on-failure:5` 也可。

---

### F20 [P2 / Performance] Dockerfile 没显式 `--no-install-recommends`

- **File**: `Dockerfile:33`
- **Issue**: `playwright install --with-deps` 内部 apt-get 是否带 `--no-install-recommends` 由 playwright 决定（多数版本会带），但本 Dockerfile 自身没有任何额外 apt 操作可以做这件事，仅依赖上游。无直接 fix，但若未来要加 apt 包，需要严格 `--no-install-recommends && rm -rf /var/lib/apt/lists/*`。
- **Fix sketch**: 加注释作为后续维护者 guard。

---

### F21 [P2 / Security] Dockerfile 没显式 `WORKDIR` 持久化策略 / `VOLUME` 与 bind-mount 冲突

- **File**: `Dockerfile:43`、`docker-compose.yml:29`
- **Issue**: `VOLUME ["/app/data"]` 与 compose 的 `./data:/app/data` 同时存在。bind-mount 会覆盖 VOLUME 声明，没有功能性问题；但若用户**不通过 compose** 单独 `docker run` 不带 `-v`，会创建一个匿名 volume，导致 DB 落在不可控位置，下一次 run 又是空的，容易丢数据。
- **Fix sketch**:
  - 去掉 Dockerfile 里的 `VOLUME`，强制运行者必须显式挂载（fail-fast）。
  - 或在 entrypoint 启动前 `test -w /app/data || (echo "[ERROR] /app/data 不可写，请挂载持久化卷"; exit 1)`。

---

### F22 [P2 / UX] migration 没有 schema_migrations 表的概念

- **File**: `scripts/migrate_add_user_coins.py` (整文件)
- **Issue**: 单次性脚本，无版本表跟踪。下次再要加列时，又会写一份类似 `migrate_add_user_xxx.py`，仍然没有顺序保障。
- **Fix sketch**:
  - 长期 backlog：引入 alembic / yoyo-migrations / 自写 schema_migrations 表，记录 `(version, applied_at, sha)`。本次先记入 scope-out backlog。

---

## Scope-out backlog（不在本 bucket，需上游再开 task）

- 引入项目级 migration 框架（F22 配套）。
- webui `/healthz` 端点实现（F6 前置）。
- `server/utils/paths.py` 写 auth file 时 `chmod 600`（F8 子项）。
- 镜像发布流水线：`ghcr.io/arispex/nextbot` 加 SBOM / cosign 签名（与 F17/F18 同源问题）。
- README / `docker-compose.yml` 注释统一改成"线上请用 nginx + TLS，不要直接暴露 18081/6099/3001"。

---

## Summary

- **Total findings**: 22
- **P0**: 2 (F1 容器以 root 运行；F2 migration 无事务/无回滚/无 dry-run)
- **P1**: 9 (F3–F11)
- **P2**: 11 (F12–F22)

### Top 3 must-fix

1. **F1** — Dockerfile 用非 root user 运行 (`Dockerfile:21-47`)。否则任何 webui / Playwright 入侵直接拿到 root + 宿主 bind-mount 数据所有权。
2. **F2** — `scripts/migrate_add_user_coins.py` 加 `--dry-run` + 事务包裹 + 备份提示 + 统一 logger + 区分退出码。当前是生产 DB schema 工具，缺乏任何防呆。
3. **F7** — `docker-compose.yml` napcat 6099/3001 默认仅绑 `127.0.0.1`。当前 0.0.0.0 暴露会把 QQ 登录后台 + WS 鉴权端口直接放到公网。
