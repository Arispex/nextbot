# WebUI 商店页面审计 — round-1

- **Scope（严格 4 文件）**：
  - `server/routes/webui_shop.py`（846 LOC）
  - `server/webui/templates/shop_content.html`（319 LOC）
  - `server/webui/static/js/shop.js`（856 LOC）
  - `server/webui/static/css/shop.css`（725 LOC）
- **Date**: 2026-05-15
- **Prior art reviewed**: `05-15-webui-servers-audit-r2/prd.md`、`05-15-webui-commands-audit-r3/prd.md`
- **Out of scope**：`api.js` / `webui.js` / shell / 其他 webui 模块 / 中间件 / 经济插件 / 仓库插件 / TShock 下游。任何跨模块发现统一标 **`scope-out backlog`**。

## 共享前置事实（用于判定本桶 finding 严重度）

1. `add_webui_auth_middleware`（`server/routes/webui.py:197-220`）已对 `/webui/*`（除 `/webui/login` / `/webui/api/session` / `/webui/static/`）全局拦截：未登录的 `/webui/api/*` 返回 `401 unauthorized`、HTML 返回 302。`webui_shop.py` 全部 8 个 `/webui/api/shops*` 路由均落入该中间件覆盖。因此 **authn 维度只需复审是否走 router 注册路径**，无需逐路由判定。
2. `read_json_object` 已统一拒绝非对象 / 非 JSON 请求体；shop 路由全部都先调用它。
3. 后端 success/error 已用 `api_error` / `api_success` 标准化（包括 `details` 数组）。
4. `apiRequest`（`api.js:179-265`）统一处理 401 跳转 / `buildActionFailureMessage(action, reason)` 形态。shop.js 全部出错都走 `err.message` 显示，因此**后端 `error.message` 是面向用户的文案来源**。
5. ORM：`Shop.name` 唯一约束（DB 级别），`ShopItem` 在同一 shop 下名称无唯一约束（业务允许重名）。
6. 经济上界 `MAX_COINS_AMOUNT = 10_000_000_000`、`MAX_ITEM_QUANTITY = 9999`，已在路由内同步硬编码（带注释解释原因）。
7. servers / commands 审计已分别在 `_client_ip` / `user_agent` 维度补齐 logger 上下文（M-B3 / D-2）。**webui_shop.py 0 处补齐**——是后端发现的最主要 gap。

---

# 一、按严重度汇总

| 严重度 | 数量 | 编号 |
|--------|------|------|
| Critical | 0 | — |
| High | 3 | H-1 ~ H-3 |
| Medium | 10 | M-1 ~ M-10 |
| Low | 11 | L-1 ~ L-11 |
| 合计 | **24** | |

**Top 3 最高严重项（皆 High）**：

1. **H-1**：8 个 shop 写入路径 logger 全部缺失 `client_ip` / `user_agent`，与 servers / commands 已建立的项目惯例（D-2 / M-B3）不一致；admin 操作不可追溯（含批量 import 的破坏性日志）。
2. **H-2**：`POST /webui/api/shops/import` 在 `mode=replace_all` 时**先 `DELETE` 所有 ShopItem + Shop 再 flush，再校验目标插入**，但 _校验已前置完成_——问题是**校验不验 `target_server_id` 在已删除上下文中是否还有效**（提前一次性加载到 `server_ids`，无 race 防护），且**整体路径未受任何幂等 token / dedupe 保护**：admin 误连点导入按钮、网络重传、CSRF 触发（已在共享层无 CSRF 防御）都会导致**真实的数据库整表清空**。属于"破坏性 + 跨用户副作用"，与`prd.md` 「Shop-specific concern: money / economy」 维度直接相关。
3. **H-3**：前端 `submitShopModal` / `submitItemModal` / `confirmDeleteShop` / `confirmDeleteItem` / `confirmImport` 全部**未设 in-flight guard**（无 `submitting` flag、无 `disabled` 切换）。用户在网络慢时双击「保存」「删除」「导入」会发出 2~N 个并发请求，对 import / replace_all 路径可直接放大 H-2 的影响。

---

# 二、Findings 详表

## ① Security

### H-1 8 个写入 endpoint 全部缺失 `client_ip` / `user_agent` 日志上下文
**File**: `server/routes/webui_shop.py:360, 432-435, 616-619, 695, 713, 764-767, 821-824, 843`
**Dimension**: security
**Issue**：所有 8 处 admin 写入路径只 `logger.info(f"... shop_id=... name=...")`，无 IP、无 UA：
- `create_shop`（line 360）
- `export_shops`（line 432）
- `import_shops`（line 616-619，包含 `mode=replace_all` 这条全表清空动作）
- `update_shop`（line 695）
- `delete_shop`（line 713）
- `create_shop_item`（line 764-767）
- `update_shop_item`（line 821-824）
- `delete_shop_item`（line 843）

参考 `webui_servers.py:59-78` 已有 `_client_ip` / `_user_agent` helper（servers R1 D-2 / commands R2 M-B3 已落地）。shop 路由完全没复用，与 24 小时前刚收敛的 servers / commands 项目惯例直接冲突。
**Fix sketch**：import `_client_ip` from `server.routes.webui_servers` 或者本文件内复制一份 helper；签名上 8 个 endpoint 都改为接受 `request: Request`（其中 `get_shop`/`delete_shop`/`delete_shop_item` 当前签名只有 `shop_id` 没有 `request`，需要补齐）；每条 `logger.info` 追加 `client_ip={client_ip} user_agent={user_agent!r}`。`export_shops` / `import_shops` 已经收 `request: Request` 可直接用。
**Risk if unfixed**：admin 误操作 / 凭据泄漏后追溯困难；与 servers / commands 不一致，长期维护成本高。

### H-2 `import_shops` 在 `mode=replace_all` 下整表清空，无幂等令牌 / 无审计 / 无二次确认（后端层面）
**File**: `server/routes/webui_shop.py:446-630`
**Dimension**: security
**Issue**：`mode=replace_all` 路径（554-556）执行：
```python
session.query(ShopItem).delete(synchronize_session=False)
session.query(Shop).delete(synchronize_session=False)
session.flush()
```
随后插入校验后的新数据。问题点：
1. **无幂等 token**（无 `Idempotency-Key` 头 / 无 `client_request_id`）。前端 `confirmImport` 异步请求若被 admin 双击 / 网络重发，可能多次 fire——第一次清空后第二次清空+插入相同集合是幂等的，但若两次 JSON 不同（极少见但理论可能），结果不可预测。
2. **无 CSRF 防御**（中间件层只校验 cookie auth），任何能诱导 admin 浏览器对 `/webui/api/shops/import?mode=replace_all` 发 POST 的页面（同 origin 的 XSS、跨标签 form post 等）都能触发整表清空。
3. `server_ids` 在 `_load_server_id_set()`（line 482）一次性加载后校验所有 item.target_server_id，但**没有重新校验**——长事务期间若 Server 被并发删除，此处对失败行为不可控（最终插入的 item 引用了已删除 server）。这是一致性 race，非崩溃但污染数据。
4. `mode=replace_all` 删除 `ShopItem` 时用 `synchronize_session=False`，是同一 session 内有 inflight item 引用的常规优化，但**没有显式 `session.expire_all()`** 后再做 insert——SQLAlchemy 2.0 同 session 多 query 间状态混合需要注意。
5. **替换前没有自动备份导出**：`replace_all` 是不可恢复操作，UI 文案虽然提示，但后端可以保险地在删除前 `logger.warning` 输出当前 shop 数 + item 数，留下 forensic 痕迹。

**Fix sketch**：
- 添加 `logger.warning(f"WebUI 商店 import replace_all 即将执行：原 shop={N} item={M} 新 shop={X} item={Y} mode=replace_all client_ip={...} user_agent={...}")` 在删除前。
- 长期：考虑 `Idempotency-Key` 头 / `request_id` UUID 字段去重（按 60s 窗口缓存最近请求）——属于跨模块改造（共享层），先 backlog。
- 长期：CSRF 中间件统一改造（**scope-out backlog**，已在 servers A-3 出现）。
- `replace_all` 前 `session.expire_all()` 后再做 insert，避免 stale 状态。

**Risk if unfixed**：admin 一次误点 + 缺备份直接丢失整套商店配置；事后无 IP / UA / 数量记录可追。

### H-3 前端无 in-flight guard：「保存」「删除」「导入」可被并发触发
**File**: `server/webui/static/js/shop.js:355-385, 409-426, 498-551, 578-593, 710-731`
**Dimension**: security（与可靠性交叉）
**Issue**：所有写入交互（`submitShopModal`、`confirmDeleteShop`、`submitItemModal`、`confirmDeleteItem`、`confirmImport`）都未在请求飞行期间：
- 切换 button `disabled`
- 设置 `submitting` state guard
- 静默忽略二次触发

```js
async function submitShopModal(ev) {
  ev.preventDefault();
  hideAlert(els.shopModalAlert);
  // ... 直接 callApi ...
}
```
对照 commands.js R2 B-7：`aliasSaving = false;` 之后才 `closeAliasModal(true)`——commands 已建 in-flight 模式，shop 完全没复用。
配合 H-2：admin 双击「导入」按钮 → 第一次清表 + 重建，第二次再清表 + 重建。期间用户看到的状态会闪烁，且**第二次请求若网络抖动导致前端 abort 但后端已开始事务，回写状态不可控**。

**Fix sketch**：
1. `state` 加 `submittingShop / submittingItem / deletingShop / deletingItem / importing` 5 个 boolean。
2. 每个 submit / confirm 函数开头：`if (state.xxx) return; state.xxx = true; button.disabled = true;`，`finally` 内复位。
3. 模板上保留按钮原 class，不需要 CSS 改造（`.btn[disabled]` 应已有标准样式；若没有则补 `.btn:disabled { opacity: 0.6; cursor: not-allowed; }`）。

**Risk if unfixed**：并发请求放大 H-2 损害面；同一 item 被双删导致 404 + alert 闪一下；用户体验问题最显著。

### M-1 `command_template` 长度上界 / 注入面只做长度限制
**File**: `server/routes/webui_shop.py:239-246`
**Dimension**: security
**Issue**：`command_template` 校验只检查 `len <= 500` 和非空，**不约束字符集**。下游 `shop.py:833` 直接 `command_template.replace("{player}", player_name)` 后送到 TShock 执行。
- admin 是可信角色，但 export/import 路径允许导入**任意 JSON**——如果有人能诱导 admin 「导入」一份恶意 JSON（社工 / 共享备份链），就能注入任意 TShock 命令。
- player_name 在 `replace("{player}")` 中若含 TShock 特殊字符（空格、引号、换行）会破坏命令分词。这一节归 shop 插件下游，**scope-out backlog**——但 _在 admin 校验时_ 至少应禁掉 `\n`、`\r`、`\0` 这类显然不可能合法的字符（避免多行注入）。
**Fix sketch**：增加正则黑名单 `if re.search(r"[\x00-\x08\x0a-\x1f]", command_template)` → 422 `命令模板含不可见字符`。
**Risk if unfixed**：admin 在 webui 直接输入 / 误导入的多行命令会污染 TShock 执行序列；目前仅 admin 自损面。

### M-2 `sort_order` 无范围限制，可设为极大 / 极小整数
**File**: `server/routes/webui_shop.py:108-112, 156-160`
**Dimension**: security（边界 + UX）
**Issue**：`int(data["sort_order"])` 直接接受任何整数。SQLite Integer 是 64-bit，存 `2**63` 会溢出抛 OverflowError → FastAPI 500。前端 `<input type="number">` 不限定 `min` / `max`（shop_content.html:117、167）。
**Fix sketch**：clamp 到 `[-1_000_000, 1_000_000]` 之类；422 拒绝越界。前端 input 加 `min="-999999" max="999999"`。
**Risk if unfixed**：恶意 admin 可触发 500 / 整数溢出；属边界 hygiene。

### M-3 `actual_value` 上界与 `price` 上界不一致地传达给玩家
**File**: `server/routes/webui_shop.py:144-154, 207-223`
**Dimension**: security（经济）
**Issue**：`price` 和 `actual_value` 都上界到 `MAX_COINS_AMOUNT`（10 亿×10），但**两者关系无约束**：
- `actual_value > price * 100` 之类显然异常的配置（比如 price=1 → actual_value=10⁹）会被接受。
- `actual_value` 决定仓库回收金额，相当于**单价上限漏洞**：admin 误输入或合谋写大 actual_value 让玩家 buy → 仓库回收 → 经济膨胀，配合 `quantity≥1`、单笔 ≤ MAX_ITEM_QUANTITY 仍可在一次循环里把账户拉高近 10 亿。
- 注释（line 217-219）已认知 actual_value 是 "玩家通过仓库回收绕过 economy 限额" 风险点，但上界本身没和 price 做关系性约束。
**Fix sketch**：业务侧加 `if actual_value is not None and actual_value > price * 100` → 422 提示「实际单价不应超过单价的 100 倍」（数字可调），或加宏 `_MAX_ACTUAL_VALUE_RATIO = 10`。也可不动后端，仅在前端 modal 加非阻塞警示。
**Risk if unfixed**：admin 一次输错（少打一个零）→ 玩家发现并刷经济。属于经济一致性维度。

### M-4 `kind` 切换时旧字段值未清理（命令切物品 / 物品切命令）
**File**: `server/routes/webui_shop.py:737-754, 800-814`
**Dimension**: security（数据一致性）
**Issue**：`_validate_shop_item_payload` 严格按 kind 分支验证，且未设字段的 default 是 0 / "" / None。但 `update_shop_item`（line 799-814）**全字段无条件赋值**，且没有以 kind 为依据清空对端字段。
- 一个 item 原是 `kind=command`，含 `command_template="/buff..."`、`target_server_id=1`；admin 把 kind 改为 `item` 并提交。前端 `submitItemModal`（行 511-525）按新 kind 只发 item 字段，请求体中 `command_template` / `target_server_id` 缺省。`_validate_shop_item_payload` 在 kind=item 分支里**不读** `command_template`（默认 `""`），最终插入 `command_template=""`。✅ 这部分行为正确。
- 但**导入路径**（`import_shops`）/ **PUT 路径**接收的 raw JSON 可能任意包含两组字段；`_validate_shop_item_payload` 只验证 kind 对应组，**忽略另一组字段**——意思是导入 JSON 把 `kind="item"` 的 entry 顺手塞了 `command_template="/rm -rf"`，校验通过后 `command_template` 字段被设为 `""`（因为 line 171 默认 `command_template = ""`，line 239 只在 kind==command 时读取并赋值）。✅ 同样安全。
- 真正的小问题：**前端表单切 kind 不会清空对端 input 的 DOM value**（`shop.js:454-463` 只切显示），如果用户改回去会看到旧值——这是 UX 而非 security。

**Re-classify**：经过细看其实**不是 bug**。降级到 L 类信息记录（**L-1**）。原因列举见 L-1。本条移除。

### M-5 `import_shops` 在 `mode=merge` 下重新 attach 时整组 item 全删全建（无 diff）
**File**: `server/routes/webui_shop.py:569-613`
**Dimension**: security（数据一致性）
**Issue**：merge 模式下对 existing shop 的处理：
```python
# Replace all items belonging to this shop.
session.query(ShopItem).filter(ShopItem.shop_id == existing.id).delete(...)
shop_id = int(existing.id)
```
然后插入新 items——**整组替换语义**。文档（template line 302-303）声明 "已存在的更新元数据并整组替换商品"，所以语义是产品决策。但隐含影响：
- ShopItem.id 自增，整组替换会导致同名 item 拿到新 ID。
- 如果 ShopItem.id 在他处被引用（购买记录 / 抽奖回放 / 日志 grep），会断开关联——这属于跨表外键耦合，**scope-out backlog**。
- 整组 delete 前**不输出快照**：丢失多少条无 audit。
**Fix sketch**：merge 路径 delete 前 `logger.info(f"WebUI 商店 import merge：shop={name} 已删除原 items={count} 新增 items={len(items_data)}")`。
**Risk if unfixed**：merge 路径删除条数不可追溯。

### M-6 `delete_shop` 级联删除 ShopItem 无 audit
**File**: `server/routes/webui_shop.py:708-713`
**Dimension**: security
**Issue**：
```python
session.query(ShopItem).filter(ShopItem.shop_id == shop_id).delete(synchronize_session=False)
session.delete(shop)
session.commit()
logger.info(f"WebUI 商店 delete：shop_id={shop_id}")
```
日志只记 shop_id，**不记被一并删除的 item 数量、item 名称**；admin 误删 100 条商品后无线索回溯（需 DB 备份）。
**Fix sketch**：
```python
items = session.query(ShopItem).filter(ShopItem.shop_id == shop_id).all()
item_summary = ",".join(f"{it.id}:{it.name}" for it in items[:10])
session.query(ShopItem)...
logger.info(f"WebUI 商店 delete：shop_id={shop_id} name={shop.name} item_count={len(items)} items_sample={item_summary}")
```
**Risk if unfixed**：误删无快照、无回滚线索。

### M-7 `import_shops` 校验聚合无上界 → 大文件 OOM 风险
**File**: `server/routes/webui_shop.py:447-545`
**Dimension**: security
**Issue**：`payload.get("shops")` 没有数量上界，每个 shop 的 `items` 也无上界。攻击 / 误操作上传 100 MB JSON（数十万 item）→ 全部 load 到 memory → `aggregated` 列表无界增长 → 极端情况下 OOM；正常情况下校验阶段长事务持锁。
共享层 body size 应该有 FastAPI middleware 限制（`05-15-webui-commands-audit-r3` 提到 `H-B1` body size middleware 为 backlog），但 shop 本路由层**没有任何额外保护**。
**Fix sketch**：在 `import_shops` 开头：
```python
raw_shops = payload.get("shops")
if isinstance(raw_shops, list) and len(raw_shops) > 200:
    return api_error(status_code=413, code="payload_too_large", message="单次导入商店数过多（最多 200）")
# 或在校验循环内累计 item 总数 > 5000 → 拒绝
```
**Risk if unfixed**：admin 误导入大 JSON 拖垮内存；属 DoS 边界。

### M-8 输入验证不区分类型，字符串数字被 silently coerced
**File**: `server/routes/webui_shop.py:144-145, 157-159, 177-178, 184-185, 191-192, 211-214, 232-236`
**Dimension**: security
**Issue**：所有数字字段都用 `int(data.get(...))` 而非显式 `isinstance(..., int)` 校验：
```python
try:
    price = int(data.get("price", -1))
except (TypeError, ValueError):
    price = -1
```
JSON 里写 `"price": "100"`（字符串）会被接受。前端 shop.js 也用 `Number(els.itemFieldPrice.value || 0)`（line 507），保证只发数字，但 **import 路径**接受任意外部 JSON。
不是 security 漏洞，只是宽松 schema——属于"API 设计严格性"。结合 `api-design` skill 应当对所有 numeric field 显式 reject 字符串。
**Fix sketch**：抽 helper：
```python
def _strict_int(value, field, details, *, min_v=None, max_v=None):
    if not isinstance(value, int) or isinstance(value, bool):
        details.append({"field": field, "message": f"{field} 必须为整数"})
        return None
    ...
```
**Risk if unfixed**：API 文档与实际行为不一致；导入恶意 JSON 时类型解析路径不可预期。

### M-9 `target_server_id` 验证只在创建 / 更新时做，引用稳定性无保护
**File**: `server/routes/webui_shop.py:226-237, 482, 720, 784`
**Dimension**: security（数据一致性）
**Issue**：item 创建时校验 `target_server_id ∈ valid_server_ids`，但 server 被删除后，已存在的 shop_item.target_server_id 不会自动重置。serialize 时（line 76-79）会显示 `#{shop_item.target_server_id}` 即"#3" 这种 ghost reference。runtime（shop.py 购买）会针对找不到的 server fail。
**Fix sketch**：要么在 `delete_server` 时级联 `ShopItem.target_server_id=None`（**scope-out backlog**，属 servers 路由），要么在 shop GET 时检测并标注 "目标服务器已删除"。
**Risk if unfixed**：admin 看到的目标服务器列表与实际可执行 server 不一致；玩家购买失败。

### M-10 export 输出的 JSON 含 `exported_at`，但 import 不校验 → 重放检测缺失
**File**: `server/routes/webui_shop.py:436-441, 463-475`
**Dimension**: security（轻度）
**Issue**：import payload 只校验 `version=1` / `kind="shops"` / `shops` 是 list，对 `exported_at` 不读取也不校验。攻击者把 1 个月前的备份在不知情情况下重放（社工 admin），系统不会警告"这是 N 天前的备份"。
**Fix sketch**：在 import 校验通过后，根据 `exported_at` 计算"备份时间距今"，>30 天则在 response data 里追加 `warn_old_backup=true`，前端展示提示——**非阻塞**，仅信息化。
**Risk if unfixed**：admin 误用陈旧备份的恢复力不足。

### L-1 kind 切换时旧字段 DOM 值残留（前端）
**File**: `server/webui/static/js/shop.js:454-463`
**Dimension**: ux
**Issue**：`applyKindVisibility()` 只切 `.hidden` class，不清空对端 input value。从 command 切到 item 再切回 command 时，原 command_template / target_server / show_command / require_online 仍在 DOM。submit 时按 kind 分支只读当前 kind 字段，不会污染数据；但**用户视觉上不知道这些值会被丢弃**。
**Fix sketch**：`applyKindVisibility` 切换时把对端字段 set 为初始值（item: item_id=1 / prefix_id=0 / quantity=1 / min_tier="none" / actual_value="" / is_mystery=false；command: target_server_id="" / command_template="" / show_command=false / require_online=false）。
**Risk if unfixed**：UX 困惑，无数据安全风险。

---

## ② Performance

### M-11 `list_shops` 用 Python dict 聚合 ShopItem.shop_id，O(N) 但行级遍历
**File**: `server/routes/webui_shop.py:313-319`
**Dimension**: perf
**Issue**：
```python
for sid, in (
    session.query(ShopItem.shop_id)
    .filter(ShopItem.shop_id.in_([s.id for s in shops]))
    .all()
):
    counts[int(sid)] = counts.get(int(sid), 0) + 1
```
每个 shop_item 都拉回到内存后用 Python 计数。当 item 总数 ≤ 几千是 fine，>10⁴ 时应改 `GROUP BY shop_id` SQL 聚合。这不是 N+1（一次 query），但 row→python 行成本不必要。
**Fix sketch**：
```python
from sqlalchemy import func
counts_query = (
    session.query(ShopItem.shop_id, func.count(ShopItem.id))
    .filter(ShopItem.shop_id.in_([s.id for s in shops]))
    .group_by(ShopItem.shop_id)
    .all()
)
counts = {int(sid): int(cnt) for sid, cnt in counts_query}
```
**Risk if unfixed**：商品规模上去后 list_shops 慢。

### M-12 `get_shop` 内 `_load_server_label_map()` 重新 query 全表
**File**: `server/routes/webui_shop.py:635-657`
**Dimension**: perf
**Issue**：每次 GET `/webui/api/shops/{id}` 都 `_load_server_label_map()` → 全表 Server 查询 → 再用于 serialize_item。若 shop items 全是 `kind=item`（多数情况），map 完全用不上。
**Fix sketch**：lazy 加载——先 `items` 查询完毕，若任意 `it.target_server_id is not None` 才查 label_map。
**Risk if unfixed**：每次详情请求多一次表扫描，N 服务器小表影响微小。Low 也合理，保守 M。

### M-13 `loadShops()` 后再 `loadShopDetail()` 串行
**File**: `server/webui/static/js/shop.js:77-95`
**Dimension**: perf
**Issue**：
```js
async function loadShops() {
  ...
  state.shops = ...;
  renderShopList();
  if (state.selectedShopId !== null) {
    ...
    await loadShopDetail(state.selectedShopId);
  }
}
```
两次 fetch 串行，可 `Promise.all([fetch1, fetch2])` 并行（已知 selectedShopId 时）。
**Fix sketch**：
```js
const [resList, resDetail] = await Promise.all([
  callApi("/webui/api/shops", { action: "加载商店列表" }),
  state.selectedShopId !== null
    ? callApi("/webui/api/shops/" + state.selectedShopId, { action: "加载商店详情" })
    : Promise.resolve(null),
]);
```
注意要先校验 selectedShopId 仍在 list 里，避免无效 detail。
**Risk if unfixed**：UI 切换略慢，非阻塞。

### M-14 `renderShopList` / `renderShopDetail` 是全量重绘，频繁 click 时 DOM 抖动
**File**: `server/webui/static/js/shop.js:109-178, 180-215`
**Dimension**: perf
**Issue**：每点一次 shop card → `renderShopList()`（删全部 + 重建全部 card）+ `loadShopDetail()`（删全部行 + 重建）。N 个 shop / 多个 item 时 click 频繁会抖。但典型量级（10 家店、20 商品）影响微弱。
不主张 diff 框架化（servers / commands R1 已决策保留），只标 backlog。
**Fix sketch**：保留现状；如果未来 item 数 >100，考虑 patch update 而非 replaceChildren。
**Risk if unfixed**：性能成本可接受范围内。

### L-2 `Esc` 键监听是无脏检测的全局 listener
**File**: `server/webui/static/js/shop.js:835-839`
**Dimension**: perf（轻度内存 / 重复绑定风险）
**Issue**：`bindEvents()` 内 `document.addEventListener("keydown")` 没有解绑路径；shop 页是 SPA 子页（webui 主壳切换 tab 时），从 shop 切到 servers 不会 unbind——listener 累积。但 shop init 只跑一次（`if (document.readyState ...) init`），所以累积也只一次。
但**与 commands R2 B-4 的 modal stack + 单 ESC dispatcher 模式不一致**——shop 的 ESC 直接关闭**全部** modal（行 837：`document.querySelectorAll(".modal:not(.hidden)").forEach(...)`），即使 modal 嵌套（虽然 shop 暂时没有 nest 场景）也是一次 ESC 关全部。语义不同于 commands 的"关栈顶"。

**Fix sketch**：复用 commands 已建立的 modal stack（**scope-out backlog**：共享层），或者本页面接受批量关闭语义。当前没有 modal 嵌套场景，可保留。
**Risk if unfixed**：行为差异性，无功能问题。

### L-3 import 路径用 `FileReader.readAsText`，不限制文件大小
**File**: `server/webui/static/js/shop.js:621-666`
**Dimension**: perf
**Issue**：用户选 1 GB 文件 → 浏览器 OOM 后无降级。
**Fix sketch**：开头加 `if (file.size > 5 * 1024 * 1024) { showAlert(..., "文件超过 5MB"); return; }`。
**Risk if unfixed**：admin 误选大文件浏览器卡死，自损面。

### L-4 export 路径无超时 / abort 控制
**File**: `server/webui/static/js/shop.js:597-617`
**Dimension**: perf
**Issue**：`callApi(.../export, ...)` 走 api.js 默认 15s 超时（已 OK）。但生成 blob → `a.click()` → `URL.revokeObjectURL(url)` 同步进行，若导出 100 MB JSON，stringify 同步阻塞 UI。
**Fix sketch**：仅做大小提醒；正常规模 N MB 内可接受。
**Risk if unfixed**：admin 极大商店量级时浏览器卡顿。

---

## ③ UX

### M-15 删除商店模态文字未提及商品数量
**File**: `server/webui/templates/shop_content.html:248-253`
**Dimension**: ux
**Issue**：
> 确定删除商店「xxx」吗？该商店下的所有商品会一起被删除，此操作不可恢复。

未量化「所有商品」具体数字。state 里已有 `shop.item_count`（list 时拉到），但 confirm modal 没显示。
**Fix sketch**：`openShopDeleteModal` 多 set 一个 `shop-delete-item-count` span，template 加 "该商店下含 {N} 件商品..."。
**Risk if unfixed**：admin 误删时不知规模。

### M-16 删除商品 / 删除商店模态无焦点陷阱、无 previousFocus 恢复
**File**: `server/webui/static/js/shop.js:46-58, 401, 570`
**Dimension**: ux（accessibility）
**Issue**：`showModal` 只 `classList.remove("hidden")`，**未**：
- 把焦点移到 modal 内
- 锁定 Tab 在 modal 内（无 focus trap）
- 关闭时 restore previousFocus

对比 commands R1 P2-T1（focus trap）+ R2 B-3（previousFocus fallback + tabindex on native button），shop 完全没建。
**Fix sketch**：复用 commands 的 `openModalWithFocus` / `closeModalAndRestoreFocus` 模式（**跨模块 backlog**：抽到 webui.js / api.js），或本页内直接实现：
```js
function showModal(modal) {
  modal._previousFocus = document.activeElement;
  modal.classList.remove("hidden");
  const focusable = modal.querySelector("input, button, select, textarea, [tabindex]:not([tabindex='-1'])");
  if (focusable) focusable.focus();
}
function hideModal(modal) {
  modal.classList.add("hidden");
  if (modal._previousFocus && typeof modal._previousFocus.focus === "function") {
    modal._previousFocus.focus();
  }
}
```
**Risk if unfixed**：键盘用户体验差，与 commands 不一致。

### M-17 ESC 关闭模态时不区分"保存中"——可能在导入飞行期间关闭模态丢交互
**File**: `server/webui/static/js/shop.js:835-839, 710-731`
**Dimension**: ux
**Issue**：与 H-3 联动。导入飞行期间按 ESC → modal 立即 `hidden`，但 fetch 仍在跑。用户看到 modal 关闭以为取消，实际后端继续处理（特别 replace_all）。
**Fix sketch**：H-3 修复后顺带：`importing` 状态期间 ESC 拦截（与 commands B-7 aliasSaving guard 同模式）。
**Risk if unfixed**：导入未完成被误以为取消，影响信任。

### M-18 toast 文案违反 CLAUDE.md「不含操作对象名」
**File**: `server/webui/static/js/shop.js:613, 634, 638, 642, 646, 663, 726`
**Dimension**: copy
**Issue**：前端 hard-coded 文案违反共享文档「动作 + 结果」「不含对象名」：
- 行 613：`showAlert(els.alert, "导出成功", "success");` ✅ 合规
- 行 634：`api.buildActionFailureMessage("导入", "文件不是有效的 JSON")` ✅ → 实际生成 "导入失败，文件不是有效的 JSON" 合规
- 行 642：`...("导入", "kind 必须为 shops")` ✅
- 行 646：`...("导入", "version 必须为 1")` ✅
- 行 726：`showAlert(els.alert, "导入成功", "success");` ✅ 合规

**结论复审**：经逐行核对，**shop.js 的成功 / 失败文案全部合规**（"导出成功"、"导入成功" 都符合「动作 + 结果」，不含 "商店 / 商品" 等对象名）。失败路径全走 `buildActionFailureMessage(action, reason)` 由 api.js 统一拼装为「{action}失败，{reason}」，reason 是 error.message 原样透传。

**但**后端的 `error.message` 还是有合规风险：
- `webui_shop.py:340`：`"参数校验失败"` ✅
- `webui_shop.py:348-349`：`"商店名称已存在"` ⚠️ **含对象名**「商店」
- `webui_shop.py:640`：`"商店不存在"` ⚠️
- `webui_shop.py:676`：`"商店不存在"` ⚠️
- `webui_shop.py:681-682`：`"商店名称已存在"` ⚠️
- `webui_shop.py:707`：`"商店不存在"` ⚠️
- `webui_shop.py:736`：`"商店不存在"` ⚠️
- `webui_shop.py:798`：`"商品不存在"` ⚠️
- `webui_shop.py:840`：`"商品不存在"` ⚠️
- `webui_shop.py:458`：`"mode 必须为 merge 或 replace_all"` ✅

CLAUDE.md 规则原文：**「前端面向用户展示的操作结果文案（toast、message、modal、页面内反馈等）必须由前端生成」**。后端 `error.message` 是 **API 原始 reason**，按规则 7 应「仅返回有效原因」——"商店名称已存在" 是合法原因（用户需知道冲突字段），"商店不存在" 同理。配合前端 buildActionFailureMessage("保存商店", "商店名称已存在") → "保存商店失败，商店名称已存在"。

⚠️ **真正违规之处**：前端 `action` 字段含对象名：
- `shop.js:370`：`action: "新建商店"`
- `shop.js:377`：`action: "保存商店"`
- `shop.js:413`：`action: "删除商店"`
- `shop.js:532`：`action: "新建商品"`
- `shop.js:541`：`action: "保存商品"`
- `shop.js:584`：`action: "删除商品"`
- `shop.js:68`：`action: "加载进度选项"` ✅（"加载" + "进度选项"，对象就是进度选项本身，无对象名重复）
- `shop.js:72`：`action: "加载服务器列表"`（同上 ✅）
- `shop.js:79`：`action: "加载商店列表"` ⚠️
- `shop.js:99`：`action: "加载商店详情"` ⚠️
- `shop.js:600`：`action: "导出"` ✅
- `shop.js:721`：`action: "导入"` ✅

CLAUDE.md 规则原文（强制约束 1）：「**不得包含操作对象名称。动词后直接接"成功/失败"，不要拼接业务名词（服务器、用户、订单、文件等）**」。所以："新建商店" 应改 "新建"，"保存商店" 应改 "保存"，"删除商店" 应改 "删除"，"新建商品" 应改 "新建"，"保存商品" 应改 "保存"，"删除商品" 应改 "删除"，"加载商店列表" 应改 "加载"，"加载商店详情" 应改 "加载"。

**Fix sketch**：
```js
action: "新建商店" → "新建"
action: "保存商店" → "保存"
action: "删除商店" → "删除"
action: "新建商品" → "新建"
action: "保存商品" → "保存"
action: "删除商品" → "删除"
action: "加载商店列表" → "加载"
action: "加载商店详情" → "加载"
// 后端 error.message 保留：商店名称已存在 / 商店不存在 / 商品不存在 → 是 reason 字段，不算前端展示对象名
```
（后端 message 的修改属于 reason 不应拼接"商店"/"商品" 的边界讨论；个人判断保留 OK——这是必要的语境信息「哪种资源不存在」。）
**Risk if unfixed**：toast 形如 "新建商店失败，xxx"——重复对象名，违反规则 1。

**这条 finding 严重度评估**：copy 类问题但**全 webui 项目级一致性问题**——servers / commands / lottery 等其他模块大概率有同类，本桶 scope 内的就是这 6 处 action。

### M-19 删除按钮没用 `aria-label` / 删除 modal 缺 `aria-describedby`
**File**: `server/webui/templates/shop_content.html:238-282, 159-167, 324-329`
**Dimension**: ux (a11y)
**Issue**：删除 modal 已有 `aria-labelledby="shop-delete-title"` / `role="dialog"` / `aria-modal="true"`，但描述段（"确定删除商店「xxx」吗..."）没用 `aria-describedby`，屏幕阅读器只读标题不读描述。
**Fix sketch**：modal-body 加 `id="shop-delete-desc"`，dialog 加 `aria-describedby="shop-delete-desc"`。
**Risk if unfixed**：a11y 体验残缺。

### M-20 modal mask 点击关闭 + ESC 关闭 + 表单未提交时无脏检测
**File**: `server/webui/templates/shop_content.html:96, 134, 239, 263, 286` + `shop.js:826-832, 835-839`
**Dimension**: ux
**Issue**：点 mask / 按 ESC 直接关闭，不论用户是否已修改表单。commands R2 / servers R1 都没要求脏检测（无此约束），但商店 / 商品 modal 字段多（item modal 14 个字段），admin 编辑了一半误点 mask 全丢——风险面较高。
**Fix sketch**：可选 `dirty` flag——首次 input change 后置 true；关闭时若 dirty 弹二次确认。**非阻塞，仅建议**。
**Risk if unfixed**：admin 数据丢失风险（误关），但有"取消"按钮明显标识；保留现状也合理。

### L-5 actual_value 字段 placeholder 文案小问题
**File**: `server/webui/templates/shop_content.html:196`
**Dimension**: copy
**Issue**：placeholder 为 "留空 = 单价 / 单份数量"——用户不熟悉单价 / 单份数量含义时存疑。`field-section-hint`（line 199）已解释，重复信息可保留。OK。
**Risk if unfixed**：N/A，可忽略。

### L-6 import modal "全量替换" 警告文案以 emoji ⚠️ 开头
**File**: `server/webui/templates/shop_content.html:309`
**Dimension**: copy
**Issue**：项目 CLAUDE.md 规则 "Only use emojis if the user explicitly requests it" 是给 agent 的，不约束业务代码。HTML 里用 ⚠️ 通用网页设计可接受。但项目其他 webui 模块的 hint 多数纯文字。保持视觉一致性可考虑去 emoji。
**Fix sketch**：可选 `<strong style="color:var(--danger);">注意：</strong>` 替代 emoji。
**Risk if unfixed**：视觉一致性微问题。

### L-7 import 文件选择后 `input.value = ""` 在 cancel 时也清空
**File**: `server/webui/static/js/shop.js:625`
**Dimension**: ux
**Issue**：`handleImportFileChosen` 行 624-625 先 reset input value 才校验 file。注释 "Reset the input value so the same file can be reselected if user closes the modal"——OK。但用户在弹出文件选择器后按 Cancel，input 触发 change 是 false 路径（不进 handler）。这条没问题，注释意图正确。OK，无 bug。
**Risk if unfixed**：N/A。

### L-8 item modal 表单 `<label class="form-checkbox-row">` 文案过长
**File**: `server/webui/templates/shop_content.html:201-202, 220-221, 223-225`
**Dimension**: copy
**Issue**：盲盒、展示命令、要求在线 checkbox 描述非常长（30+ 字），label 包裹 input + 长文本——使整个文本可点击切换 checkbox，长文本误点率高。
**Fix sketch**：把"默认关闭"/"默认隐藏"等元信息抽到 `field-section-hint` 单独行。
**Risk if unfixed**：UX 微问题。

### L-9 `item-field-actual-value` placeholder 与字段意义需要上下文
**File**: `server/webui/templates/shop_content.html:196`
**Dimension**: copy（与 L-5 同）
**Issue**：见 L-5。
**Risk if unfixed**：见 L-5。

### L-10 shop.css 中存在死代码（`.kind-coin-pos` / `.kind-coin-neg` / `.weight-chip`）
**File**: `server/webui/static/css/shop.css:347-355, 311-313, 357-367`
**Dimension**: perf（轻度）
**Issue**：shop.js 里完全没用 `.kind-coin-pos` / `.kind-coin-neg` / `.weight-chip` / `.weight-chip.is-default` 这些 class。grep 4 个 source 文件 0 引用。来源可能是 lottery 共享样式遗留。
**Fix sketch**：清理 4 个未使用 selector，或注释说明用途。
**Risk if unfixed**：CSS 文件 ~30 行死代码，无功能问题。

### L-11 `.shop-item-table` 在窄屏（<880px）依赖横向滚动，搜索 / 过滤不存在
**File**: `server/webui/static/css/shop.css:382-422`，`shop_content.html:70-85`
**Dimension**: ux
**Issue**：item table `min-width: 880px;` 强制横向滚动；表头 7 列在 1080px 以下用户会丢失视觉范围。同时 item 数多时**无搜索 / 过滤 / 分页**——10+ shop 含上百 item 时定位低效。
- 与 commands 页面对比，commands 已有搜索 + 分页（commands.js 多处）。
- 与 servers 也一致有 search。
**Fix sketch**：长期 — 在 detail panel head 加一个 `<input>` 搜索 item.name；当 items.length > 20 时启用。属于较大改造，可 backlog。
**Risk if unfixed**：item 多时不易管理；规模上去前可接受。

---

# 三、跨模块发现（scope-out backlog）

| ID | 描述 | 推荐归属 |
|---|---|---|
| **SO-1** | webui 全局无 CSRF token / SameSite=Lax cookie 仅依赖浏览器层防御 | servers A-3 已记录，统一中间件层 backlog |
| **SO-2** | FastAPI 全局无 request body size middleware | commands H-B1 已记录 |
| **SO-3** | server delete 时不级联 ShopItem.target_server_id（M-9 跨模块部分） | servers DELETE 改造 backlog |
| **SO-4** | modal focus trap / previousFocus / body scroll lock 应抽到 webui.js 共享层 | commands B-3/B-12 已建模式，shop 复用 backlog |
| **SO-5** | 全局 logger helper（client_ip / user_agent）应抽公共 module | commands M-B3 backlog |
| **SO-6** | shop runtime（`nextbot/plugins/shop.py`）`command_template.replace("{player}", ...)` 对 player_name 特殊字符无 escape | shop 插件层 backlog（与 M-1 联动） |

---

# 四、复审建议执行顺序

1. **H-1 logger 上下文**：一次性补 8 处，机械改造，最低风险。
2. **H-3 in-flight guard**：前端 5 处 submit / confirm，5 个 boolean flag + try/finally，影响面收口。
3. **H-2 import audit + race**：先补 logger.warning + 数量上界（M-7），CSRF 跨模块标 backlog。
4. **M-15 ~ M-20**：UX 类批量改，影响低。
5. **M-2 / M-3**：经济边界小修。
6. **M-8 / M-10**：API 严格性 + 备份重放警示。
7. **L-1 ~ L-11**：批量低优清扫。

---

# 五、Caveats

- 本审计严格限于 4 个 shop 文件，未交叉走读 `webui.py` 中间件、`api.js`、shop 插件 runtime 与 economy 插件——这些都已在 servers / commands prior audit 中处理过且与 shop 同一项目惯例。
- 文案合规判定基于 `/Users/arispex/.claude/CLAUDE.md` 的「用户操作反馈文案规范」当前版本（2026-05-15）。
- 严重度判定参考了 servers R2 / commands R3 的标准，遵循「同等行为同等评级」。
- 0 Critical 不代表绝对零风险，仅表示无即时 RCE / 凭据泄漏 / 直接经济损失类问题。H-2 的 replace_all 是 "破坏性 admin 操作 + 缺乏 CSRF/幂等" 组合，已是项目共享层 backlog 不应在本桶单点修复。
