# 商店系统命令审计报告（已二次复查）

**审计对象**：`nextbot/plugins/shop.py` 3 个 `category="商店系统"` 命令（共 660 行）
**审计日期**：2026-05-07
**复查方式**：trellis-research sub-agent 初审 → 主代理逐条对照源码 + 调用链验证
**参照修复**：commit `0206834`（economy）+ `8d5ba4d`（warehouse）+ `ec42714`（execute_rowcount）

## 严重级别分布（复查后）

- 🔴 必修：**3** —— 两条买入路径金币 lost-update + 指令购买 DB-API 双重一致性
- 🟠 应修：**5** —— 3 handler 缺异常兜底 / 单价+总价+buy_count 上界 / actual_value 上界 / TOCTOU 商品下架 / 列表 N+1 全量取
- 🟡 建议：**6** —— `_safe_param_int` / 大小写匹配 / 多 session / 物品入仓 quantity 上界 等
- 🟢 观察：**4**

复查无误报。所有 🔴 / 🟠 项均已对照源码逐行确认。

**商店系统的特殊性**：3 个命令同时操作 3 类资源（金币 / WarehouseItem / TShock API），但 ShopItem schema **没有 stock 字段**（`db.py:274-302`），所以"超卖"问题**不存在**，是相对仓库简单的部分。

---

## 🔴 必修

### S-1.1 — 普通购买（kind="item"）：user.coins lost-update（金币凭空产生）

- **位置**：`shop.py:478-517`，关键 line 482 / 486-492 / 498 / 514
- **现象**：经典 read-modify-write，与 economy F-2.1 / red_packet R-1.1 / warehouse W-6.1 同模式：
  ```python
  user = session.query(User).filter(...).first()  # line 482：读 stale
  coins = int(user.coins or 0)
  if coins < total_price: ...                      # 应用层检查
  user.coins = coins - total_price                 # line 498：写绝对值
  session.commit()                                 # line 514
  ```
- **关键**：`warehouse_lock(user_id)` 只保护 WarehouseItem 不保护 `User.coins`（`warehouse_lock.py:6-8` 注释明确）。webui 转账 / 签到 / 抢红包等非仓库路径都不获取此 lock
- **复现**（A 余额 100，商店物品 80 金币）：
  1. A 几乎同时执行 `购买商品 1 5` 与（被人）`转账 A 100`
  2. shop 读 100、检查 ≥ 80 通过；transfer 读 100，写 200 commit
  3. shop 写 `100 - 80 = 20` commit（**覆盖 200**）
  4. 最终 A.coins=20，应为 120 → **A 损失 100**
- **修复方案**：与 economy F-2.1 同模板，复用 `execute_rowcount`：
  ```python
  rowcount = execute_rowcount(
      session,
      update(User).where(User.user_id == user_id, User.coins >= total_price)
      .values(coins=User.coins - total_price),
  )
  if rowcount == 0:
      coins_now = int(session.query(User.coins).filter(...).scalar() or 0)
      ...金币不足回复
      return
  # 然后 add WarehouseItem，同事务 commit
  ```

### S-1.2 — 指令购买（kind="command"）：user.coins lost-update

- **位置**：`shop.py:610-622`
- **现象**：与 S-1.1 同模式（无 warehouse_lock 包裹，更直白）
- **修复方案**：与 S-1.1 同模板。注意保留"commit 在 TShock 调用之前"的现有顺序

### S-2.1 — 指令购买：扣金币 + TShock /give 双重一致性窗口

- **位置**：`shop.py:622 commit → 627-633 调 TShock`
- **现象**：与 warehouse W-7.1 同根，但**方向相反**：
  - warehouse 领取：先 give 后 commit → commit 失败 = 玩家拿到物品但 DB 仍认为有
  - 商店购买指令：先 commit 后 give → give 失败 = **金币已扣但命令未执行**
- **失败路径**：
  1. line 622 commit → 玩家金币已扣
  2. line 632 `_issue_raw_command` 网络超时返回 `(False, "无法连接服务器")`
  3. 回复"成功 0 / 失败 1"，**金币不退**
- **二级问题（部分成功）**：当 `target_server_id is None`，循环每个服务器 give，**任一失败不阻断后续**
- **三级问题**：line 631 `for _ in range(buy_count)` 中途 raise 时已成功的 give 不可逆
- **修复方案**（无完美方案）：
  - **保守**：全部失败时 `logger.error` + 回复"金币已扣但所有服务器执行失败，请联系管理员退款"
  - **更稳**：全部失败时 `update(User).values(coins=User.coins + total_price)` 自动回退；部分成功仍走 logger.error

---

## 🟠 应修

### S-Common.1 — 3 handler 缺异常兜底

- 与 economy F-Common.3 / warehouse W-Common.1 同病。`session.commit` 抛 IntegrityError / OperationalError / `bot.send` 网络错时被 NoneBot 顶层吞掉

### S-Common.2 — 单价 / 总价 / buy_count 缺 MAX_COINS_AMOUNT 上界

- **位置**：webui `webui_shop.py:135-139` 仅校验 `price < 0`（含 0 都过）；`shop.py:443` `total_price = target_price * buy_count` 无上界；`shop.py:401-403` buy_count 仅 ≥ 1
- **三个风险**：
  1. **price=0 合法**：admin 配 price=0 → 玩家无限循环购买白嫖物品
  2. **总价无上界**：可超 `MAX_COINS_AMOUNT = 100_000_000`，违反经济限额
  3. **buy_count 性能炸弹**：`购买商品 1 1 1000000` → 100 万次 HTTP，bot 假死
- **修复方案**：webui + shop 两侧加 `MAX_COINS_AMOUNT` / `MAX_BUY_COUNT` 上界

### S-Common.3 — 物品入仓 actual_value 缺上界，绕过 economy 限额

- **位置**：`shop.py:499-510` + webui `webui_shop.py:195` 仅 `actual_value >= 0` 校验
- **现象**：admin 误输 `actual_value=2_000_000_000` → 物品入仓 → 玩家 `回收仓库物品` 一格即获 20 亿金币（**绕过 warehouse 已修复的 W-3.2**）
- **关键**：warehouse 的"添加物品"命令 W-3.2 已校验 `value > MAX_COINS_AMOUNT`，但 **shop 写 WarehouseItem 走另一条路径**，绕过了那个校验
- **修复方案**：
  1. `shop.py:500` 改 `unit_value = max(0, min(int(actual_value), MAX_COINS_AMOUNT))`
  2. webui 加 `actual_value <= MAX_COINS_AMOUNT` 校验

### S-3.1 — TOCTOU：商品在 buy 流程中被 admin 下架/删除

- **位置**：`_buy_item`（line 479-517）+ `_buy_command`（line 539-625）
- **现象**：第一段 session 查到 `target.enabled=True` 后 close；第二段 session 只查 user，**不再校验 ShopItem.enabled**。期间 admin 下架/删除 → 玩家仍能买
- **修复方案**：第二段 session 内重读 `ShopItem.enabled` + 校验

### S-3.2 — `handle_shop_list` / `handle_shop_view` 全量取后切片 + N+1

- **位置**：line 173-205 / 289
- **现象**：`session.query(Shop).all()` 全取再 python 切片；每个 shop `.count()` → 100 商店 = 1 + 100 次查询
- **修复方案**：单次 `LEFT JOIN` 一次性取 `(shop, item_count)`；`LIMIT/OFFSET` 推到 SQL 端

---

## 🟡 建议

### S-Common.4 — `limit` 参数未走 `_safe_param_int`
- `shop.py:169 / 277`：与 dice / guess_number 已采用的 helper 不一致

### S-2.2 — `_check_player_online` 大小写匹配脆弱（与 warehouse W-7.4 同根）
- shop 仍是 `lower()`，warehouse 已修为 NFKC + casefold
- 修复：抽公共 helper

### S-2.3 — `_buy_command` 跨 await 后 server 列表 stale
- 配置生效几秒延迟，可接受

### S-3.3 — `handle_shop_view` 无注册用户也能查看
- UX 设计，无 bug

### S-Common.5 — 多 session（性能微小）
- 与 warehouse W-Common.3 同；engine 单例后影响不大

### S-Obs.1 — `_buy_item` quantity 无上界（与 warehouse W-3.1 同根）
- admin 配 quantity=999999 + buy_count=999999 → total_quantity 极大值，TShock give 时爆
- 修复：与 warehouse W-3.1 一并修

---

## 🟢 观察

- **S-Obs.2**：3 个命令都在 guest 默认权限里
- **S-Obs.3**：`command_template.replace("{player}", player_name)` 字符串替换，玩家名含空格会让 TShock 命令解析错位，admin 职责
- **S-Obs.4**：`temp_screenshot_path` 已迁移 ✓
- **S-Obs.5**：webui delete shop 正确 cascade 清理子 ShopItem

---

## 与最近修复的对照

| 修复点 | 商店是否同病 |
|---|---|
| economy F-2.1 转账并发条件 UPDATE | **S-1.1 + S-1.2 同病** |
| F-Common.1 MAX_COINS_AMOUNT | **S-Common.2 全部缺** |
| F-Common.3 异常兜底 | **S-Common.1 全部缺** |
| `_safe_param_int` helper | **S-Common.4 未复用** |
| `temp_screenshot_path` | 已迁移 ✓ |
| `execute_rowcount` helper | S-1.1 / S-1.2 修复时复用 |
| warehouse W-7.x DB-API 双重一致性 | **S-2.1 同病（方向反）** |
| warehouse W-7.4 大小写 | **S-2.2 同根** |
| warehouse W-3.1 / W-3.2 quantity / value 上界 | **S-Common.3 + S-Obs.1 同根（旁路）** |

**新维度**：
- **S-3.1 TOCTOU 商品下架** —— 商店独有的"长链路 + admin 异步操作"问题
- **S-3.2 N+1 / 全量切片** —— 商店比仓库列表更易被 admin 数据放大触发

---

## 推荐处理顺序

1. 🔴 **S-1.1 + S-1.2 金币 lost-update**（同模板，2 路径一并改）
2. 🔴 **S-2.1 指令购买双重一致性**（先加 commit 后失败汇总告警日志）
3. 🟠 **S-Common.1 异常兜底**
4. 🟠 **S-Common.2 单价 + 总价 + buy_count 上界**（webui + shop 两侧）
5. 🟠 **S-Common.3 actual_value cap**
6. 🟠 **S-3.1 TOCTOU 重读 ShopItem.enabled**
7. 🟠 **S-3.2 N+1 / 全量切片性能**
8. 🟡 其余下一轮

## 主代理复查最容易误判的点

- **S-Common.2 price=0 风险**：webui 仅 `price < 0` 校验（含 0 合法）。叠加 buy_count 无上界 → 无成本无限购入 → 配合 S-Common.3 让 admin 一次配错就让玩家洗仓库
- **S-2.1 部分失败语义**：当 `target_server_id is None` 时部分服失败是否回款是**业务决策**，不是代码 bug。但 0/N 服务器成功仍扣全款违反"未送达不扣款"常识
- **S-3.2 无 stock**：商店系统**没有库存字段**，所以"超卖"/"限购 race"不存在；不要凭直觉报这两类问题
