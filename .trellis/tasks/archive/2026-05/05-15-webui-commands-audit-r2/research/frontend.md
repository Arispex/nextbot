# R2 Frontend 桶审计 — commands 页面

- **Scope**: `server/webui/templates/commands_content.html` + `server/webui/static/js/commands.js`（commit `10d7936` 后状态）
- **Date**: 2026-05-15
- **桶**: trellis-research frontend
- **目的**: 复审 R1 修复 + 全量再扫剩余 finding

---

## Part A：R1 修复复审

### A-1. P0-T1 文案修复（保存成功 / 刷新失败）— ✅ 通过

- **位置**: `commands.js:428/430` 和 `:690/692`
- **当前 R1 实现**:
  - 行 428 `setStatus("保存成功", "success");`（reloaded=true 分支）
  - 行 430 `setStatus("保存成功，已立即生效；刷新失败，请手动刷新页面", "warning");`（reloaded=false 分支）
  - 行 690/692 同上（参数 modal 保存）
- **任务书约定的字符串**: `"保存成功，已立即生效；刷新失败，请手动刷新页面"`
- **比对**: 4 处均字符级一致；规范要求"动作 + 结果，原因"格式得到遵循，没有"保存命令""保存参数"等对象名违规。
- **结论**: R1 已修复，验证通过。

### A-2. 还有别的 setStatus 含对象名违规？— ✅ 全文 grep 无残留

`grep -nE '(参数|列表|命令|别名|服务器|用户|订单).*(成功|失败)' commands.js` 无匹配。
全文 14 个 setStatus 调用点 (行 87/420/428/430/437/690/692/706/711/766/927/997/1002/1006) 均符合"通用动词 + 结果"或"通用动词 + 结果，原因"，无对象名拼接。

但需要注意 `:706` `setStatus("页面资源版本不一致，请刷新页面或重启机器人", "error");` 不是"动作 + 结果，原因"格式，而是更接近 toast / banner 信息文案。这在 NextBot 现有规范里属于灰区（既不是操作结果反馈也不是 toast），R2 不强制改，但可标注。

### A-3. P1-Race debounce + AbortController — ⚠️ 通过但存在边界问题（见 B-1/B-2）

- **debounce**: 300ms（commands.js:818）。300ms 在搜索输入领域偏保守，与 dashboard 一致即可，不强制改。
- **AbortController 生命周期**:
  - 每次 input 事件 abort 旧 controller + new AbortController(commands.js:813-815)
  - `loadCommands` 接收 signal，await 后通过 `signal.aborted` 双重判断(行 733, 759)，catch 内也检查 `AbortError`/`ABORT_ERR`(行 762)
  - **abort 后 controller 实例没显式 set null** —— 但因为下次 input 立即被 new 出来的 controller 替换，旧 controller 由于 keydown listener 持有引用？ 看实际代码 `searchAbortController = new AbortController()` 直接覆盖，原 controller 在事件循环里只剩 fetch promise 的引用，await 完成后 GC 可回收，无泄漏。
- **searchDebounceTimer 清理**: 仅在下一次 input 触发时 `clearTimeout`。页面 unload / 组件卸载场景 NextBot 整页是 server-rendered，没有 SPA 卸载，可接受。
- **signal aborted silent return**: 行 733 `if (signal && signal.aborted) return false;` 在 await api.apiRequest 完成后判断；行 759 在 catch 块再判断。两条路径覆盖。
- **loadCommands(signal) 向后兼容**: 5 个调用点(行 797/803/824/832/840/928/1012)，只有 input handler 传 signal，其他都不传，函数默认 `signal` undefined，`signal && signal.aborted` 短路求值安全。

**通过项**：abort 时序合理，签名向后兼容。

**遗留问题见 B-1/B-2**。

### A-4. P2-T1 restart 传 action: "重启" — ✅ 通过

- `commands.js:1001` `await api.apiRequest("/webui/api/restart", { method: "POST", action: "重启", timeoutMs: 60000 });`
- buildActionFailureMessage("重启", reason) 将得到 `重启失败，<原因>`，符合规范。

### A-5. P2-T2 删除 modal alert 重复显示 — ✅ 通过

- 当前 `saveModalParams` 失败时仅调用 `setModalAlert(message, "error")`（commands.js:697），不再额外 `setStatus`。
- 成功分支才 setStatus + closeParamModal。无重复。

### A-6. P2-A modal focus 管理 — ⚠️ 通过但存在 4 个边界问题（见 B-3/B-4/B-5/B-6）

- **WeakMap**（modalPreviousFocus + modalTrapHandlers）：以 modal DOM 节点为 key，节点不会被 dynamically remove（HTML 静态渲染），所以 GC 不是问题。但 entry **关闭时手动 delete**（commands.js:173, 176）已经清理，无累积。
- **openModalWithFocus 首次 focus**：`setTimeout(..., 0)`（行 154），合理。CSS `.hidden { display: none }`，必须先 remove hidden 才能 focus，setTimeout 解耦布局可接受。
- **focus trap Tab 监听**：行 130-143，shift+Tab 反向循环已覆盖。
- **disabled 元素跳过**：`getFocusableInModal` 的 selector(行 124) 写了 `input:not([disabled])` 等，OK。
- **closeModalAndRestoreFocus**：`document.contains(previousFocus)` 检查(行 177)，DOM 已移除时不 focus（fallback 行为是把焦点留在原处，可接受）。
- **跳过 close button (✕)**: 通过 `.modal-close-btn` className filter(行 158)。

**遗留问题见 B-3/B-4/B-5/B-6**。

### A-7. P2-ESC restart-confirm-modal — ⚠️ 通过但与其他两个 ESC handler 串扰（见 B-7）

`commands.js:983-987` 用 `window.addEventListener("keydown", ...)` 监听 ESC + 判断 `!restartModal.classList.contains("hidden")`。
但 param modal ESC handler（行 862-866）和 alias modal ESC handler（行 953-957）也都监听 window keydown。3 个 listener 都不阻止冒泡。多 modal 嵌套场景见 B-7。

### A-8. P2-Loading aria-busy 同步 — ✅ 通过

- `setLoadingVisible` (commands.js:342-350) 两侧分别 setAttribute "true"/"false"
- **所有 loading 切换点**：grep `setLoadingVisible` 在 commands.js 出现 3 次（定义 + `:354` renderTable 完成隐藏 + `:705` apiReady 失败 + `:714` loadCommands 开始显示 + `:767` 错误分支隐藏）。
- HTML 初始 `aria-busy="true"`（行 35）和 JS 首次 set false 一致：`loadCommands → setLoadingVisible(true)` 触发 setAttribute("true") 重置（实际等同），渲染完成或 catch 后转 false。**无遗漏**。
- 错误状态(行 767)：`setLoadingVisible(false)` 已被调用，aria-busy 正确重置为 false。

### A-9. P3 排版 / 一致性 — ✅ 大部分通过，1 项小遗漏（见 B-8）

- **半角省略号**：`grep '\.\.\.'` 在 commands.js 无匹配（除 spread/rest 语法及无关注释，本次未出现）。所有用户可见省略号均为全角 `…`（行 420, 681, 997, 1002）。
- **全角冒号**：modal alert 拼接用 `${paramLabel}：${message}`(行 650, 672) 全角；OK。
- **alias saving guard `closeAliasModal(force=false)`**：行 894-899 实现，行 926 `closeAliasModal(true)` 在成功路径显式传 true；行 942/943/948/955 的 caller 传 undefined（隐式 false），符合"saving 中阻止关闭"语义。但 **行 942/943** 把 `closeAliasModal` 直接当 listener 传入（`addEventListener("click", closeAliasModal)`），listener 收到的第一个参数是 MouseEvent 对象，被当作 force 求 `if (aliasSaving && !force)` 时 `!MouseEvent === false`，等价于 force=true，**绕过 saving guard**。⚠️ 见 B-8。
- **空态文案去句号**：
  - commands.js:357 `"当前页暂无数据"` / `"暂无可配置命令"` — 无句号 ✓
  - 行 386 `"暂无介绍"` ✓
  - 行 393 `"未填写用法"` ✓
  - 行 519 `"当前命令没有可配置参数"` ✓
  - commands_content.html:36 `暂无可配置命令` ✓
- **replaceChildren**: 3 处使用（行 353, 514, 621），均代替原 innerHTML = ""，无残留 innerHTML 赋值。

---

## Part B：全量再扫新发现

### B-1. P1 / High：debounce 期间用户立即点击「刷新」/分页 → 旧 debounce 仍会 trigger 一次过期 loadCommands

- **位置**: commands.js:807-819 + 801-803 reload / 821-825 perPage / 827-841 prev/next
- **触发概率**: 中高（搜索输入后 300ms 内点击其它按钮）
- **严重度**: P1（数据 race + UI 抖动）
- **场景**:
  1. 用户键入 "a" → 启动 debounce timer 300ms
  2. 250ms 内用户点击 reloadButton → 立即 `void loadCommands();`（无 signal）
  3. 50ms 后 debounce 触发 → 启动新的 search loadCommands(signal)
  4. 两条请求结果竞争渲染。reload 触发的请求先完成，被 search 的覆盖（即使 search 自己 abort 了旧 search controller，但 reload 请求没有 signal，无法 abort）
- **修复前**:
  ```js
  reloadButton.addEventListener("click", () => {
    currentPage = 1;
    void loadCommands();  // 不取消 pending debounce
  });
  ```
- **修复后建议**:
  ```js
  const cancelPendingSearch = () => {
    if (searchDebounceTimer) { clearTimeout(searchDebounceTimer); searchDebounceTimer = null; }
    if (searchAbortController) { searchAbortController.abort(); searchAbortController = null; }
  };
  reloadButton.addEventListener("click", () => {
    cancelPendingSearch();
    currentPage = 1;
    void loadCommands();
  });
  // perPage / prev / next 同样调用 cancelPendingSearch()
  ```

### B-2. P2 / Medium：searchAbortController 在 page lifecycle 末端可能持有 fetch promise

- **位置**: commands.js:812-815
- **严重度**: P2（无功能影响，但形式上 leak）
- **说明**: abort 后 controller 实例覆盖给新 controller，旧 controller 通过 `signal.addEventListener("abort", ...)` 在 fetch 内部仍有引用，直到 fetch promise reject/settle 才能 GC。这是 fetch + AbortController 的固有行为，非 bug，但可在 unload 时 abort 兜底：
  ```js
  window.addEventListener("beforeunload", () => {
    if (searchAbortController) searchAbortController.abort();
    if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
  });
  ```
- **触发概率**: 低（用户关页前正好 in-flight 才有微弱影响）

### B-3. P1 / High：多 modal 嵌套打开导致 previousFocus 错乱 + Tab trap 互相干扰

- **位置**: commands.js:50-52, 146-164, 855-866, 944-957, 983-987
- **严重度**: P1（焦点管理 bug + 可达性回归）
- **触发场景**:
  1. 用户在 param modal 打开后（modalPreviousFocus 记录了表格的「参数」按钮）
  2. 表格被搜索/刷新触发 renderTable → tableBodyNode.replaceChildren() **销毁了 previousFocus 节点**
  3. 关闭 param modal → `document.contains(previousFocus) === false`，焦点静默丢失到 `<body>`
- **现状**: 行 177 已有 `document.contains` 防护，但无 fallback —— 焦点丢到 body。Bug 是没主动 fallback 到一个稳定可聚焦元素（如 reloadButton / restartButton / `<main>`）。
- **修复前**:
  ```js
  if (previousFocus && document.contains(previousFocus) && typeof previousFocus.focus === "function") {
    try { previousFocus.focus({ preventScroll: true }); } catch (_e) { previousFocus.focus(); }
  }
  ```
- **修复后建议**:
  ```js
  if (previousFocus && document.contains(previousFocus) && typeof previousFocus.focus === "function") {
    try { previousFocus.focus({ preventScroll: true }); } catch (_e) { previousFocus.focus(); }
  } else {
    // fallback：把焦点交给页面级 landmark 或主按钮
    const fallback = reloadButton || document.querySelector("main, [role=main]");
    if (fallback && typeof fallback.focus === "function") {
      if (!fallback.hasAttribute("tabindex")) fallback.setAttribute("tabindex", "-1");
      fallback.focus();
    }
  }
  ```
- **触发概率**: 中（搜索/重启场景常发）

### B-4. P1 / High：3 个 ESC handler 同时绑在 window，导致 ESC 同时关闭多个 modal（虽然现状只可能同开 1 个，但 listener 互相干扰）

- **位置**: commands.js:862-866（param modal）, 953-957（alias modal）, 983-987（restart modal）
- **严重度**: P1（focus / ESC 行为不可预期）
- **现状**: 3 个 listener 在每次 ESC 都被全部触发，每个内部各自判断 `!modalNode.hidden` 后调用 close。当前业务场景下 3 个 modal **永远不会同时打开**（点击 restart 按钮时其他 modal 已关），所以行为仍正确。但 listener 堆叠 + 不阻止冒泡，**任何 ESC** 都会触发 3 次 keydown 调用（包括用户在表格搜索框按 ESC 想清空也会触发某个 modal 检查），微小开销 + 维护脆弱性。
- **修复后建议**: 合并到一个 window keydown listener，按"栈顶 modal"原则关闭：
  ```js
  const modalStack = [];  // 栈顶 = 最近打开
  const pushModalToStack = (m) => modalStack.push(m);
  const popModalFromStack = (m) => {
    const idx = modalStack.lastIndexOf(m);
    if (idx >= 0) modalStack.splice(idx, 1);
  };
  // openModalWithFocus 内 pushModalToStack(modalNode);
  // closeModalAndRestoreFocus 内 popModalFromStack(modalNode);
  window.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const top = modalStack[modalStack.length - 1];
    if (!top || top.classList.contains("hidden")) return;
    // 路由到对应 modal 的 close 函数
    if (top === modalNode) closeParamModal();
    else if (top === aliasModalNode) closeAliasModal();
    else if (top === restartModal) closeRestartModal();
  });
  ```
- **触发概率**: 低-中（功能正确，但属于"看上去对、实际脆弱"的代码）

### B-5. P2 / Medium：openModalWithFocus 重复 open 同一个 modal 时，旧 previousFocus 被覆盖

- **位置**: commands.js:146-164
- **严重度**: P2（边界 bug）
- **场景**: 用户在 param modal 已打开时通过键盘 / 程序逻辑再次触发 `openParamModal` → `modalPreviousFocus.set(modalNode, document.activeElement)` 覆盖了原 previousFocus（此时 activeElement 在 modal 内部），关闭后焦点回不到最初的"参数"按钮，跳到 modal 内某元素或 body。
- **修复后建议**:
  ```js
  const openModalWithFocus = (modalNode) => {
    if (!modalNode || !modalNode.classList.contains("hidden")) return; // 已打开则跳过
    modalPreviousFocus.set(modalNode, document.activeElement);
    // ... 原逻辑
  };
  ```
- **触发概率**: 低（业务路径不太可能重复 open，但程序员误用或 race 场景会触发）

### B-6. P3 / Low：getFocusableInModal selector 未覆盖 `<a href>` / `[contenteditable]` / 自定义可聚焦元素

- **位置**: commands.js:120-127
- **严重度**: P3（当前 modal 内只有 input/select/button，命中所有元素，无 a / contenteditable / details，无实际问题）
- **现状**: selector =
  ```js
  'input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex="-1"])'
  ```
- **风险**: 未来若有人在 modal-body 内添加 `<a href>` / `<details>` / contenteditable，trap 漏检 → Tab 跳出 modal。
- **修复后建议**:
  ```js
  'a[href]:not([disabled]), area[href]:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [contenteditable]:not([contenteditable="false"]), [tabindex]:not([tabindex="-1"]):not([disabled]), details:not([disabled]) > summary:not([disabled])'
  ```
- **触发概率**: 极低（依赖未来扩展）

### B-7. P1 / High：closeAliasModal 直接绑定到 click listener，绕过 saving guard

- **位置**: commands.js:942-943
- **严重度**: P1（数据一致性 bug）
- **现状**:
  ```js
  if (aliasCancelButton) aliasCancelButton.addEventListener("click", closeAliasModal);
  if (aliasCloseButton) aliasCloseButton.addEventListener("click", closeAliasModal);
  ```
- **问题**: `closeAliasModal(force = false)` 第一个参数 `force` 接收的是 MouseEvent 对象（而不是 boolean）。`!event` 在对象上为 `false`，等价于 `force=true`，**所以 aliasSaving 中点击「取消」/「✕」会直接关闭 modal，与 R1 引入的 saving guard 设计意图矛盾**。
- **对比 param modal**: 行 847-853 用 arrow function 包裹（`() => closeParamModal()`），不带参数，force 默认 false → 正确触发 guard。
- **修复前** (commands.js:942-943):
  ```js
  if (aliasCancelButton) aliasCancelButton.addEventListener("click", closeAliasModal);
  if (aliasCloseButton) aliasCloseButton.addEventListener("click", closeAliasModal);
  ```
- **修复后**:
  ```js
  if (aliasCancelButton) aliasCancelButton.addEventListener("click", () => closeAliasModal());
  if (aliasCloseButton) aliasCloseButton.addEventListener("click", () => closeAliasModal());
  ```
- **同 bug** 是否影响行 948/955？
  - 行 948 在 mask click 内 `closeAliasModal()` 显式调用无参，OK。
  - 行 955 window keydown 内 `closeAliasModal()` 显式调用无参，OK。
- **触发概率**: 中（用户保存 alias 时来不及响应而点取消的概率不大，但行为不可预期）

### B-8. P2 / Medium：reloadButton 不在 requiredNodesReady 校验里 + 没空检查就 addEventListener

- **位置**: commands.js:54-77（requiredNodesReady）+ 801（reloadButton.addEventListener）
- **严重度**: P2（潜在 TypeError）
- **现状**: `requiredNodesReady` 包含 statusNode / loadingNode / 9 个分页节点 / modal 节点，**不包含** `reloadButton` 和 `searchInput`。但 `reloadButton.addEventListener("click", …)`（行 801）和 `searchInput.addEventListener("input", …)`（行 807）都未做 null 检查 → 模板若被某种原因（旁路 partial / 改版）不渲染 reload-btn 或 command-search，整个 IIFE 在行 801 抛 TypeError，下面所有 init（含 modal handler 注册和 `void loadCommands()` 行 1012）都不执行，页面静默白屏。
- **同类**: `restartButton` 在行 961 已经 `if (restartButton)` 防护，aliasModalNode / aliasSaveButton 等都有 null check，**唯独 reloadButton / searchInput 没有**。
- **修复前**:
  ```js
  reloadButton.addEventListener("click", () => {...});
  searchInput.addEventListener("input", () => {...});
  ```
- **修复后** (两个选项):
  1. 加入 `requiredNodesReady`:
     ```js
     const requiredNodesReady = Boolean(
       reloadButton && searchInput && statusNode && ... );
     ```
  2. 或单独防护:
     ```js
     reloadButton?.addEventListener("click", () => {...});
     searchInput?.addEventListener("input", () => {...});
     ```
- **触发概率**: 极低（模板恒定渲染），但属于一致性问题

### B-9. P2 / Medium：apiReady 失败时 setLoadingVisible(false) + setStatus 错误，但不再 throw / disable 后续按钮

- **位置**: commands.js:704-708
- **严重度**: P2（UX 退化）
- **现状**:
  ```js
  if (!apiReady) {
    setLoadingVisible(false);
    setStatus("页面资源版本不一致，请刷新页面或重启机器人", "error");
    return false;
  }
  ```
- **问题**: loadCommands 返回 false 后，**reloadButton / searchInput / perPage / prev / next 仍可点击**，再次触发 loadCommands → 又走 apiReady=false 分支 → 重复打印 setStatus（虽然不再叠加，但反复刷状态文案）。建议在此分支后禁用顶部所有交互按钮。
- **修复后建议**:
  ```js
  if (!apiReady) {
    setLoadingVisible(false);
    setStatus("页面资源版本不一致，请刷新页面或重启机器人", "error");
    reloadButton.disabled = true;
    searchInput.disabled = true;
    perPageSelect.disabled = true;
    prevPageButton.disabled = true;
    nextPageButton.disabled = true;
    if (restartButton) restartButton.disabled = true;
    return false;
  }
  ```
- **触发概率**: 极低（apiReady 几乎不会 false，但 graceful degradation 缺失）

### B-10. P3 / Low：openParamModal 内 paramSchema 序列化到 dataset 时 schema 含特殊字符 / 大对象会膨胀 DOM

- **位置**: commands.js:607
- **严重度**: P3（理论性能 + 不必要的 DOM 大小）
- **现状**:
  ```js
  inputNode.dataset.paramSchema = JSON.stringify(definition);
  ```
- **问题**:
  1. 每次重 render modal body 都 stringify 一次 schema 写到 dataset，再在 saveModalParams (行 648) JSON.parse 回来。schema 大时 DOM size 浪费。
  2. 若 schema 中含 `description: "<script>alert(1)</script>"` 之类被 stringify 后嵌入 dataset，虽然 dataset 是 attribute 取值，不会 HTML 解释，但 JSON 内任意值若被未来某个 innerHTML 路径回灌会成 XSS 风险（**当前代码全部用 textContent，无 XSS**）。
- **修复后建议**: 用 module-scoped Map<inputNode, schema> 代替 dataset 序列化：
  ```js
  const paramInputSchemas = new WeakMap();
  // openParamModal: paramInputSchemas.set(inputNode, definition);
  // saveModalParams: const schema = paramInputSchemas.get(inputNode);
  ```
- **触发概率**: 低（schema 通常小、当前无 XSS 路径）

### B-11. P3 / Low：alias 输入校验缺失 + 重复 alias 未去重

- **位置**: commands.js:904-905
- **严重度**: P3（依赖后端校验，前端 UX 弱）
- **现状**:
  ```js
  const raw = String(aliasInput.value || "").trim();
  const aliases = raw ? raw.split(",").map(s => s.trim()).filter(Boolean) : [];
  ```
- **问题**: 无去重，"c, c, c" → 提交 ["c","c","c"]，依赖后端报错。重复时前端可直接 dedupe + 给提示。空格仅 trim，含中文逗号 `，` 不切分。
- **修复后建议**:
  ```js
  const aliases = raw ? raw.split(/[,，]/).map(s => s.trim()).filter(Boolean) : [];
  const uniqueAliases = Array.from(new Set(aliases));
  if (uniqueAliases.length !== aliases.length) {
    setAliasAlert("已自动去重重复别名", "info");
  }
  ```
- **触发概率**: 低（用户输入习惯）

### B-12. P3 / Low：滚动条 / 模态 body 在 modal 打开时未锁定 background scroll

- **位置**: commands.js:146-164
- **严重度**: P3（移动端 + 长表格 UX 问题）
- **现状**: 打开 modal 时不加 body class 锁定滚动，用户在 modal 上滚动会穿透到背景表格。
- **修复后建议**: openModalWithFocus 时 `document.body.classList.add("modal-open")`，closeModalAndRestoreFocus 时移除。CSS 端 `.modal-open { overflow: hidden; }`。
- **触发概率**: 中（长表格 + 移动端）。但 CSS 改造不在本桶范围。

### B-13. P3 / Low：commands_content.html 行 35 `<div id="loading" role="status">` 用 `<div>` 而非 `<output>` / `<span>`，role 重复

- **位置**: commands_content.html:32-35
- **严重度**: P3（语义微优化）
- **现状**:
  ```html
  <div id="status" class="alert hidden" role="status" aria-live="polite">
    <span id="status-message" class="alert-message"></span>
  </div>
  <div id="loading" class="empty" role="status" aria-live="polite" aria-busy="true">正在加载命令…</div>
  ```
- **观察**: 两个 `role="status" aria-live="polite"` 同时存在 → 屏幕阅读器可能播放两次 / 顺序不确定。建议把 loading 改为 `aria-live="off"`（或不设 role）让 status 唯一作为 live region，loading 仅作视觉占位 + aria-busy 转移到 `<table>` / `<main>`。
- **触发概率**: 低（与 dashboard 该项一致即可，不强制修）

### B-14. P3 / Low：行 1003 `setTimeout(() => location.reload(), 3000);` 没有 cleanup / 用户取消机会

- **位置**: commands.js:1003
- **严重度**: P3（UX 弱）
- **现状**: 重启成功后 3 秒后自动 reload。用户若想立即手动检查、查看 status 文案、或取消不能。
- **修复后建议**: 给 status 文案加可点击 `<a>` 立即刷新，并允许用户 ESC 取消自动 reload（实现成本大于价值，标注即可）。
- **触发概率**: 低

### B-15. P2 / Medium：搜索 `q` 参数无最大长度限制

- **位置**: commands.js:721
- **严重度**: P2（潜在 DOS / log spam）
- **现状**:
  ```js
  `...&q=${encodeURIComponent(String(searchInput.value || "").trim())}`
  ```
- **问题**: 用户黏贴 10MB 文本到 search input → URL 超大 → 浏览器/CDN/服务端可能 4xx，但前端没拦截，每次 input 都 debounce 后发出。建议前端截断到 200 字符 + maxlength。
- **修复后建议**:
  - HTML: `<input id="command-search" ... maxlength="200" />`
  - JS: `const q = String(searchInput.value || "").trim().slice(0, 200);`
- **触发概率**: 极低（恶意场景）

---

## 结论

### R1 修复整体复审

| Finding | 状态 | 备注 |
|---|---|---|
| P0-T1 文案 (4 处) | ✅ 通过 | 字符级一致 |
| P1-Race debounce + AbortController | ⚠️ 通过 | 见 B-1 race 边界 |
| P2-T1 restart action 参数 | ✅ 通过 | |
| P2-T2 alert 不重复 | ✅ 通过 | |
| P2-A modal focus helpers | ⚠️ 通过 | 见 B-3/B-4/B-5/B-6 |
| P2-ESC restart modal | ⚠️ 通过 | 见 B-4 |
| P2-Loading aria-busy | ✅ 通过 | |
| P3 排版 / 一致性 | ⚠️ 通过 | 见 B-7（closeAliasModal 绕过 guard）|

R1 主要修复全部落地，文案字符级符合规范。

### 本轮 R2 新增 finding 优先级摘要

- **P1 (3)**: B-1 (race-with-reload)、B-3 (focus fallback 缺失)、B-4 (3 个 window ESC listener 互相干扰)、B-7 (closeAliasModal 直接当 listener 绕过 saving guard)
- **P2 (4)**: B-2 (unload 不 abort)、B-5 (重复 open modal 覆盖 previousFocus)、B-8 (reloadButton / searchInput 缺 null guard)、B-9 (apiReady=false 不 disable 按钮)、B-15 (q 无 maxlength)
- **P3 (5)**: B-6 (focusable selector 不完整)、B-10 (schema 序列化到 dataset)、B-11 (alias 无去重 / 中文逗号)、B-12 (body 滚动锁)、B-13 (role=status 重复)、B-14 (重启自动 reload 不可取消)

### 推荐优先实施

1. **B-7** (closeAliasModal 绑定方式) — 1 行修复，立即生效
2. **B-1** (race 取消 pending search) — 5 行 + 4 处 caller
3. **B-3** (focus fallback) — closeModalAndRestoreFocus 内加 fallback 分支
4. **B-4** (合并 ESC handler + modal stack) — 中等改造，提升健壮性
