# Audit: WebUI 抽奖（lottery）页面

- **Query**: 全量审计 WebUI lottery 页面（4 文件，~2750 LOC）
- **Scope**: 仅以下 4 个文件，跨模块发现一律标注 `scope-out backlog`
  - `server/routes/webui_lottery.py`（853 LOC）
  - `server/webui/templates/lottery_content.html`（328 LOC）
  - `server/webui/static/js/lottery.js`（854 LOC）
  - `server/webui/static/css/lottery.css`（720 LOC）
- **Date**: 2026-05-15

> 参考：`add_webui_auth_middleware`（`server/routes/webui.py:195-226`）已为整个 `/webui/api/*` 注入 401 守卫；CSRF 当前全局缺失（与 servers / commands 历史结论一致，列为 scope-out）。实际抽奖随机性 / 库存扣减发生在 `nextbot/plugins/lottery.py`，**不在本次审计 4 文件范围内**，相关疑虑也以 scope-out 记录。

---

## 计数总览

| 严重度 | 数量 | 编号 |
|---|---|---|
| Critical | 1 | C-1 |
| High | 6 | H-1 ～ H-6 |
| Medium | 11 | M-1 ～ M-11 |
| Low | 9 | L-1 ～ L-9 |
| **合计** | **27** | |
| scope-out backlog | 5 | S-1 ～ S-5 |

### Top 3 highest-severity（按风险）

1. **C-1**：`weight=null` 自动平分逻辑可被一个 `weight=100` 的 prize 把 `remaining` 全部吃光，前端展示「默认 0.00%」而 **后端真正抽奖的权重语义却完全不一样**（业务公平性歧义） — `lottery.js:60-74`、`webui_lottery.py:154-163`。
2. **H-1**：`/webui/api/lottery/import` 全量替换（`mode=replace_all`）会先 `DELETE LotteryPrize / LotteryPool`，**完全没有人机确认 token / 二次校验**，前端只在弹窗里加文案提示就让用户一键覆盖全部数据，且整个失败回滚依赖单条 `session.commit()` — `webui_lottery.py:552-555`、`lottery.js:711-732`。
3. **H-2**：`command_template` 在 prize 详情页直接渲染并用 `title` 显示，包含 `{player}` 占位符 — **未做命令安全白名单 / 注入限制**（如 `op /` `ban` `stop` 等极权命令），抽奖落地阶段会按字面拼接执行（落地代码不在本审范围，但 WebUI 是唯一录入端，应增加录入侧告警 / 校验） — `webui_lottery.py:228-235`、`lottery_content.html:220-223`。

---

## 详细发现

### Critical

#### C-1 概率「默认平分」语义在前后端不对齐，可导致显示概率与实际抽奖概率不一致
**File**: `server/webui/static/js/lottery.js:60-74`，`server/routes/webui_lottery.py:154-163`、`51-56`
**Dimension**: ux / security（业务公平性）
**Issue**:
- 后端 `_validate_prize_payload`（`webui_lottery.py:154-163`）把 `weight` 校验在 `[0, 100]`，允许 `None`（NULL = 分剩余），但 **没有任何「同一奖池内已设置权重之和 ≤ 100」的约束**。换言之，可以创建：
  - prize-A weight=80, prize-B weight=50（已设置和=130，超 100），后端通过保存；
  - 此时前端 `resolveProbabilities`（`lottery.js:60-74`）把 setSum=130 截到 100，导致 A、B 的展示概率仍按 80/50 截到 80/50（但实际相加 = 130/100 > 1），`unset` 数组的 `perUnset = max(0, 100-100)/n = 0`，前端展示 `0.00%（默认）`。
- 真正在 `nextbot/plugins/lottery.py:148` 是 `random.uniform(0, 100)` 累加权重抽样。这意味着「设置权重之和」≠100 时，**实际命中概率与 WebUI 展示完全不一致**（玩家被宣告 0% 的奖项，实际可能命中；展示 50% 的奖项，实际可能 ~38.5%）。
- 这是「财务公平性」级别的歧义：管理员根据 WebUI 配置出来的概率与玩家拿到的实际中奖概率会偏离。
**Fix sketch**（仅本次范围内可做的录入侧约束）：在 `_validate_prize_payload` 末尾增加跨奖池校验：保存 / 更新单条 prize 时，查询同 pool 的其他 enabled prize 已设置 weight 之和（排除当前 id），若 `已设置和 + 本次 weight > 100` 则返回 422；同时前端在保存按钮可禁用前先做一次本地总和提示（不替代后端校验）。或至少在 `_serialize_pool` 暴露 `set_weight_sum`，让 JS 在表格底部标红警告。
**Risk if unfixed**：玩家通过命令客户端体验到的中奖率与管理后台显示不符 → 客诉 / 公平性投诉 / 法律风险（含金币奖品时尤甚）。

---

### High

#### H-1 `replace_all` 导入操作没有强人机确认，且失败可能部分落地
**File**: `server/routes/webui_lottery.py:552-622`、`server/webui/static/js/lottery.js:711-732`
**Dimension**: security / ux
**Issue**:
- 后端 `mode=replace_all` 路径先 `session.query(LotteryPrize).delete()` 再 `LotteryPool.delete()`，然后插入新数据。整个流程依赖 `session.commit()` 末尾原子提交。但 **没有任何二次确认凭证**（例如 expected_count / signed hash / "我确认删除 N 个奖池" 文本输入），仅靠前端 modal 文案兜底。任何被劫持的 fetch / 错误绑定（H-3）都能一键清库。
- 前端 `confirmImport`（`lottery.js:711-732`）也只发送 `mode=replace_all`，不要求二次输入。
- 校验：`structural`（`webui_lottery.py:463-480`）已 fail-fast；`aggregated`（485-543）逐 pool 校验后再统一返回。但 `replace_all` 在校验 PASS 后仍可能因 `LotteryPrize` 插入异常（例如 `weight=NaN` 通过了校验、DB 报错）回滚到「奖池被删了一半」之前。代码 `except: rollback()` 实际能撤销（SQLAlchemy 单事务），但 **`logger.info` 的成功日志在 commit 后才打，无审计能力定位失败原因**。
**Fix sketch**：在 `confirmImport` 路径，`replace_all` 模式额外要求用户在 modal 中键入「全量替换」四个汉字才启用按钮（前端校验 + 后端可选透传 `confirm=true` 一并校验）。后端额外补 `logger.warning("WebUI 奖池 import：replace_all triggered ...")` 含 `client_ip` / `user_agent`，与 `servers` 模块 H-1 类比。
**Risk if unfixed**：误点 / XSS 利用 / 链接钓鱼一键清空全部奖池配置。

#### H-2 `command_template` 缺白名单 / 危险命令校验，WebUI 是唯一录入端
**File**: `server/routes/webui_lottery.py:216-238`，`server/webui/templates/lottery_content.html:220-223`
**Dimension**: security
**Issue**:
- `_validate_prize_payload` 对 `command_template` 仅校验长度 ≤ `_CMD_MAX_LEN=500` 和非空，未做 **任何命令前缀白名单 / 黑名单**。这意味着管理员可以将 `op {player}` / `deop {player}` / `stop` / `ban {player}` / `say <任意 chat 注入>` 录入为抽奖奖品；抽奖命中后这条命令会被 `nextbot/plugins/lottery.py` 通过 RCON 直发到 MC 服务器。
- 前端 `prize-field-command-template` placeholder 只是 `/buff {player} 1 1800`，无任何输入侧危险关键字提示。
- 同时 `command_template` 在表格中通过 `cmdPreview.textContent = prize.command_template` 渲染（`lottery.js:313-314`）— XSS 已用 textContent 防护，**但日志注入仍可能**：`logger.info(... name={prize.name} kind={prize.kind} weight={prize.weight})`（`webui_lottery.py:770-773、828-831`）。若管理员把 `name` 设为 `\n[ERROR] 假错误`，日志会被注入伪行 — 见 L-2。
**Fix sketch**：在 `_validate_prize_payload` `kind == "command"` 分支增加可配置黑名单（如 `op` / `deop` / `stop` / `restart` / `ban` / `pardon` / `whitelist` 等），命中时返回 422，附 `details=[{"field": "command_template", "message": "命令前缀 op 不允许作为抽奖奖品"}]`。同时模板增加 `field-section-hint` 提示「禁止录入 op/stop/ban 等高权命令」。
**Risk if unfixed**：低权管理员账号被钓鱼 / 凭证泄漏后，可借抽奖一键拉高任意玩家权限 / 关停服务器。

#### H-3 删除奖池后没有审计日志（缺失 `client_ip` / `user_agent`），与 servers / commands R1+R2 已修标准不对齐
**File**: `server/routes/webui_lottery.py:706-721`、`722-781`、`837-853`
**Dimension**: security
**Issue**:
- `delete_pool` / `create_prize` / `update_prize` / `delete_prize` 仅 `logger.info(...)` 不带 `client_ip` / `user_agent`（对照 `server/routes/webui_servers.py:160` / `223` / `483` 等已统一注入）。
- 抽奖配置直接影响玩家奖励 / 金币 / 命令执行，事后追溯非常重要。当前任何账号操作都无法定位"是谁、从哪个 IP 改的"。
- 同时 `list_lottery_tiers`（289）、`list_lottery_servers`（294）、`list_pools`（306）、`get_pool`（636）等只读路径完全没有日志，至少 import / delete / replace_all 应纳入 WARN 级。
**Fix sketch**：复用 `server/routes/webui_servers.py:_client_ip` helper（已经被 commands R2 / servers R1 复用，单向依赖无循环）。给所有 state-changing 路径（POST/PUT/DELETE/import）补 `client_ip = _client_ip(request)` 和 `user_agent = request.headers.get("user-agent", "")[:200]`，replace_all / delete_pool 等高危操作用 `logger.warning`。
**Risk if unfixed**：被入侵 / 误操作后无可追溯证据；与 R1+R2 已定标准不一致，等于本模块「日志合规债」。

#### H-4 `weight` 校验存在赋值-检查时序错误，超界值仍可能通过
**File**: `server/routes/webui_lottery.py:154-163`
**Dimension**: security / 数据一致性
**Issue**:
```python
try:
    weight = float(raw_weight)
except (TypeError, ValueError):
    weight = -1.0
if weight is not None and (weight < 0.0 or weight > 100.0):
    details.append(...)
```
- `weight = float(raw_weight)` 接受 `"NaN"` / `"Infinity"` 等字符串 — `float("nan")` 返回 NaN，**`NaN < 0.0` 和 `NaN > 100.0` 都为 False**，于是 NaN 会绕过校验并保存到 DB。后续 `resolveProbabilities` 用 `Number(p.weight)` 累加，`setSum` 变 NaN，整个表格概率全显示 NaN%。
- 同理 `float("inf")` 会被 `weight > 100.0` 截住。但 NaN 是真实的逃逸路径。
**Fix sketch**：
```python
import math
...
try:
    weight = float(raw_weight)
except (TypeError, ValueError):
    weight = None
    details.append({"field": "weight", "message": "概率必须是数值"})
if weight is not None and (math.isnan(weight) or math.isinf(weight) or weight < 0.0 or weight > 100.0):
    details.append({"field": "weight", "message": "概率必须为 0-100 之间的数值"})
```
同时 `actual_value` / `coin_amount` 都是 `int(raw)`，`int` 不接受 NaN/Inf 字面量字符串，但接受布尔（`int(True)==1`），见 L-3。
**Risk if unfixed**：单条 NaN 权重把整个奖池前端展示破坏，并污染落地侧的 `random.uniform` 累加（业务公平性）。

#### H-5 `delete_pool` 不在事务中，且 LotteryPrize 表无外键级联，存在残留奖品风险
**File**: `server/routes/webui_lottery.py:706-721`，`nextbot/db.py:341-345`
**Dimension**: 数据一致性
**Issue**:
- `LotteryPrize.pool_id` 只是 `Integer + index`，**没有 ForeignKey 约束**（`nextbot/db.py:345`）。删除奖池路径 `session.query(LotteryPrize).filter(...).delete(synchronize_session=False); session.delete(pool); session.commit()` 虽然在同一 session 内，但若中途异常被 `finally session.close()` 静默吞掉，可能留下 **奖池没了、奖品孤立** 的脏数据（同样的孤立问题也会在 `update_prize` 校验 `pool_id` 不匹配时存在，因为 `pool_id` 来自路径参数而 prize 已存在）。
- `delete_pool` 没有 `try/except: session.rollback(); raise`，与 `import_lottery`（629-631）路径不一致。
**Fix sketch**：加 `try/except` 显式 rollback + raise，并在 catch 路径打 `logger.error` 带 `pool_id` / `client_ip`。中长期可在 `LotteryPrize.pool_id` 上加 `ForeignKey("lottery_pool.id", ondelete="CASCADE")` — 但建模变更属于 scope-out。
**Risk if unfixed**：偶发 DB 异常后表里多出无家可归的 LotteryPrize，玩家看不到（pool 不存在）但占容量 / 干扰审计。

#### H-6 `update_prize` 不要求路径中的 `pool_id` 与 prize 实际归属一致（已有），但删除 / 修改的 prize 可被「跨池删除」
**File**: `server/routes/webui_lottery.py:783-834`、`837-853`
**Dimension**: security / authz
**Issue**:
- `update_prize` / `delete_prize` 都 `filter(LotteryPrize.id == prize_id, LotteryPrize.pool_id == pool_id)`，**形式上正确**：错配 pool_id 会 404。
- 但 `create_prize`（724-781）的路径 `/webui/api/lottery/{pool_id}/prizes` 没有校验 `pool.enabled`；同时 `update_prize` 也允许把 prize 改成与路径 pool_id 不同的 `target_server_id`（target_server_id 校验仅检查 server 存在性，**不校验 target_server_id 是否在管理员权限范围内** — 本系统暂无分权，所以这个先记为 H 级别提醒）。
- 真正的 H 级问题：`update_prize` 接受完整 payload 覆盖（PUT 语义），如果前端漏传任一字段，会被默认值覆盖（例如 `is_mystery=False`、`coin_amount=0`、`actual_value=None`）。前端 `submitPrizeModal`（`lottery.js:507-561`）按 kind 只填该 kind 的字段，**切 kind 后 PUT 会把另一 kind 的旧字段清零**。这是有意的设计（kind 换了就重置），但 **没有 UI 警告**：用户从 item 改成 coin 保存后，原 item_id / quantity / actual_value 全部归零，不可恢复。
**Fix sketch**：前端在 `prize-field-kind` 的 `change` 事件中，若 `editingPrizeId !== null && original.kind !== newKind`，弹一个二次确认（或在 alert 区显示 warning：「切换类型会清空原有的 XX 配置，确定继续？」）。后端可选补 `logger.warning` 标记 kind 切换。
**Risk if unfixed**：管理员误操作清空已校对的物品 ID / 概率配置；不可逆。

---

### Medium

#### M-1 `cost_per_draw` 缺上界，可填 `2**31-1`
**File**: `server/routes/webui_lottery.py:110-118`，`server/webui/templates/lottery_content.html:117`
**Dimension**: ux / 数据
**Issue**: `cost_per_draw` 只要 `>= 0` 就放行。HTML `<input type="number" min="0">` 也无 max。允许填到 `2**31-1` 之后落地阶段做减法 / 显示会出 UI 截断；金币系统应有合理上限（如 10**8）。
**Fix sketch**：后端加上界（如 `1_000_000_000`），前端 `max="1000000000"`。
**Risk if unfixed**：UI 显示溢出 / 玩家结算溢出。

#### M-2 `quantity` / `prefix_id` / `item_id` 同样缺上界
**File**: `server/routes/webui_lottery.py:177-198`、`lottery_content.html:184-194`
**Dimension**: ux / 数据
**Issue**: `quantity >= 1` 无上界，`prefix_id >= 0` 无上界，`item_id >= 1` 无上界。中奖落地若 quantity 极大会导致 player 仓库一次性插入失败 / 后端 OOM（落地行为不在范围，但录入侧应防御）。
**Fix sketch**：后端 quantity 上界（如 9999）、item_id 与 prefix_id 校验为已存在（需要 Item / Prefix 表 - 跨模块，记为可选）。
**Risk if unfixed**：录入端缺约束，落地端被迫处理边界。

#### M-3 `coin_amount` 缺上下界，且 `0` 校验顺序错误时仍能通过
**File**: `server/routes/webui_lottery.py:240-247`
**Dimension**: 数据
**Issue**:
```python
try:
    coin_amount = int(data.get("coin_amount", 0))
except (TypeError, ValueError):
    coin_amount = None   # ← bug: 后面 coin_amount == 0 不再触发
    details.append({"field": "coin_amount", "message": "金币数量必须为整数（可正可负）"})
if coin_amount == 0:
    details.append({"field": "coin_amount", "message": "金币数量不能为 0"})
```
- 若 `int(...)` 抛错，`coin_amount = None`，下一行 `coin_amount == 0` 是 False，**只会有 1 条 details**（"必须为整数"）— 正确。但反过来，正常 `int("0")` 通过后 `coin_amount=0` 会触发 "不能为 0"，OK。
- 真正的问题：`int(True)` == 1，`int(False)` == 0；JSON 里若 client 误传 `coin_amount: false`（如某个布尔输入误用），会被当作 0 处理（不能为 0 触发 422）；但 `true` 会被当作 +1 金币奖品 — 数据失实。
- 同时上下界缺失，可填 `2**63-1`。
**Fix sketch**：先 `if not isinstance(raw, int) or isinstance(raw, bool): error`；加上下界（如 ±10**8）。
**Risk if unfixed**：极端值落地 / 类型耦合错误。

#### M-4 `sort_order` 无上下界，且 partial update 在 import 路径下永远是非 partial
**File**: `server/routes/webui_lottery.py:101-105`、`78-84`
**Dimension**: 数据
**Issue**: `sort_order` 仅 `int()`，可负 / 可极大 / 可被传 `2**63`。`_validate_pool_payload(..., partial=False)`（import 路径）若 `data` 没传 `sort_order` 会落到 `if "sort_order" in data` 分支 — 不报错，但 `"sort_order" not in validated` 会让 `pool_data.get("sort_order", 0)`（`webui_lottery.py:586`）兜底 0，**导入侧静默丢失原 sort_order**。
**Fix sketch**：上下界（±1_000_000），import 路径显式校验必填字段（与 create 一致）。
**Risk if unfixed**：导入后排序乱掉。

#### M-5 `name` 唯一性校验存在 TOCTOU
**File**: `server/routes/webui_lottery.py:344-349`、`680-686`
**Dimension**: security / 数据
**Issue**: `create_pool` / `update_pool` 检查 `existing` → 然后 `add` → `commit`。两个并发请求可能同时拿到 `existing=None` 然后都尝试 insert，**靠 DB unique 约束兜底**（`LotteryPool.name` 有 `unique=True`，`db.py:331`）。但是抛 IntegrityError 会被外层 fastapi 500 化，**返回给前端是泛化 500，而不是 409 duplicate_name**。
**Fix sketch**：`try: session.commit() except IntegrityError: session.rollback(); return api_error(409, "duplicate_name", ...)`。
**Risk if unfixed**：并发场景下用户拿到 500 而非清晰提示。

#### M-6 `import_lottery` `replace_all` 与 `existing_by_name` 的 merge 在同一函数内分支，DB 一致性边界靠人脑
**File**: `server/routes/webui_lottery.py:546-622`
**Dimension**: 数据
**Issue**:
- `replace_all` 路径在 553 `delete()` 之后才 `flush()`，然后 `existing_by_name = {}`（558-561 三元表达式）；
- merge 路径 559 单查 `query(LotteryPool).all()` 拉全表，对大数据集（虽然不多见）造成 N 次 select。
- 同名 pool 多次出现在 JSON 中：seen_names 校验在 506-512，OK。但 prize 内部没有 sort_order 重复 / id 重复校验（id 不在 export 中，OK），prize 名称重复也未校验 — 业务允许，但 UX 上可能误导。
- `existing.cost_per_draw = int(pool_data["cost_per_draw"])` 等都用 `if ... in pool_data`（568-575），但 `_validate_pool_payload(...)` 在 import 路径用默认 `partial=False`，所以 cost_per_draw 是必填。如果 JSON 文件来自旧版本（v0 没这个字段），整个 import 失败 — 兼容性差。
**Fix sketch**：在 422 错误里附迁移指引；增加 `version: 0 → 1` 的 lenient 兼容（旧字段 default 0）。
**Risk if unfixed**：跨版本 import 直接 422。

#### M-7 前端 `loadPools` 失败时 `state.selectedPoolId` 未清理，导致后续 race
**File**: `server/webui/static/js/lottery.js:89-107`
**Dimension**: ux
**Issue**: `callApi` 抛错走 `catch` 显示 alert，但 `state.pools` 保留旧值。用户点刷新失败 → 再点别的奖池卡片仍走旧数据；如果 selected pool 已被另一管理员删除，`loadPoolDetail` 会 404。前端没有针对 404 的"奖池被删了"专项提示。
**Fix sketch**：`catch` 内额外 `state.selectedPoolId = null; state.selectedPoolDetail = null; renderPoolDetail();`，让 UI 重置到空态。
**Risk if unfixed**：并发管理员场景下 UI stale。

#### M-8 前端无 fetch abort，快速切换奖池导致请求乱序
**File**: `server/webui/static/js/lottery.js:109-117`、`175-179`
**Dimension**: perf / ux
**Issue**:
- `loadPoolDetail` 直接 await fetch，**没有 AbortController**；快速点击不同 pool（A → B → A）时，请求顺序可能与点击顺序不一致，导致最后渲染的不是用户最后点击的 pool（commands R2 的 P1-Race 同模式已修，但 lottery 没采纳）。
- 同模式 `loadPools` 也没 abort，刷新按钮快连点会叠加请求。
**Fix sketch**：与 commands.js 一致，用 module-level `currentDetailController` / `currentListController`，新请求前 `abort()` 旧的。
**Risk if unfixed**：UI 闪烁 / 数据错位（低概率但管理员快速操作可复现）。

#### M-9 `formatProbabilityPct` 精度展示与后端 step="0.01" 不对齐
**File**: `server/webui/static/js/lottery.js:51-56`、`lottery_content.html:168`
**Dimension**: ux / 数据
**Issue**:
- HTML `step="0.01"`、`min=0 max=100`，但提交时 `Number(els.prizeFieldWeight.value)`（`lottery.js:519`）会把 `"0.1234"` 直接传给后端（前端 step 只是建议，不会拦截）。后端 `float(raw)` 接受任意精度。
- 展示侧 `formatProbabilityPct(v)`：两位四舍五入，等于一位则展示一位 — 边界 `99.995` → `100.00`（看起来满 100，实际未到），玩家可能产生误解。
**Fix sketch**：后端接收侧把 weight 强制 `round(weight, 4)`（保留 4 位足够覆盖 step=0.01 的所有合理输入），并写回 DB；前端在用户输入时 `Number.isFinite` + `Math.round(v*100)/100` 钳制。
**Risk if unfixed**：玩家看到「100.00%」 实际不是 100%。

#### M-10 `applyKindVisibility` 切 kind 时未清空对应字段，提交后 `_validate_prize_payload` 各分支独立校验，但落地侧（不在范围）拿到的对象会带"上一个 kind 的"脏字段
**File**: `server/webui/static/js/lottery.js:468-473`、`507-538`
**Dimension**: 数据
**Issue**:
- `submitPrizeModal` 只填当前 kind 对应的字段进 payload，其他 kind 字段不传。后端 `_validate_prize_payload` 在 `item` 分支才检查 `item_id`，其他 kind 时直接 `item_id=0`、`quantity=1`。看起来 OK。
- 但 PUT 之后 DB 里 `item_id=0` / `quantity=1` 等成了"已写入"状态。前端编辑同一 prize 时，`openPrizeModal` 把这些值回填到 hidden 的 item 分区。一旦再切回 item kind，发现"原 item_id 居然是 0" — 容易让用户以为是 bug。
**Fix sketch**：`applyKindVisibility` 切换时，把非当前 kind 的 input value 重置为 placeholder/默认（与新建语义一致）。
**Risk if unfixed**：编辑混淆。

#### M-11 删除确认 modal 在删除失败后没有重置 `pendingDeletePool`，会卡死
**File**: `server/webui/static/js/lottery.js:425-443`、`581-596`
**Dimension**: ux
**Issue**:
- `confirmDeletePool` `catch (err) { showAlert(...) }` — 此时 `state.pendingDeletePool` 还在，modal 仍可见（因为没 `hideModal`）。用户再次点"删除"按钮，仍走同一对象。OK。
- 但若用户改主意点"取消"（`data-modal-close`）关闭 modal，`pendingDeletePool` 没清空。下一次再点列表里其它奖池的"删除"，`openPoolDeleteModal(pool)` 会用新值覆盖，OK。
- 真正问题：失败状态下 ESC 关闭 modal（`bindEvents` 833-837 的全局 ESC dispatcher），`pendingDeletePool` 仍残留 — **如果中途用户切换页面 / 触发 hash 变化（不会，单页静态），残留对象会占内存。** 影响极小但与 commands R2 已修的 modal stack 不一致。
**Fix sketch**：在全局 ESC dispatcher 中 `state.pendingDeletePool = state.pendingDeletePrize = state.pendingImport = null`；或为每个 modal 单独注册 close callback。
**Risk if unfixed**：极弱内存 / 状态卫生问题。

---

### Low

#### L-1 `formatProbabilityPct(0)` 返回 `"0.0"`，prize 默认为 `0.0%（默认）`，但弹窗 placeholder 写「留空 = 自动平分剩余概率」— 当 setSum=100 时 unset prize 展示 `0.0%（默认）`，**容易被理解为禁用**
**File**: `server/webui/static/js/lottery.js:51-74`、`lottery_content.html:168`
**Dimension**: ux / copy
**Fix sketch**：当 `perUnset == 0` 且存在 unset 时，weight-chip 显示 `0.00%（剩余概率不足，请下调其他奖品）` 警示文案。
**Risk if unfixed**：轻微误导。

#### L-2 logger 消息使用 `f"... name={pool.name}"` 等格式，pool/prize 名包含换行 / `[ERROR]` 可注入日志
**File**: `server/routes/webui_lottery.py:360`、`700`、`770-773`、`828-831`、`432-435`、`619-622`
**Dimension**: security / observability
**Issue**: `name` 校验只对 length / strip，没禁换行 / 控制字符。`logger.info(f"WebUI 奖池 create：pool_id={pool.id} name={pool.name}")` 中 name=`"abc\n[ERROR] hacked"` 会在日志文件里伪造一条 ERROR 记录。
**Fix sketch**：`_validate_pool_payload` / `_validate_prize_payload` 在 name / description / command_template 内 reject `\r\n\t`（或 replace 为空）；logger 调用统一用 `name=name!r`（已习惯）。
**Risk if unfixed**：日志被污染，事后审计困难。

#### L-3 整型字段对 bool 兼容（`int(True)==1`），可能被 JSON 中的 `true` 当 1 处理
**File**: `server/routes/webui_lottery.py:111-118`、`146-149`、`177-198`、`240-244`
**Dimension**: 数据
**Issue**: 同 M-3 但更通用；`bool` 是 `int` 的子类。
**Fix sketch**：统一 helper `_strict_int(raw, field, *, allow_negative=False)` 拒绝 bool。
**Risk if unfixed**：极少触发。

#### L-4 `_serialize_prize` 在 `prize.weight is None` 时返回 `None`，前端展示用 `(prize.weight !== null && prize.weight !== undefined)` 双重判断 — 字符串 `""` 在 export JSON 重新导入路径会被当作 `null` 还是 `0`？
**File**: `server/routes/webui_lottery.py:46-67`、`154-163`
**Dimension**: 数据
**Issue**: `raw_weight === ""` → `weight = None`；`raw_weight === "0"` → `weight = 0.0`。两者语义不同（None=分剩余，0=锁定 0% 不抽中）。前端 placeholder 文案明确了 "留空 = 自动平分剩余概率"，OK。但 export 里 weight=0.0 与 weight=null 都是合法值，**人肉编辑 JSON 时容易把 0.0 误写成 null**。
**Fix sketch**：export 里 `null` 用注释或额外提示性字段；import 422 错误里加上下文。
**Risk if unfixed**：极少。

#### L-5 服务器列表 `/webui/api/lottery/meta/servers` 把全部服务器名暴露给所有 webui 登录账号
**File**: `server/routes/webui_lottery.py:294-303`
**Dimension**: security
**Issue**: 在多管理员 / 分权场景下，本应只暴露当前管理员有权管理的 server，但此 endpoint 直接 `query(Server).all()`。本系统暂无分权，列为低。
**Fix sketch**：等多管理员功能上线时再处理。
**Risk if unfixed**：当前 N/A。

#### L-6 `lottery.js` 缺 `beforeunload` abort（参考 commands R2 B-2 已修）
**File**: `server/webui/static/js/lottery.js`
**Dimension**: perf
**Issue**: 与 commands.js 修复后的统一模式不一致。无 inflight abort 在离开页面时会延后释放资源。
**Fix sketch**：`window.addEventListener("beforeunload", () => { controllers.forEach(c => c.abort()); })`。
**Risk if unfixed**：极少。

#### L-7 modal `setTimeout(() => focus(), 30)` 与 commands R2 `openModalWithFocus` 标准不一致；focus restore 未实现
**File**: `server/webui/static/js/lottery.js:369`、`499`
**Dimension**: ux / a11y
**Issue**: 打开 modal 前没有保存 `previousFocus`，关闭后没 restore；键盘用户操作流被打断。commands R2 B-3 已修这套，lottery 未对齐。
**Fix sketch**：抽象 `openModalWithFocus(modal, firstFocusable)` + `previousFocus = document.activeElement; modal.close → previousFocus?.focus()`。
**Risk if unfixed**：a11y / 键盘流畅度。

#### L-8 `lottery.css:701-720` 响应式 max-width 1080px 阈值与其他模块不一致
**File**: `server/webui/static/css/lottery.css:701-720`
**Dimension**: ux
**Issue**: 与 commands / servers 不在同一断点（侧边栏宽度 320px 在该阈值下变 1fr）。视觉规范化问题。
**Fix sketch**：与 servers / commands 对齐。
**Risk if unfixed**：极弱视觉一致性。

#### L-9 toast / alert 文案部分含「对象名」违反全局规范
**File**:
- `lottery.js:616` `"导出成功"` — OK
- `lottery.js:727` `"导入成功"` — OK
- `lottery.js:618` `err.message || "导出失败"` — OK（兜底文案符合规范）
- `lottery.js:106` `err.message || "加载失败"` — OK
- `lottery_content.html:262-264` `"确定删除奖池「..."` — 业务确认弹窗里允许出现对象名，**符合规范的「确认对话框」例外**（toast/alert 才禁止）。
- 但 `webui_lottery.py:347` `"奖池名称已存在"` / `685` `"奖池名称已存在"` 是后端 error.message — 用户全局规则规定后端 error.message 应仅返回原因，不拼接动作 + 结果。此处只有原因，**OK**。
**Dimension**: copy
**Issue**: 仅 `webui_lottery.py` 在 `code="duplicate_name"` 时附带 details `{"field": "name", "message": "奖池名称已存在"}` — message 与 top-level message 重复，前端拼接出 `保存失败，奖池名称已存在；奖池名称已存在`（`api.js:62-72` 的 `buildDetailReason` 用 `；` 拼接）。
**Fix sketch**：后端 details message 改为 `"该名称已被其他奖池占用"` 区分；或前端在 `buildDetailReason` 跳过与 top-level 完全相同的项。
**Risk if unfixed**：toast 出现重复内容，体验略 awkward。

---

## scope-out backlog（跨模块，不计入本任务严重度）

- **S-1** 抽奖随机性源（`nextbot/plugins/lottery.py:148` `random.uniform`）— Python `random` 是 Mersenne Twister，对"金币奖品 / 真实经济价值"场景应改用 `secrets.SystemRandom().uniform`，避免可预测。但此文件不在本审范围。
- **S-2** 概率「设置和 > 100」的硬约束是否应该在 DB 触发器 / model 层（`nextbot/db.py:341`）？同样跨模块。
- **S-3** CSRF：整个 `/webui/api/*` POST/PUT/DELETE 缺 CSRF token（与 servers / commands 历史结论一致，未在任一已修任务中处理）。建议在 `add_webui_auth_middleware` 同层增加 sameSite=Strict cookie + double-submit token。
- **S-4** `LotteryPrize.pool_id` 缺 ForeignKey 与 `ondelete=CASCADE`（`nextbot/db.py:345`），建模变更。
- **S-5** `command_template` 占位符语义（`{player}`）由 `nextbot/plugins/lottery.py` 落地处理，本审范围内只能做录入侧校验；命令真正执行链路的注入面（参数转义 / 引号 / shell metacharacters）应在落地侧另开任务。

---

## 备注

- 本审 27 条仅基于 4 文件 ~2750 LOC 静态阅读 + 与 servers R1+R2 / commands R3 已修标准对照得出，没有运行测试。
- C-1（概率歧义）和 H-2（命令注入面）是与「公平性 / 安全性」直接相关的 2 个最高优先级；H-1（一键清库）属于操作风险。
- H-3（日志审计缺失）是与 servers / commands 已修标准的硬对齐项，推荐与 C-1 / H-1 同 PR 合入。
