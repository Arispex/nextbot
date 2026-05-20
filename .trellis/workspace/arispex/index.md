# Workspace Index - arispex

> Journal tracking for AI development sessions.

---

## Current Status

<!-- @@@auto:current-status -->
- **Active File**: `journal-4.md`
- **Total Sessions**: 202
- **Last Active**: 2026-05-20
<!-- @@@/auto:current-status -->

---

## Active Documents

<!-- @@@auto:active-documents -->
| File | Lines | Status |
|------|-------|--------|
| `journal-4.md` | ~1593 | Active |
| `journal-3.md` | ~1984 | Archived |
| `journal-2.md` | ~1973 | Archived |
| `journal-1.md` | ~1999 | Archived |
<!-- @@@/auto:active-documents -->

---

## Session History

<!-- @@@auto:session-history -->
| # | Date | Title | Commits | Branch |
|---|------|-------|---------|--------|
| 202 | 2026-05-20 | 掷骰子/猜数字冷却时间显示支持分钟与小时 | `47c56f9` | `main` |
| 201 | 2026-05-20 | 签到新增要求在线开关 | `37730d7` | `main` |
| 200 | 2026-05-19 | feat: WebUI 改名拎出独立 dialog/endpoint | `319c77c` | `main` |
| 199 | 2026-05-19 | refactor: 删除 WebUI ban/unban/delete 的 owner 保护 | `6dfcc92` | `main` |
| 198 | 2026-05-19 | feat: WebUI 用户修改密码功能 | `06bf025` | `main` |
| 197 | 2026-05-19 | feat: sync API users[] 加 ban_reason 字段 | `d5adb0d` | `main` |
| 196 | 2026-05-19 | feat: WebUI 创建用户加密码字段 + TShock create push | `8825841`, `d3b1fa2` | `main` |
| 195 | 2026-05-19 | audit + fixes: 注册账号 + sync API 复审（修 5/15 项） | `00dc194` | `main` |
| 194 | 2026-05-19 | feat: WebUI sync snapshot API (ETag-based pull sync) | `d015756` | `main` |
| 193 | 2026-05-19 | feat: 注册账号自动创建 TShock 账号 + 旧用户 hash 迁移 | `1aa4bff` | `main` |
| 192 | 2026-05-18 | fix: WebUI 创建用户补 push + 修复 _sync_user_whitelist 错误端点 | `62af1f9` | `main` |
| 191 | 2026-05-18 | fix: WebUI 删除/改名用户补 server 白名单 push | `46a3692` | `main` |
| 190 | 2026-05-17 | refactor: 删除 我的背包/用户背包 的 send_link 参数 | `66fc472` | `main` |
| 189 | 2026-05-17 | fix: lottery_result header 奖池 ID 去 # prefix | `8805ed0` | `main` |
| 188 | 2026-05-17 | feat: red_packet_own header 加 owner bar (avatar + name + QQ) | `a2f224a` | `main` |
| 187 | 2026-05-17 | fix: red_packet_all 标题改红包列表 + 每条加抢红包引导 | `fc6ffaa` | `main` |
| 186 | 2026-05-17 | fix: rob header 删「→ 目标」元信息 | `86362ca` | `main` |
| 185 | 2026-05-17 | fix: guess_number header 删 范围 / 投入 元信息 | `fd91d35` | `main` |
| 184 | 2026-05-17 | fix: dice header 去重「投入」+「你的选择」并入 5-stat-tile 网格 | `bf4a97d` | `main` |
| 183 | 2026-05-17 | refactor: 4 个图片模板 header 统一玩家 avatar bar | `c52d97d` | `main` |
| 182 | 2026-05-16 | refactor: 删除所有图片模板的 header-eyebrow | `2874512` | `main` |
| 181 | 2026-05-16 | feat: 签到改图片渲染（DESIGN.md + frontend-design skill） | `e14f19e` | `main` |
| 180 | 2026-05-16 | fix: 允许/拒绝登入 全失败统一返回'没有待处理的登入请求' | `cfc5f39` | `main` |
| 179 | 2026-05-16 | fix: 允许/拒绝登入 至少一台成功即视为成功 | `7d9b84a` | `main` |
| 178 | 2026-05-16 | refactor: Web UI Token 启动日志去脱敏 | `e8cc5f3` | `main` |
| 177 | 2026-05-16 | fix: 仪表盘命令计数过滤已下线命令 | `7361a74` | `main` |
| 176 | 2026-05-16 | rename: 全亮地图 → 查看全亮地图 | `e190f60` | `main` |
| 175 | 2026-05-16 | users per_page=0 全表通道补全校验器侧 | `4b0d734` | `main` |
| 174 | 2026-05-16 | 移除 3 个端点的限速 / 节流 | `bfda70d` | `main` |
| 173 | 2026-05-16 | 修复命令别名 + @用户 解析失败 | `4cddfe2` | `main` |
| 172 | 2026-05-16 | inventory/progress 模板 eyebrow 玩家查询→查询系统 | `bdb115b` | `main` |
| 171 | 2026-05-16 | 管理员列表删除 Owner badge | `1f7045e` | `main` |
| 170 | 2026-05-16 | 服务器列表移到查询系统 + 玩家查询改名查询系统 | `5b0e63d` | `main` |
| 169 | 2026-05-16 | 服务器列表移到查询系统 + 玩家查询改名查询系统 | `5b0e63d` | `main` |
| 168 | 2026-05-16 | 抽奖标题去结果 + 抽奖发图加 @ | `25745fd` | `main` |
| 167 | 2026-05-16 | lottery_list 每奖池右侧加抽奖命令 | `d75709a` | `main` |
| 166 | 2026-05-16 | lottery_list 移除底部 hint + 每奖池右侧加查看奖池命令 | `a2da8d6` | `main` |
| 165 | 2026-05-16 | shop_list 移除底部 hint + 每商店右侧加查看商店命令 | `eef701f` | `main` |
| 164 | 2026-05-16 | 我的红包截图加 @ 调用者 | `34f013c` | `main` |
| 163 | 2026-05-16 | 抢劫图片加 QQ 头像 + 中央流向增强 | `dff4eb5` | `main` |
| 162 | 2026-05-16 | 抢劫改图片渲染 + 警察→地牢守卫 | `472c2a9` | `main` |
| 161 | 2026-05-16 | 命令格式错误回复加 @ 调用者（集中入口） | `35485d4` | `main` |
| 160 | 2026-05-16 | 猜数字改为图片渲染（dice 同模式） | `8aac18d` | `main` |
| 159 | 2026-05-16 | 全量补齐 plugin 命令失败回复的 @ 调用者（134 处 / 12 文件） | `c2df99b`, `7c7fae5` | `main` |
| 158 | 2026-05-16 | 我的信息 / 用户信息 失败路径全部加 @ 调用者 | `29dedc1` | `main` |
| 157 | 2026-05-16 | 我的信息 / 用户信息 截图加 @ 调用者 | `e926c78` | `main` |
| 156 | 2026-05-16 | dice 改动审计 R1+R2 闭环（20 findings / 8 fixes / 0 new H） | `3e8792c` | `main` |
| 155 | 2026-05-16 | dice 加 win_rate 概率控制（默认 50%，仅大/小，豹子保留自然） | `7257317` | `main` |
| 154 | 2026-05-16 | dice 标题去「结果」+ render_and_send_screenshot 加 at_user_id | `f340aaa` | `main` |
| 153 | 2026-05-16 | dice 求和行 total 字体改 mono 对齐骰子数字 | `c7552bd` | `main` |
| 152 | 2026-05-16 | dice.html 重排版对齐 lottery_result 风格 | `b1e9c6a` | `main` |
| 151 | 2026-05-16 | 补注册 /render/dice 路由（修复掷骰子图片 404） | `5b72f5b` | `main` |
| 150 | 2026-05-16 | 掷骰子改为图片渲染（DESIGN.md 风格） | `f3f229e` | `main` |
| 149 | 2026-05-16 | 注册账号文案优化（已注册提示更直白 + 成功加白名单提示） | `afde5c0` | `main` |
| 148 | 2026-05-16 | permission_manager 下线 3 条 admin 用户权限命令 | `2bb9770` | `main` |
| 147 | 2026-05-16 | server_manager 仅保留「服务器列表」（admin 3 条下线） | `a8b2a74` | `main` |
| 146 | 2026-05-16 | 下线 group_manager 插件（WebUI groups 已完整覆盖） | `bcf49ec` | `main` |
| 145 | 2026-05-16 | 移除 lottery template 残留的高权命令前缀提示 | `45ce670` | `main` |
| 144 | 2026-05-16 | 按用户偏好回退 3 项审计限制（users per_page=0 / groups 保留名 / lottery 命令黑名单） | `07cde92` | `main` |
| 143 | 2026-05-16 | 统一表格 ID 列文案：shop / lottery 改「#数字」为 DB ID | `5aed9c4` | `main` |
| 142 | 2026-05-16 | 商店商品切换 kind 加确认 dialog（对齐 lottery） | `b369003` | `main` |
| 141 | 2026-05-16 | 用自写 dialog 替换 window.alert / window.confirm | `ac908a4` | `main` |
| 140 | 2026-05-16 | shop / lottery 导入确认文案统一为「全量替换」 | `c613417` | `main` |
| 139 | 2026-05-16 | shop / lottery CRUD 成功补 toast | `b9080bd` | `main` |
| 138 | 2026-05-15 | app_shell 退出登入加确认 dialog（含共享 modal 样式下沉） | `bddbcdd` | `main` |
| 137 | 2026-05-15 | 修复设置页重启 poll：401 视为已恢复 + '正在重启' 状态提示 | `d0e4a80` | `main` |
| 136 | 2026-05-15 | 修复商店 _load_server_label_map SQLite 自死锁（同 lottery 同形） | `cccd569` | `main` |
| 135 | 2026-05-15 | 修复抽奖创建/更新奖品 SQLite BEGIN IMMEDIATE 自死锁 | `7c24541` | `main` |
| 134 | 2026-05-15 | 项目剩余未审计代码全量审计 + 修复（5 bucket / 82 项） | `d364692` | `main` |
| 133 | 2026-05-15 | 还原 app_shell logo coral 色 + header 全宽两侧贴边 | `e93dd16` | `main` |
| 132 | 2026-05-15 | WebUI 全量审计 + 修复：剩余 6 页面 + 3 公共模块 = 162 项硬化落地 | `6995d3c`, `8bec34e` | `main` |
| 131 | 2026-05-15 | WebUI servers R2 audit (R1 修复复审 + 全量再扫) | - | `main` |
| 130 | 2026-05-15 | WebUI 服务器管理页面审计 + 全修（token 链改造） | `1355521` | `main` |
| 129 | 2026-05-15 | WebUI 命令配置页面 R2 复审 + 全修（含 R1 regression B-7） | `f512c8c` | `main` |
| 128 | 2026-05-15 | WebUI 命令配置页面审计 + 全修 | `10d7936` | `main` |
| 127 | 2026-05-14 | fix: WebUI auth middleware 区分 API 401 vs HTML 302 | `9df669b` | `main` |
| 126 | 2026-05-14 | Dashboard R3 复审：彻底闭环（无代码改动） | - | `main` |
| 125 | 2026-05-14 | WebUI 仪表盘 Round 2 — 跨模块 P2 回归 + dashboard 清理 | `c1a96ca` | `main` |
| 124 | 2026-05-13 | chore: gitignore SQLite WAL 副边文件 | `bc396f4` | `main` |
| 123 | 2026-05-13 | WebUI 仪表盘审计 + 10 项修复落地 | `c118d91` | `main` |
| 122 | 2026-05-13 | WebUI 登入审计 + 5 项安全加固落地 | `2e3a953` | `main` |
| 121 | 2026-05-13 | fix: 菜单截图样式恢复为 1920 宽版 | `788f781` | `main` |
| 120 | 2026-05-13 | Round 9 — nextbot 基础设施层第二次复审 + 收敛闭环 | `07042be` | `main` |
| 119 | 2026-05-13 | Round 8 — nextbot 基础设施层复审 + 全量再扫 + 全修 | `5c41928` | `main` |
| 118 | 2026-05-13 | Round 7 — nextbot 基础设施层（plugins 外）首轮系统审计 + 全修 | `66b4d6c` | `main` |
| 117 | 2026-05-13 | Round 6 复查 — plugins sweep 收敛 | - | `main` |
| 116 | 2026-05-13 | Round 5 复查 + 4 项修复 (cap-stats 家族闭合) | `565736e` | `main` |
| 115 | 2026-05-09 | Round 4 复查 + 5 项修复 | `a9ecbc1` | `main` |
| 114 | 2026-05-09 | Round 3 复查 + 11 项修复 + MAX_COINS 100 亿 | `8de726c` | `main` |
| 113 | 2026-05-09 | Post-sweep 复查 + 8 项收尾修复 | `0b06d76` | `main` |
| 112 | 2026-05-09 | Final sweep 全量复审 + 14 项修复 | `8d98920` | `main` |
| 111 | 2026-05-09 | 截图功能迁移到公共 helper | `203d7d6` | `main` |
| 110 | 2026-05-09 | 剩余 5 类 plugins 审计修复 (final sweep) | `e7f9ae9` | `main` |
| 109 | 2026-05-09 | 权限管理命令审计修复 | `b6e0db4` | `main` |
| 108 | 2026-05-08 | 安全管理命令审计修复 | `34aa7b1` | `main` |
| 107 | 2026-05-08 | 玩家查询命令审计修复 | `5720eda` | `main` |
| 106 | 2026-05-08 | 服务器工具/管理命令审计修复 | `942d923`, `4fd61e8` | `main` |
| 105 | 2026-05-08 | 商店系统命令审计与修复 | `3e26710` | `main` |
| 104 | 2026-05-07 | 仓库系统命令审计与修复 | `8d5ba4d` | `main` |
| 103 | 2026-05-07 | 红包系统审计修复 + rowcount 类型告警统一 | `6ca05b8`, `ec42714` | `main` |
| 102 | 2026-05-07 | 小游戏系统命令审计与修复 | `fe11241` | `main` |
| 101 | 2026-05-07 | 新增 查看地图 命令 玩家共同探索地图 | `b8ae9aa` | `main` |
| 100 | 2026-05-07 | 查看地图 命令改名 全亮地图 | `ee6b320` | `main` |
| 99 | 2026-05-07 | 经济系统命令审计与修复 | `0206834` | `main` |
| 98 | 2026-05-06 | 用户系统命令审计与修复 | `011aa68` | `main` |
| 97 | 2026-05-06 | 新增 地图探索率排行榜 命令 | `235fa5a` | `main` |
| 96 | 2026-05-06 | 背包页面新增地图探索率 | `a23be37` | `main` |
| 95 | 2026-05-06 | 新增 用户地图 命令 | `0684bbb` | `main` |
| 94 | 2026-05-06 | 我的地图 增加艾特用户 | `c773bad` | `main` |
| 93 | 2026-05-06 | 查看商店排版改 2 列网格 | `890c2a3` | `main` |
| 92 | 2026-05-06 | 查看奖池排版改 2 列网格 | `9f42d9f` | `main` |
| 91 | 2026-05-06 | 新增 我的地图 命令（玩家查询） | `5c7b2c7` | `main` |
| 90 | 2026-05-05 | 抽奖概率精度提升至 0.01% | `c3bf4fa` | `main` |
| 89 | 2026-05-05 | 彻底清理 RENDER_THEME 残留死代码 | `f40ee7a` | `main` |
| 88 | 2026-05-05 | 排行榜命令崩溃修复 (theme 残留) | `8dc55ea` | `main` |
| 87 | 2026-05-05 | 截图等待动态资源回归修复 | `48dc18c` | `main` |
| 86 | 2026-05-05 | 截图 Timeout 修复 + Playwright 性能优化 | `21822f7` | `main` |
| 85 | 2026-05-04 | 用户抢劫状态切换（带金币消耗） | `de932cb` | `main` |
| 84 | 2026-05-04 | WebUI 侧栏 logo 切换为 SVG 并适配暗色 | `f0eb369`, `7b9d357` | `main` |
| 83 | 2026-05-04 | 新增 NEXT BOT logo SVG 资源 | `bc4bde4` | `main` |
| 82 | 2026-05-04 | 菜单图片显示命令别名 | `8211186` | `main` |
| 81 | 2026-05-04 | WebUI 侧边栏 身份组管理 图标优化 | `ac77fcf` | `main` |
| 80 | 2026-05-04 | WebUI 侧边栏 命令配置 / 抽奖管理 图标优化 | `a11d985` | `main` |
| 79 | 2026-05-04 | WebUI native confirm() 替换为 dialog + 删除文案统一使用「」 | `55b6322` | `main` |
| 78 | 2026-05-04 | WebUI 整体按 DESIGN.md 重构（Phase 1–5 + 后续 polish） | `58381ee`, `e7bdce1`, `1fd3e0d`, `c5d7589`, `6df0341`, `39e94d4`, `4d9978d` | `main` |
| 77 | 2026-05-04 | 完成 关于 / 教程 重构 + 删除 RENDER_THEME 设置项 | `8e76a7c` | `main` |
| 76 | 2026-05-04 | 封禁列表 / 管理员列表 重构 + render eyebrow 修正 | `b2ab6e1`, `5e81f0b` | `main` |
| 75 | 2026-05-04 | 我的背包 / 用户背包 / 进度 页面按 DESIGN.md 重构 | `b61c87f` | `main` |
| 74 | 2026-05-04 | 排行榜 页面按 DESIGN.md 重构 | `7b4d7ca` | `main` |
| 73 | 2026-05-04 | 奖池列表 / 查看奖池 / 抽奖结果 页面按 DESIGN.md 重构 | `5cebee7` | `main` |
| 72 | 2026-05-04 | 商店列表 / 查看商店 页面按 DESIGN.md 重构 | `e501ff2` | `main` |
| 71 | 2026-05-04 | 我的仓库 / 用户仓库页按 DESIGN.md 重构 | `e2893d3` | `main` |
| 70 | 2026-05-04 | 红包列表 / 我的红包页按 DESIGN.md 重构 | `2c7e26e` | `main` |
| 69 | 2026-05-04 | 用户信息页按 DESIGN.md 重构（canvas-first + 数字字体修正） | `36fd875` | `main` |
| 68 | 2026-05-04 | 菜单截图高度自适应 | `35e70e5` | `main` |
| 67 | 2026-05-04 | Trellis 0.5 升级 + 菜单页面按 DESIGN.md 重构 | `2c5405c`, `aa72ce0` | `main` |
| 66 | 2026-05-03 | 仓库赠送 + 访客权限同步 + NoneBot2 T_State 注入修复 | `601b2a6`, `99cd3ba` |
| 65 | 2026-04-26 | WebUI 商店 / 抽奖管理 JSON 导入导出 + Docker 镜像稳定化 + v1.4.1 release | `29cc38f`, `eac8bc8`, `4d00861`, `97a9be9`, `6159615` |
| 64 | 2026-04-25 | 奖池系统 #N 清理 + 抽奖系统使用教程 + v1.4.0 release | `ce89fcf`, `0c23633` |
| 63 | 2026-04-25 | 商店系统使用教程编写 + 商店/商品标识符统一为稳定 DB ID | `7c1dd03`, `18e85d0` |
| 62 | 2026-04-25 | 抽奖结果页 UI 重构 + 奖品估值口径修正 | `1526e16`, `008b7b9` |
| 61 | 2026-04-23 | 新增「使用教程 仓库系统」并收紧仓库截图高度 | `0955d45`, `6d11b04` |
| 60 | 2026-04-23 | Warehouse system + tutorial command + reply layout standardization | `eaa93f0`, `bae3905`, `2357809`, `935f18c`, `411c408`, `0eb3e36`, `459bb75`, `57b6a99`, `ab05ca1`, `d903106`, `82542f3`, `09f6abc`, `d25e17b` |
| 59 | 2026-04-22 | 菜单二级分类重构 + admin 字段清理 | `eb9e0e0`, `7ac1889` |
| 58 | 2026-04-20 | 新增猜数字/掷骰子排行榜与 v1.1.1 发布 | `2d08c13`, `fa436a2` |
| 57 | 2026-04-12 | 抢劫系统、更改用户名称、签到奖励调整 | `1890c77`, `ced5eda`, `26dfa6e` |
| 56 | 2026-04-12 | 封禁安全修复、用户名大小写不敏感、API 全量查询、更改用户名称 | `25c15a9`, `4d4e7b0`, `e0b5aec`, `ced5eda` |
| 55 | 2026-04-12 | 封禁系统、关于页面、签到排名 | `2107e4d`, `9cda3fc`, `2b67a0e`, `64441e4`, `f69061b`, `e6dad1a`, `39ada49`, `735758b` |
| 54 | 2026-04-12 | 添加关于页面和视频教程链接 | `2107e4d`, `9cda3fc` |
| 53 | 2026-04-10 | Command aliases, login notify config, daily sign leaderboard | `d290268`, `038c737`, `1d93501` |
| 52 | 2026-04-10 | Daily sign-in leaderboard | `d290268` |
| 51 | 2026-04-10 | Admin list config order option | `29e5108` |
| 50 | 2026-04-10 | Mention user in operation replies & unify QQ label | `ce08623`, `eb713b6` |
| 49 | 2026-04-10 | Unify user ID label to QQ | `ce08623` |
| 48 | 2026-04-09 | Redesign progress page | `0ac76a3` |
| 47 | 2026-04-09 | Redesign inventory page | `0eafedb` |
| 46 | 2026-04-09 | Login confirmation UX improvements | `2b5d20a`, `4c90c61` |
| 45 | 2026-04-09 | Fix settings crash & add docs | `e8c15ff`, `f25112b`, `d37f495`, `15b7c7f`, `a521737` |
| 44 | 2026-04-09 | 权限键命名空间对齐 + guest 组默认权限补全 | `17c2618`, `1ed4315` |
| 43 | 2026-04-08 | 插件配置编辑器新增 autoLogin 字段 | `8b534aa` |
| 42 | 2026-04-08 | 登入二次确认：WebUI 请求端点 + 允许/拒绝登入命令 | `8b236fc`, `dfd36cb` |
| 41 | 2026-04-08 | 服务器插件配置编辑器 + NextBot 连通性验证 + 归属菜单文案 | `b7bfd68`, `853c8d7`, `3302ada` |
| 40 | 2026-04-08 | WebUI 支持 query 参数 token 鉴权 | `2c390ea` |
| 39 | 2026-04-08 | 按主题重组 plugins 目录 | `aabafda` |
| 38 | 2026-03-31 | 命令 admin 字段可配置化 | `b2971f1` |
| 37 | 2026-03-31 | 贡献墙算法修复 & 同步白名单优化 | `520fd79`, `a726135` |
| 36 | 2026-03-27 | 管理员列表 | `8648fac` |
| 35 | 2026-03-27 | 用户信息图片渲染 | `ff16fe8` |
| 34 | 2026-03-27 | 签到日期记录 | `6f11da4` |
| 33 | 2026-03-27 | 总在线时长排行榜 | `76cbbdc` |
| 32 | 2026-03-27 | 背包在线时长 & 排行榜修复 | `96d6f7c` |
| 31 | 2026-03-27 | 在线时长排行榜 | `194bbaa` |
| 30 | 2026-03-26 | 死亡/渔夫任务排行榜 | `44de5ae`, `4d7de26` |
| 29 | 2026-03-26 | 死亡排行榜 | `44de5ae` |
| 28 | 2026-03-25 | WebUI 用户签到字段展示与编辑 | `450d72b` |
| 27 | 2026-03-25 | 搜索命令 & guest 默认权限补全 | `d33dd39`, `fb02090` |
| 26 | 2026-03-25 | 用户信息展示累计签到和连续签到 | `2f8cade` |
| 25 | 2026-03-25 | 累计签到排行榜 | `3665c26`, `a902664` |
| 24 | 2026-03-25 | 用户累计签到次数字段 | `3665c26` |
| 23 | 2026-03-25 | 菜单页面图片主题适配 | `bbcb348` |
| 22 | 2026-03-25 | 背包页面图片主题适配（dark/light） | `a3e0c1b`, `5108446` |
| 21 | 2026-03-25 | 进度页面图片主题适配 + render_utils 公共模块 | `8904b88` |
| 20 | 2026-03-25 | 排行榜图片主题适配（dark/light/auto） | `7dba598` |
| 19 | 2026-03-25 | 新增图片主题配置项 render_theme | `2dbbe58` |
| 18 | 2026-03-25 | 排行榜新增我的排名显示 | `6da6f0c` |
| 17 | 2026-03-25 | 排行榜翻页功能 | `c239b71` |
| 16 | 2026-03-25 | 新增连续签到排行榜 | `ed527c8` |
| 15 | 2026-03-25 | 排行榜颁奖台布局优化 | `34abf20` |
| 14 | 2026-03-25 | 排行榜通用化重构 | `8a38108` |
| 13 | 2026-03-25 | 新增排行榜插件 - 金币排行榜 | `aef9f35` |
| 12 | 2026-03-25 | 菜单拆分为菜单/管理菜单，新增 admin 标记机制 | `fd6a8d3` |
| 11 | 2026-03-25 | economy 插件新增转账功能 | `281cbe9` |
| 10 | 2026-03-25 | 重设计背包页面精致亮色主题 | `98c63ba` |
| 9 | 2026-03-25 | 重设计进度页面 Terraria 暗色主题 | `05790ea` |
| 8 | 2026-03-24 | 背包命令新增 send_link 参数 | `58c446b` |
| 7 | 2026-03-24 | 新增下载地图命令 | `4ecd5f5` |
| 6 | 2026-03-24 | 新增查看地图命令 | `339f743` |
| 5 | 2026-03-24 | 迁移白名单接口到 NextBotAdapter API | `9058538` |
| 4 | 2026-03-24 | 迁移进度接口到 NextBotAdapter API | `e6f9634` |
| 3 | 2026-03-24 | 迁移背包接口到 NextBotAdapter API | `d486f59` |
| 2 | 2026-03-18 | Disable implicit OneBot startup connection | `c9f1628` |
| 1 | 2026-03-18 | Refine WebUI API semantics and pagination | `a7bb49d`, `46d921c`, `608f1ec`, `d0adcf5`, `1c782a4` |
<!-- @@@/auto:session-history -->

---

## Notes

- Sessions are appended to journal files
- New journal file created when current exceeds 2000 lines
- Use `add_session.py` to record sessions