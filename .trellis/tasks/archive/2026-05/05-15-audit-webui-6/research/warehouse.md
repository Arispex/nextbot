# Research: WebUI 仓库（warehouse）页面安全 / 性能 / UX / 文案审计

- **Query**: 审计 webui warehouse 页面 4 文件，输出 finding 报告
- **Scope**: internal（严格限定 4 文件）
- **Date**: 2026-05-15

## 严格 scope（仅这 4 文件）

| 文件 | LOC |
|---|---|
| `server/routes/webui_warehouse.py` | 235 |
| `server/webui/templates/warehouse_content.html` | 121 |
| `server/webui/static/js/warehouse.js` | 523 |
| `server/webui/static/css/warehouse.css` | 497 |

跨模块怀疑 → 末尾 `scope-out backlog`。

## 总览：finding 数（按严重度）

- **Critical**: 0
- **High**: 4
- **Medium**: 11
- **Low**: 9
- **合计**: 24

### Top 3 最高严重项

1. **H-1**：`upsert_slot` / `delete_slot` 路径中 `user_id` 为路径参数，未校验目标用户是否存在 → 可向任意不存在的 user 写入 / 删除孤立 `WarehouseItem`（仅 PUT 校验，DELETE 跳过；且 PUT 在 user 不存在时也不阻断已通过的 slot 校验）。
2. **H-2**：写操作（PUT / DELETE）日志缺少 `client_ip / user_agent`，与 servers / commands 模块的审计基线（R1 D-2 / R2 M-B3 落地）不一致，关键写路径无法追溯到管理员来源 IP。
3. **H-3**：`renderSlot` 全量 100 格 DOM 在每次 `loadWarehouse` 时整体 `clearChildren + append`，单次操作后立即重绘所有格子；同时未对 search dropdown / loadWarehouse 做请求级 `AbortController`，快速切换用户时存在「旧响应覆盖新响应」竞态。

---

## Findings

### High-1 PUT / DELETE 路径未校验 user_id 存在性，可写入孤立仓库记录

**File**: `server/routes/webui_warehouse.py:127-202`、`server/routes/webui_warehouse.py:205-235`
**Dimension**: security
**Issue**:
- `PUT /webui/api/warehouse/{user_id}/{slot_index}`：路径中的 `user_id` 仅由全局 webui auth 中间件保证「请求方已登录」，但对 `user_id` 这个**业务标识**没有任何字符集 / 长度限制（QQ 号本应为正整数字符串）。目前接受任意字符串如 `"' OR 1=1 --"`、`"../etc"`、emoji、空白等。虽然 SQLAlchemy ORM 已防 SQL 注入，但会产生孤立 `WarehouseItem.user_id`（任意值），且在 GET 时永远查不出来（除非攻击者用相同字符串读），导致脏数据沉淀。
- `DELETE /webui/api/warehouse/{user_id}/{slot_index}`（line 205）：**完全没有验证 `User` 是否存在**，仅检查 slot 是否非空。如果攻击者构造一个不存在的 `user_id`，DELETE 走完锁链路 + DB 查询，最终返回 404 `slot_empty`——浪费锁 / DB 资源，但更重要的是缺少与 PUT 一致的 `user_not_found` 语义。
- `PUT` 虽然在 line 149-153 检查了 user，但发生在拿到 `warehouse_lock` 之后；如果 user 不存在，仍然占用了 lock + session 一次。
**Fix sketch**:
1. 在 webui_warehouse 顶部加 `_validate_user_id(user_id: str)` helper：strip → 必须匹配 `^[1-9]\d{0,19}$`（QQ 号正整数字符串），否则 400 `invalid_path_parameter`，在 lock 之前调用。
2. DELETE 也加 User 存在性检查（与 PUT 一致），返回 `user_not_found` 404；或显式说明「DELETE 容忍 user 不存在」并以注释固化。
3. PUT 内 user 检查可以前移到 lock 之前以节省锁等待。
**Risk if unfixed**: 数据库沉淀脏数据；攻击面扩大（虽不可注入但可任意填充 user_id 列）；与 servers/commands 模块的路径参数校验粒度不一致。

---

### High-2 写操作日志缺失 client_ip / user_agent，与 R1/R2 审计基线不一致

**File**: `server/routes/webui_warehouse.py:187-191`、`server/routes/webui_warehouse.py:234`
**Dimension**: security
**Issue**:
- servers 模块 R1 D-2、commands 模块 R2 M-B3 均已为 WebUI 写路径补齐 `client_ip=` + `user_agent=` 上下文（见 `server/routes/webui_servers.py:160,191,265,319,407,444` 等），但 warehouse 的 PUT / DELETE 仅记录 `user_id={user_id} slot={slot_index}`，**完全没有请求方 IP / UA**。
- 后端 logger 入口 `nonebot.log` 在产品中用于敏感操作的事后审计，这条 gap 意味着无法回溯「是哪个管理员从哪个客户端编辑了某用户的某格物品」。
- 注意 `webui_warehouse.py` **未 import** `Request` 上下文用于读取 client_ip，且未引入 servers 里的 `_client_ip` helper。
**Fix sketch**:
1. 在 PUT 函数签名已有 `request: Request`，可直接复用 servers 的 helper 或在 routes 层抽公共 `_client_ip` helper（见 backlog）。
2. DELETE 当前签名 `delete_slot(user_id: str, slot_index: int)` 没有 `request` 参数 → 改为 `delete_slot(user_id: str, slot_index: int, request: Request)`。
3. 日志格式与 servers 对齐：`f"WebUI 仓库 {action}：user_id={user_id} slot={slot_index} ... client_ip={client_ip} user_agent={user_agent!r}"`。
**Risk if unfixed**: 安全事件无法溯源；与同项目内 servers / commands 写路径审计粒度不一致；违反 CLAUDE.md「关键入口 + 重要状态变化 + 外部依赖调用」日志规则中的「必要上下文」要求。

---

### High-3 切换用户 / 重载时无 fetch abort + 全量 100 格 DOM 重渲染

**File**: `server/webui/static/js/warehouse.js:128-184`、`server/webui/static/js/warehouse.js:407-426`
**Dimension**: perf
**Issue**:
- `loadWarehouse(userId)`（line 128）无 `AbortController`。若用户快速点击多个 dropdown 项 / 反复点 reload，请求并发且没有 cancel；返回顺序错乱时**后到的旧响应会覆盖新响应**（commands 模块 R1 P1-Race / servers 模块 B-2 都已修复同类问题）。
- `searchUsers(keyword)`（line 407）同样无 abort，输入 fast typing 时会有多 in-flight 请求，最后由 `current === keyword` 软比较 + `lastSearchKeyword` 去重保护，但只能挡住 keyword 已变化的场景；如果 keyword 在 200ms debounce 后两次相同 → 会触发两次请求（line 408 `if (lastSearchKeyword === keyword) return` 仅在第二次仍相同时 return，但首次 focus 重置为 `__force__` 会绕过）。
- `renderGrid()`（line 177-184）每次 reload 全量 `clearChildren(els.grid)` + 重建 100 个 cell。即便单格 PUT/DELETE 成功，line 317 `await loadWarehouse(state.user.user_id)` 也会触发 100 格重绘，HTML 节点数 ~10 个/格 × 100 = ~1000 个，存在 layout thrashing。
**Fix sketch**:
1. 引入两个模块级 `AbortController`：`loadController` / `searchController`；每次新调用前 `abort()` 旧的，把 signal 透传给 `api.apiRequest({ signal })`。
2. `renderSlot` 改为「仅替换变化的格子」：在 saveModal / confirmDelete 成功后，单点更新 `state.slots.get(slot)` 并 `replaceChild` 单格 cell，而不是 `loadWarehouse` 全量重拉。
3. 退化方案：至少在保存 / 删除分支局部更新，reload 按钮 / 切换用户保留全量逻辑。
**Risk if unfixed**: 切换用户 race 导致看到错位数据；单格编辑触发 1000+ DOM 操作，弱机器 / 帧节流时卡顿；与 servers/commands 模块的 abort 模式不一致。

---

### High-4 数值字段前后端校验不对齐：parseInt 截断 + 后端无上限

**File**: `server/webui/templates/warehouse_content.html:72-85`、`server/webui/static/js/warehouse.js:289-299`、`server/routes/webui_warehouse.py:75-124`
**Dimension**: security
**Issue**:
- `<input type="number" min="1" required />` 在浏览器层接受 `1e10`、`1.5`、`1, 2`、空格等；前端 `parseInt(els.fieldItemId.value, 10)`（line 289）会把 `"1.9"` 解析为 `1`、`"1e10"` 解析为 `1`（parseInt 在 e 处截断），导致**前端校验通过但实际写入的数和用户输入的数不一致**。
- 后端 `int(data.get("item_id", 0))`（line 79）对 `"1e10"` 这个字符串会抛 `ValueError` → 走 `item_id = -1` → 422。但若前端先 parseInt → 发 `1`，后端就吃 `1` 而不报错，业务上**用户以为存了 10000000000，实际存了 1**。
- 同样问题影响 `prefix_id`、`quantity`、`value`，且 `value` 缺少**上限**校验（理论可填 `Number.MAX_SAFE_INTEGER`，溢出到 SQLite Integer 列时行为依实现而定）。
- 这些字段写入 `WarehouseItem.value` / `quantity` 后会影响 lottery / 商店 / 经济系统的奖励计算逻辑（跨模块引用 `nextbot/plugins/warehouse.py`、`nextbot/plugins/lottery.py`）。
**Fix sketch**:
1. 前端：改用 `Number(value)` + `Number.isInteger(n)` 校验，拒绝小数 / 科学计数法 / NaN，并在 input 上加 `step="1"` + `inputmode="numeric"` + `pattern="\\d+"`。
2. 前端弹错文案：`保存失败，物品 ID 必须为整数且 ≤ 999999`（举例上限）。
3. 后端：在 `_validate_slot_payload`（line 75）对 `item_id` / `prefix_id` / `quantity` / `value` 加合理上限（如 `quantity ≤ 9999`、`value ≤ 1_000_000_000`），避免奇怪溢出。
4. 后端：拒绝传入 float（`isinstance(data.get("quantity"), bool)` 已被 int 转，但 `1.9` 这种 JSON number 会被 `int()` 截断 → 应先 `isinstance(raw, int)` 严格判断）。
**Risk if unfixed**: 用户体验割裂（前端看似允许但后端截断）；潜在经济系统数据失真；跨模块经济计算输入污染。

---

### Medium-1 后端日志格式不带 timestamp / level 前缀且使用全角冒号

**File**: `server/routes/webui_warehouse.py:187-191,234`
**Dimension**: copy / log hygiene
**Issue**: 当前日志主消息 `f"WebUI 仓库 {action}：user_id=... slot=..."` 使用中文全角冒号「：」分隔。CLAUDE.md 后端日志规范要求 timestamp/level 由底层框架统一输出，业务消息推荐风格 1（machine-search-first），但此处 `action` 是英文动词（create/update/delete），后面字段也是 key=value。混用中文「仓库 create」+ 全角冒号既不是纯 machine-friendly 的英文格式，也不是 human-reading-first 的自然中文，**风格混用**。
**Fix sketch**:
- 选项 A（machine-friendly）：`f"warehouse {action} success user_id={user_id} slot={slot_index} ..."` 完全英文 key=value。
- 选项 B（human-friendly）：`f"WebUI 仓库 {ACTION_ZH[action]}成功，用户 {user_id} 格子 {slot_index}，数量 {quantity}"`。
- 与 servers/commands 模块的「保存服务器成功：server_id=N client_ip=...」格式对齐，二选一全项目统一。
**Risk if unfixed**: 日志风格碎片化，跨模块 grep 困难；ELK / Loki 字段抽取规则需要为每个模块单独写。

---

### Medium-2 saveModal toast 文案违反「不得含操作对象名」

**File**: `server/webui/static/js/warehouse.js:295-299,318,352`
**Dimension**: copy
**Issue**: CLAUDE.md 操作反馈规范明确：「成功 = 动作 + 结果」「不得包含操作对象名称」。当前：
- line 318：`"保存成功，#" + slotShown` —— `#5` 是格子标识属于操作对象的位置信息，规范允许「补充必要诊断信息」但「主句必须独立表达完整核心事件」。这里主句是「保存成功」，附加上下文 `#5` 是可接受的位置信息，但**与规范的「不得含操作对象名」存在边界争议**。
- line 295-299 失败文案 `"保存失败，物品 ID 必须为正整数"` 等 —— 这里**前端自己生成原因**而非透传 API error，符合规范（前端预校验场景）。但 line 295 `"物品 ID 必须为正整数"` 里「物品 ID」属业务对象名，建议改「数值必须为正整数」或保留（这条是字段语义说明，非「动作 + 对象」组合）。
- line 352 `"删除成功，#" + slotShown` —— 同 line 318。
**Fix sketch**:
- 主文案改为纯动作：`"保存成功"` / `"删除成功"`；如确需展示哪一格，可用 secondary line 或 dropdown 形态展示，不要塞在 toast 主句。
- 预校验失败文案保留字段名（如「物品 ID 必须为正整数」），因为这属于诊断而不是「动作+对象」反例。
**Risk if unfixed**: 与 CLAUDE.md 文案规范的「正例：删除成功」直接冲突；多页面文案不一致体验割裂。

---

### Medium-3 saveModal 期间未禁用「保存」按钮，可重复提交

**File**: `server/webui/static/js/warehouse.js:284-322`、`server/webui/templates/warehouse_content.html:96`
**Dimension**: ux / perf
**Issue**: `saveModal` 提交时未 `els.modalSave.disabled = true`，用户连续按 Enter 或快速点击「保存」按钮会发起多次 PUT 请求；虽然 `warehouse_lock(user_id)` 在后端保证互斥，但前端会看到多次 toast / 多次 loadWarehouse 重绘。同问题也存在于 `confirmDelete`（line 335）和 search dropdown 重复点击。
**Fix sketch**:
1. 在 saveModal / confirmDelete 入口 `els.modalSave.disabled = true` / `els.deleteConfirm.disabled = true`，`try/finally` 中恢复。
2. 提交期间 modal 内 inputs 也应 disabled（防止用户改值后看到上一次结果）。
3. 参照 commands 模块 R1 P0-T1 / servers 模块 H-3 文案改造的 in-flight 状态管理。
**Risk if unfixed**: 多次 PUT 占用锁链路；UI 抖动；网络抖动时 toast 堆叠。

---

### Medium-4 search dropdown 缺少键盘导航（↑ ↓ Enter）

**File**: `server/webui/static/js/warehouse.js:376-405,462-477`
**Dimension**: ux / accessibility
**Issue**: dropdown 项仅绑定 `click` 事件，没有：
- ArrowUp / ArrowDown 选中高亮；
- Enter 确认（当前 Enter 在 input 触发提交但 dropdown 没消费）；
- `aria-activedescendant` / `role="listbox"` 等 ARIA；
- focus 状态视觉（hover-only）。
键盘用户与读屏用户无法操作 dropdown。
**Fix sketch**: 维护 `activeIndex` 状态，监听 ArrowUp/Down/Enter，更新 `.is-active` 类名 + `aria-selected` 属性；input 上加 `role="combobox"` + `aria-controls="wh-search-dropdown"`。
**Risk if unfixed**: 不可访问；键盘工作流低效；与 commands R1/R2 已建立的 modal 内 focus trap / keyboard a11y 基线不一致。

---

### Medium-5 ESC 关闭 modal 后未恢复焦点 + 无 focus trap

**File**: `server/webui/static/js/warehouse.js:497-503,275`
**Dimension**: ux / accessibility
**Issue**:
- ESC dispatcher（line 497-503）粗暴地 `m.classList.add("hidden")`，**绕过 `closeModal()` 的 state 清理**（`state.editingSlot = null`、`hideAlert`）；尤其在 wh-modal 上 ESC 不会重置 `state.editingSlot`，下次打开新格子时 `state.editingSlot` 仍为旧值（不过 `openModal` 会覆盖，所以只影响 ESC 后立即 deleteConfirm 等边角情况）。
- 没有 focus trap：modal 打开后 Tab 可以跳到 modal 外的页面元素（背景按钮 / search input）。
- 关闭 modal 后没有 focus restore 到触发它的 cell。
- `setTimeout(..., 30)` (line 275) 把 focus 推迟到下一帧，但无清理保障（modal 已关闭后 timer 仍执行 → focus 会闪到不可见的 input，触发屏幕键盘弹起）。
**Fix sketch**:
1. 把 ESC dispatcher 改为调用注册的 `close` 函数（参考 commands R2 B-4 modal stack + dispatcher 模式）。
2. 用 `openModalWithFocus(modal, firstFocusable, returnTarget)` helper：保存 `document.activeElement` 到 returnTarget，关闭时恢复。
3. focus trap：在 modal 内监听 Tab 循环到首/尾元素时手动设置 focus。
4. `setTimeout(..., 30)` 改为 `requestAnimationFrame` + 取消句柄。
**Risk if unfixed**: 键盘工作流割裂；可访问性回归；与 commands R2 已落地的 B-3/B-5/B-6 focus 模式不一致。

---

### Medium-6 modal-mask 点击关闭无防护，可在 saveModal 进行中误关

**File**: `server/webui/templates/warehouse_content.html:55,102`、`server/webui/static/js/warehouse.js:489-495`
**Dimension**: ux
**Issue**: 两个 modal 都有 `<div class="modal-mask" data-modal-close="wh-modal">`，点击 mask 直接 `hidden`。如果用户在 PUT 进行中点击 mask（误触 / 网络慢），modal 被关闭但请求仍在飞，请求成功后 line 317 `loadWarehouse` 会触发 toast，体验割裂；失败时则 toast 进 `els.modalAlert`（但 modal 已 hidden，alert 不可见）。
**Fix sketch**:
1. saveModal / confirmDelete 期间临时 disable mask 点击：用 `aria-busy="true"` + JS 拦截。
2. 或参照 commands 模块「提交中禁用所有 close 入口」的 in-flight 模式。
**Risk if unfixed**: 提交中关闭 modal 会丢失错误提示；用户误以为提交失败实际成功（或反之）。

---

### Medium-7 全局 ESC + 全局 click 监听器没有 modal 显示门控

**File**: `server/webui/static/js/warehouse.js:475-477,497-503`
**Dimension**: perf / correctness
**Issue**:
- `document.addEventListener("click")`（line 475）每次 click 都遍历检查 `els.searchWrap` 是否包含 target。轻量但每次 click 都触发，且**无 modal 打开门控**——当 wh-modal 打开时点击 modal 内任意位置都会触发这个 handler（虽不影响行为，但属于多余）。
- `document.addEventListener("keydown")`（line 497）ESC 处理也是全局，叠加 search input 的 ESC handler（line 472-474），ESC 会同时关 dropdown 和 modal（如果同时打开）；其实业务上 dropdown 在 modal 打开时不可见，但 dropdown 状态没被 close hooks 清理。
**Fix sketch**: 用 modal stack（参考 commands R2 B-4）+ 集中 ESC dispatcher：只关最上层 modal；dropdown 关闭逻辑用 `pointerdown` + AbortSignal 注册的方式，避免长期挂在 document 上。
**Risk if unfixed**: 多 listener 累积；ESC 行为顺序不确定。

---

### Medium-8 `/assets/items/Item_{id}.png` 路径直接拼接 item_id，无路径白名单

**File**: `server/webui/static/js/warehouse.js:201`
**Dimension**: security (low impact)
**Issue**: `img.src = "/assets/items/Item_" + slot.item_id + ".png"`。`slot.item_id` 来自 GET API 返回（后端是 `int(it.item_id)` 强类型），所以**本路径下不会注入**。但**如果未来后端 item_id 字段类型放宽**（如允许 string），路径会变成 `/assets/items/Item_..%2F..%2F.png`，触发路径穿越或外部资源加载（取决于 `/assets/` 是否做了规范化）。当前算 defensive design 提醒。
**Fix sketch**: 防御性增加 `Number(slot.item_id) | 0` 强制转 integer 后再拼路径。
**Risk if unfixed**: 当前低，但与「输入信任边界」原则不符；后端契约变更时埋雷。

---

### Medium-9 itemNameMap / prefixNameMap 字典加载失败时静默吞错

**File**: `server/webui/static/js/warehouse.js:59-82`
**Dimension**: ux / observability
**Issue**: `loadDicts` 中 `catch (e) { /* fall back to numeric ids */ }` 完全吞错 + 无 console.warn。当 `/assets/dicts/item.json` 返回 4xx/5xx 时（`itemRes.ok` 为 false）也没有任何提示，slot 名称会显示成 `ID:123` 而非物品名，用户看到的是退化 UI，无法察觉是字典加载失败。
**Fix sketch**:
1. `catch (e) { console.warn("加载字典失败", e); }` 至少记录到 console。
2. 在 `wh-alert` 顶部展示一条 info-level 提示「物品名称字典加载失败，仅显示 ID」。
3. 若 dict 加载是用户体验关键依赖，应 `Promise.all` 失败后展示明显 warning。
**Risk if unfixed**: 字典坏掉时无人发现；故障难以定位。

---

### Medium-10 unwrapData 异常 → showAlert 仅展示模糊文案

**File**: `server/webui/static/js/warehouse.js:144,153-157`
**Dimension**: ux
**Issue**: `api.unwrapData(payload)` 在 payload 形状错误时 `throw new Error("返回数据格式错误")`（见 api.js:89）。warehouse.js 捕获后 `err.message` 仅为「返回数据格式错误」，没有上下文（哪个 API、什么 payload）。
**Fix sketch**: 把 `unwrapData` 失败也归类为「加载失败，返回数据格式错误」并保留原始 payload 用于 console.warn 调试。
**Risk if unfixed**: 后端返回格式回归时排查困难。

---

### Medium-11 confirmDelete 成功后 closeDeleteModal + closeModal 但 alert 时序冲突

**File**: `server/webui/static/js/warehouse.js:335-356`
**Dimension**: ux
**Issue**: `confirmDelete` 成功后 line 349-351：
```
closeDeleteModal();
closeModal();
await loadWarehouse(state.user.user_id);
showAlert(els.alert, "删除成功，#" + slotShown, "success");
```
- `loadWarehouse` 内部 line 129 `hideAlert(els.alert)` 会**清掉 success alert 还没显示**（实际顺序：先 hideAlert → render → 然后 showAlert，但 showAlert 在 loadWarehouse 之后，所以最终 alert 会显示，但中间存在 alert flicker）。
- 同样在 saveModal line 316-318：先 closeModal → 后 loadWarehouse → 后 showAlert，loadWarehouse 触发 hideAlert 把任何之前的 alert 清空（包括失败 alert），但 success alert 仍能正常展示。
**Fix sketch**: 把 `loadWarehouse` 内的 `hideAlert(els.alert)` 改为「仅在 loadWarehouse 自己失败时不显示 success」，或将 showAlert 移到 loadWarehouse 之前；更稳的方案是单格 update 不重新 loadWarehouse。
**Risk if unfixed**: alert 闪烁；与 Medium-3 In-flight 状态管理一起改造更优。

---

### Low-1 dropdown 内 user.name 与 user.user_id 来自 API，已用 textContent（无 XSS）

**File**: `server/webui/static/js/warehouse.js:394,399`
**Dimension**: security (info)
**Issue**: 使用 `textContent` 设置，已经天然防 XSS。当前实现没有任何位置使用 `innerHTML`，**全部安全**（cross-checked）。这条记录为已确认无 XSS 风险，无需修复。
**Fix sketch**: 无。
**Risk if unfixed**: 无。

---

### Low-2 list_warehouse GET 路径未校验 user_id 字符集

**File**: `server/routes/webui_warehouse.py:25-72`
**Dimension**: security
**Issue**: 与 High-1 同源，但 GET 路径只读，不会产生脏数据。仍建议加 `_validate_user_id` 拦截，统一错误码（避免 user 不存在和 user_id 非法两个语义混在 404 上）。
**Fix sketch**: 同 High-1 helper。
**Risk if unfixed**: 错误码语义不精确（非法 user_id 也返回 `user_not_found`）。

---

### Low-3 list_tiers 端点未注入任何缓存头

**File**: `server/routes/webui_warehouse.py:18-22`
**Dimension**: perf
**Issue**: `/webui/api/warehouse/tiers` 返回的 `TIER_OPTIONS` 是静态枚举（来自 `nextbot.progression`），但每次 page load 都拉一次，且没有 `Cache-Control` / `ETag`。前端 line 84 `loadTiers` 在 DOMContentLoaded 时调用，每次刷新都打一次。
**Fix sketch**: 在 `api_success` headers 加 `Cache-Control: public, max-age=300`，或前端使用 `sessionStorage` 缓存。
**Risk if unfixed**: 无实质性能问题（响应体 < 1KB），但浪费往返。

---

### Low-4 wh-alert role="status" + aria-live="polite" 应保留，但 error 状态应 role="alert"

**File**: `server/webui/templates/warehouse_content.html:41`
**Dimension**: ux / accessibility
**Issue**: 当前固定 `role="status"` + `aria-live="polite"`。错误信息更应该是 `role="alert"` + `aria-live="assertive"`，让屏幕阅读器立即播报。
**Fix sketch**: JS 在 `showAlert` 时按 kind 动态切换 `role` / `aria-live` 属性。
**Risk if unfixed**: 屏幕阅读器对错误信息播报不及时。

---

### Low-5 slot 数据 value 字段语义在前端 UI 多处展示不一致

**File**: `server/webui/templates/warehouse_content.html:83-84`、`server/webui/static/js/warehouse.js:247-251`
**Dimension**: ux / copy
**Issue**:
- 表单 label：「单价（金币 / 件，0 = 不可回收）」
- slot 展示：`"💰 " + slot.value`（纯数字 + emoji，无单位提示）
- 中英文 / emoji 与中文混排时缺少空格分隔（CLAUDE.md 中英混排空格规则）：`"💰 " + slot.value` 中 emoji 后面有空格，但数字后无单位字符，看起来像金币数（但实际是单价）。
**Fix sketch**:
1. slot 展示改为 `"💰 ${slot.value}/件"` 或 tooltip 补充语义。
2. 与 label「单价（金币 / 件）」对齐。
**Risk if unfixed**: 用户对 value 字段含义混淆（实际单价 vs 显示像总值）。

---

### Low-6 warehouse_content.html 缺少 search input 的 label / aria

**File**: `server/webui/templates/warehouse_content.html:12-13`
**Dimension**: ux / accessibility
**Issue**: `<input id="wh-search-input">` 只有 `placeholder`，没有 `<label>` / `aria-label` / `aria-describedby`，屏幕阅读器只读到「edit text」。
**Fix sketch**: 加 visually-hidden `<label for="wh-search-input">搜索用户</label>` 或 `aria-label="搜索用户 QQ 号或用户名称"`。
**Risk if unfixed**: 可访问性回归。

---

### Low-7 CSS .modal z-index 120 与 search-dropdown z-index 30 关系正确，但未注释

**File**: `server/webui/static/css/warehouse.css:48,333`
**Dimension**: maintainability
**Issue**: dropdown z=30、modal z=120，正确（modal 应高于 dropdown）。但没有注释，未来其他模块 modal 改 z-index 时易踩坑。
**Fix sketch**: 在 CSS 文件顶部加 z-index scale 注释；或与 servers/commands 模块统一 z-index 变量。
**Risk if unfixed**: 维护成本累积。

---

### Low-8 CSS .tier-chip 颜色映射硬编码 rank 0-20，扩展性差

**File**: `server/webui/static/css/warehouse.css:300-327`
**Dimension**: maintainability
**Issue**: tier 数量未来扩展到 20+ 时 CSS 需要手动加新档；当前 rank=21 会 fallback 到默认（无 class 匹配则使用 base `.tier-chip` 样式 + 灰色）。这与 JS line 241 `tier-` + rank 计算耦合，但缺少 fallback class。
**Fix sketch**: 改用 CSS custom property + JS 计算 hue；或在 JS 端按 rank/5 离散化到 4 个固定档位。
**Risk if unfixed**: 未来 tier 扩展需要双端改。

---

### Low-9 saveModal slotShown 变量在 loadWarehouse 失败时无法回滚

**File**: `server/webui/static/js/warehouse.js:315-321`
**Dimension**: ux
**Issue**: PUT 成功 → closeModal → `loadWarehouse` 失败 → `showAlert("保存成功")` 仍执行，但 grid 已经 `hideAll()`（line 156-157）使整体清空。用户看到的是「保存成功」+ 空仓库视图，自相矛盾。
**Fix sketch**: 把 `await loadWarehouse(...)` 包在 try/catch 内，失败时不显示「保存成功」而显示「保存成功但刷新失败」；或先 showAlert，再 loadWarehouse。
**Risk if unfixed**: 罕见但矛盾的 UI 状态。

---

## Scope-out backlog（跨模块 / 共享层，本任务不计严重度）

- **B-1**：`_client_ip` helper 在 `server/routes/webui_servers.py` 与 `server/routes/webui.py` 各自实现，warehouse 也需要引入；建议抽到 `server/routes/__init__.py` 公共 helper（与 commands R2 M-B3 backlog 同源）。
- **B-2**：`api.apiRequest` 默认 `REQUEST_TIMEOUT_MS = 15000`（api.js:103），warehouse PUT/DELETE 路径走的是带 lock 的 DB 写，未来若 lock 等待超过 15s 会被 abort 但锁已被占用 → 建议为 warehouse 写路径加 timeoutMs override 或在 lock 加 lock-timeout。
- **B-3**：`/assets/dicts/item.json`、`/assets/dicts/prefix.json` 字典是全局静态资源，但加载行为分散在每个 page；建议提到 `webui.js` 全局 bootstrap 一次。
- **B-4**：`unwrapData` 抛 "返回数据格式错误"（api.js:89），所有页面共享；与 Medium-10 同源。
- **B-5**：search dropdown a11y / 键盘导航是 webui 共性需求（servers / users / warehouse 都有同类 dropdown），建议下沉到共享 `<combobox>` 组件。

---

## Caveats / Not Found

- 全局 auth 由 `add_webui_auth_middleware`（`server/web_server.py:372`）兜底，所有 `/webui/api/warehouse*` 路由都受保护；未发现匿名访问漏洞。
- 后端 SQLAlchemy ORM filter 全部用 `==` 表达式 + 占位符，未发现 raw SQL 注入。
- 后端无 `eval` / `subprocess` / 直接 shell 调用，未发现 RCE 面。
- 锁机制 `warehouse_lock(user_id)`（`nextbot/warehouse_lock.py`）使用 `asyncio.Lock`，单进程内互斥；多进程部署时无效——但此为跨模块设计，超出 4 文件 scope，不计。
- HTML 中所有用户数据均通过 `textContent` 注入，**未发现 `innerHTML` / `outerHTML` / `insertAdjacentHTML`** XSS 面。
- 未发现 CSRF token 校验路径；但 webui 全局采用 cookie session 鉴权，且 commands/servers 模块审计共识为 CSRF 由共享中间件层处理（dashboard R3 / servers R2 已 backlog 化），不计本次。
- 未发现明显 SSRF（无后端外呼）。
- 未发现 token / 密钥相关字段，warehouse 无机密信息。
