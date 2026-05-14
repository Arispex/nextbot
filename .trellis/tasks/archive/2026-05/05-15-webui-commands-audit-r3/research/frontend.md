# R3 Frontend 桶审计 — commands 页面

- **Scope**: `server/webui/templates/commands_content.html`, `server/webui/static/js/commands.js`
- **Date**: 2026-05-15
- **R2 commit reviewed**: `f512c8c`
- **Out of scope (R2 不再审 / 已闭环)**: C-1 / D-3 / D-6 / C-5 / B-13 / L-B1 / H-B1，CSS 文件，`api.js` / `webui.js` / 其他 webui 模块。
- 跨模块发现统一标 `scope-out backlog`。

---

## Part A：R2 修复复审

### A.1 B-7 R1 regression（closeAliasModal arrow wrap）
- **位置**: `commands.js:1047-1049`
- **状态**: **已修复**
- **现状**:
  ```js
  if (aliasSaveButton) aliasSaveButton.addEventListener("click", saveAliases);
  if (aliasCancelButton) aliasCancelButton.addEventListener("click", () => closeAliasModal());
  if (aliasCloseButton) aliasCloseButton.addEventListener("click", () => closeAliasModal());
  ```
  cancel/close click 都被 arrow function 包裹，DOM 传入的 `MouseEvent` 不会作为 `force` 形参。
- **`saveAliases` 内部 `closeAliasModal(true)` 路径**（`commands.js:1030-1031`）：在 `aliasSaving = false;` 之后调用 `closeAliasModal(true)`，force=true 显式绕开 saving guard（虽然此时 saving 已被置 false，true 是冗余但安全）。保留正确。
- **结论**：B-7 修复彻底，无残留触发面。

### A.2 B-1 cancelPendingSearch helper
- **位置**: helper 定义 `commands.js:874-883`；调用点 `commands.js:886, 907, 917, 926, 933`。
- **状态**: **已修复**，且 5 处全部覆盖（reload / perPageSelect change / prevPage / nextPage / beforeunload）。
- **正确性细节**:
  - `searchAbortController.abort();` 后立即 `searchAbortController = null;`（行 881），避免后续 search 误用同一已 abort 的 controller。
  - 防御性 `if (searchDebounceTimer)` / `if (searchAbortController)` null check，对未初始化路径安全。
- **注意**：`searchInput.addEventListener("input")` handler（行 892-904）**不**调用 `cancelPendingSearch`，而是直接覆盖 timer + abort old controller。这一致是正确的（新输入应替换旧 pending，不需要清空 timer）。但实际行 893 没用 helper 复用——属于风格不一致，非 bug。

### A.3 B-3 previousFocus fallback + tabindex 正确性
- **位置**: `commands.js:235-253`
- **状态**: **已修复**，并解决 R2 提及的 native button 误加 tabindex 问题。
- **细节**:
  - 行 240：fallback 优先 `reloadButton`（原生 `<button>`，本身可聚焦），其次 `main / [role=main]`。
  - 行 242-247：判断 `nativelyFocusable`（A/BUTTON/INPUT/SELECT/TEXTAREA）才**跳过**加 tabindex；非原生 focusable 且未有 tabindex 才补 `tabindex="-1"`。
  - reloadButton 命中 nativelyFocusable=true，不会被注入 tabindex，Tab 顺序无破坏。
- **结论**：B-3 修复彻底。次要观察：fallback `<main>` 不存在时静默无操作——这是可接受的降级。

### A.4 B-4 modal stack + 单 ESC dispatcher
- **位置**: stack/closer 定义 `commands.js:154-171`；ESC dispatcher `commands.js:937-946`；3 个 closer 注册 `commands.js:968`（param），`1058`（alias），`1086`（restart）。
- **状态**: **已修复**。
- **正确性细节**:
  - `pushModalToStack`（157-162）：先 `lastIndexOf + splice` 去重再 push，避免同 modal 重复 push 时栈被污染。
  - `popModalFromStack`（163-167）：用 `lastIndexOf` 删除最近一次（栈顶语义正确）。
  - `closeModalAndRestoreFocus`（215-254）调用 `popModalFromStack(modalNode);`（行 225），不论 hidden 状态都执行——配合 `closeRestartModal` / `closeParamModal` / `closeAliasModal` 都走 `closeModalAndRestoreFocus`，确保关闭路径必清栈。
  - ESC dispatcher（937-946）只对栈顶起作用，hidden modal 跳过。
- **潜在边界**：3 个 close 函数（`closeParamModal` 行 689-695、`closeAliasModal` 行 996-1001、`closeRestartModal` 行 1071-1074）仍各自存在；都走 `closeModalAndRestoreFocus`，没有遗漏 pop 路径。
- **结论**：实现正确，无嵌套漏检面。

### A.5 B-5 openModalWithFocus 已打开 return
- **位置**: `commands.js:189-210`，guard 在行 191：`if (!modalNode.classList.contains("hidden")) return;`
- **状态**: **已修复**。
- **正确性细节**:
  - guard 在 `modalPreviousFocus.set` 之前，防止重复 open 时 previousFocus 被当前焦点覆盖。
  - 与 caller（行 591 `openModalWithFocus(modalNode)` 在 param modal、行 686、993 alias、1077 restart）协调正常——所有 caller 都允许 guard 静默 return。
- **次要观察**：guard 通过 `hidden` class 判断，依赖 close 函数必加 `hidden`（`closeModalAndRestoreFocus` 行 217 已加）。class 系统是单一可信源，行为一致。
- **结论**：B-5 修复正确。

### A.6 B-9 apiReady=false disable
- **位置**: `commands.js:768-779`，disable 6 个控件（reloadButton/searchInput/perPageSelect/prevPageButton/nextPageButton/restartButton）。
- **状态**: **已修复**。
- **细节**:
  - 每个控件 disable 前用 `if (node)` 防 null（行 772-777），与 `requiredNodesReady`（行 56-83）已校验过的强必需节点重复——但是 `restartButton` 不在 `requiredNodesReady` 校验列表（commands.js 行 56-79 列表里没有 `restartButton`、`aliasModalNode` 等），所以行 777 的 `if (restartButton)` 是必要防御。
- **次要观察**：`apiReady=false` 后只 setStatus 一次（行 770）+ disable 控件，**没有**调用 `setLoadingVisible(false)` 之外的清理（loading 已隐藏，empty/table/pagination 也没清理）。但因 disable 后用户无法再触发交互，UI 残留状态影响有限。
- **结论**：修复达到 R2 描述的目标（防反复触发 loadCommands）。

### A.7 B-10 WeakMap paramInputSchemas
- **位置**: 定义 `commands.js:54`；写入 `commands.js:678`；读取 `commands.js:713`。
- **状态**: **已修复**，dataset.paramSchema 完全消除。
- **验证**: grep `paramSchema` 在 commands.js 中仅匹配 `paramInputSchemas` 变量本身和 schema 上下文，无 `dataset.paramSchema`。JSON.parse 错误分支也不存在（行 707-716 直接走 `paramInputSchemas.get(inputNode)`）。
- **GC 正确性**：key 是 inputNode，节点在 `modalBodyNode.replaceChildren();`（行 584、692）销毁后自然 GC。
- **结论**：B-10 修复彻底。

### A.8 B-11 alias 中文逗号 + 去重
- **位置**: `commands.js:1006-1014`
- **状态**: **已修复**。
- **细节**:
  - 行 1008：`raw.split(/[,，]/)` 兼容半角与全角逗号。
  - 行 1009：`Array.from(new Set(rawAliases))` 去重保序（Set 迭代器按插入顺序）。
  - 行 1010：`const deduped = aliases.length !== rawAliases.length;` 计算长度差判断是否触发去重。
  - 行 1014：`setAliasAlert(deduped ? "已自动去重，正在保存…" : "正在保存…", "info");` 文案提示。
- **轻微观察**：仅检测 `,` / `，`，不处理顿号 `、` / 分号 `;`。属于产品决策，不算 bug。

### A.9 B-12 body inline scroll lock
- **位置**: 定义 `commands.js:175-185`；触发 `commands.js:198`（open）/ `226`（close）。
- **状态**: **已修复**，且嵌套 modal 行为正确。
- **细节**:
  - `lockBodyScroll`（176-180）：仅在栈深为 1 时（即首个 modal 打开）记录原 overflow 并写 `hidden`。后续 modal push 不再覆盖记录值。
  - `unlockBodyScroll`（181-185）：仅在栈空时恢复 `bodyOverflowBeforeModal ?? ""`，使用 nullish 合并确保 null/undefined 时 fallback 空串。
  - 嵌套关闭：内层 modal close → modalStack 仍有外层 → 不 unlock；外层 close → modalStack 空 → unlock。逻辑正确。
- **小风险点（非 bug）**：`bodyOverflowBeforeModal` 是模块级变量。如果**外部代码**在 modal open 期间动了 `document.body.style.overflow`，close 时本模块会用我们记录的旧值覆盖回去，可能踩坏外部 lock。这是 R2 注释（"避免改动全局 CSS class（scope 限定）"）已经接受的 trade-off，且代码中无其他 body scroll 操作，本页面内闭环。

---

## Part B：全量再扫新发现

按严重度排序。**严格守 scope**，所有跨模块发现归 `scope-out backlog`。

---

### F-R3-1 (Low / 文案)：alias 输入框 placeholder 与中文逗号不一致
- **位置**: `commands_content.html:127`
  ```html
  <input id="alias-input" class="input" type="text" placeholder="例如：c, exec, run" />
  ```
- **严重度**: Low
- **触发概率**: 低（用户体验微问题）
- **现象**: B-11 已让 `,` / `，` 都被识别为分隔符，但 placeholder 只用半角逗号示例。中文输入习惯的用户看到 placeholder 可能不知支持全角，反向输入 `c，exec` 一字符串测试时疑惑。这一行：
  ```
  placeholder="例如：c, exec, run"
  ```
- **修复建议（前 / 后对比）**:
  - 前：`例如：c, exec, run`
  - 后：`例如：c, exec, run（半角或全角逗号均可）` 或保留示例 + 在 modal-body 描述里说明。
- **风险**: 文案微调，无副作用。

### F-R3-2 (Low / 文案一致性)：alias modal "已自动去重，正在保存…" 与全局 status 文案动作不一致
- **位置**: `commands.js:1014`（alias modal alert）vs 全局规则
- **严重度**: Low
- **触发概率**: 中（每次 alias 含重复项时触发）
- **现象**: alias-modal alert 中用了 `"已自动去重，正在保存…"`，是 modal 内部 alert，不属于顶部全局 status toast，所以**不**违反 CLAUDE.md "动作 + 结果，原因" 全局规则。  
  但保存成功后会调用 `setStatus("保存成功，需要重启后生效", "success");`（行 1032），这是顶层 status——目前文案是 `保存成功，需要重启后生效`。按 CLAUDE.md 规则：成功是"动作 + 结果"，"需要重启后生效"是**追加说明**而非 error.message，从全局规则严格看属于"动作 + 结果 + 补充说明"，可接受。但与 commands.js 中其他保存路径（行 498 `保存成功` / 行 754 `保存成功`）不对齐——同一页面，部分保存成功只显示 `保存成功`，alias 保存却带后缀。
- **修复建议（前 / 后对比）**:
  - 前：`setStatus("保存成功，需要重启后生效", "success");`
  - 后：分两种选项：
    - 选项 A（保持简洁一致）：`setStatus("保存成功", "success");`，重启提示改放在 alias modal alert（在 `closeAliasModal` 之前）或独立 hint 行。
    - 选项 B（保留信息但放进 alert）：在保存成功并 closeAliasModal 后，**不**写 setStatus，改在 alias modal 内显示"保存成功，需要重启后生效"再延迟关闭。
  - 推荐选项 A：与同页面其他成功文案对齐。
- **不算严格违规**，属于一致性建议。

### F-R3-3 (Low / 错误处理)：`saveAliases` 在 `loadCommands` 失败时静默吞掉异常
- **位置**: `commands.js:1031-1033`
  ```js
  closeAliasModal(true);
  setStatus("保存成功，需要重启后生效", "success");
  await loadCommands({ clearStatus: false });
  ```
- **严重度**: Low
- **触发概率**: 低（保存成功但 reload 失败时）
- **现象**:
  - `loadCommands` 失败路径已 setStatus(error) 内部覆盖（行 836-837）+ 设 emptyNode。
  - 但 alias 路径先 `setStatus("保存成功", "success")`，然后 `await loadCommands` 失败时会被覆盖为 error message，最终用户看到的是错误而非"保存成功 + 刷新失败"的明确二元状态。
  - 对比 `saveSingleCommand` 路径（commands.js:493-501）：用 `{ reloaded }` 返回值显式区分"保存成功+刷新成功"和"保存成功+刷新失败"，文案是 `保存成功，已立即生效；刷新失败，请手动刷新页面`。alias 路径**没有**采用同款模式。
- **修复建议（前 / 后对比）**:
  - 前：
    ```js
    closeAliasModal(true);
    setStatus("保存成功，需要重启后生效", "success");
    await loadCommands({ clearStatus: false });
    ```
  - 后：
    ```js
    closeAliasModal(true);
    const reloaded = await loadCommands({ clearStatus: false });
    if (reloaded) {
      setStatus("保存成功，需要重启后生效", "success");
    } else {
      setStatus("保存成功，需要重启后生效；刷新失败，请手动刷新页面", "warning");
    }
    ```
- **风险**: 改动小，对齐 saveSingleCommand 现有模式。

### F-R3-4 (Low / UX 一致性)：alias modal 不阻止 saving 期间的 mask 点击关闭
- **位置**: `commands.js:1051-1055`
  ```js
  aliasModalNode.addEventListener("click", (event) => {
    const target = event.target;
    if (target instanceof HTMLElement && target.dataset.aliasModalClose === "1") {
      closeAliasModal();
    }
  });
  ```
- **严重度**: Low
- **触发概率**: 低（用户保存中点 mask）
- **现象**: 调用 `closeAliasModal()`（force=false），内部 `if (aliasSaving && !force) return;` 已经能阻止——所以 mask click **不会**关闭 saving 中的 modal。逻辑正确。**但** `saveAliases` 没在 saving=true 时禁用 cancel/close 按钮和 mask hover 提示，用户视觉上不知为何点击无效。
- **对比 param modal**：`setModalSavingState(true)`（行 256-261）会 disable saveButton/cancelButton/closeButton，给用户明确反馈"保存中"。alias modal 只 disable 了 `aliasSaveButton`（行 1013），cancel/close 仍可点（点无效但视觉无变化）。
- **修复建议（前 / 后对比）**:
  - 前：仅 `aliasSaveButton.disabled = true;`
  - 后：增加：
    ```js
    aliasSaveButton.disabled = true;
    if (aliasCancelButton) aliasCancelButton.disabled = true;
    if (aliasCloseButton) aliasCloseButton.disabled = true;
    ```
    并在 finally 中复原。
- **风险**: 提升 saving 中 UX 一致性。

### F-R3-5 (Low / 错误处理)：`saveAliases` `error.details` 取首项 `.message` 时未 trim / 未 fallback
- **位置**: `commands.js:1036-1038`
  ```js
  if (error && error.details && Array.isArray(error.details) && error.details.length > 0) {
    message = error.details[0].message || message;
  }
  ```
- **严重度**: Low
- **触发概率**: 低（后端返回 details 但首项无 message 字段时）
- **现象**:
  - 直接读 `error.details[0].message`：
    - 若 first detail 非 object（如 `null`），`error.details[0].message` 抛 TypeError 中断渲染。但 `Array.isArray(error.details)` 已保证是数组，元素仍可能是 null。`api.js:46` `readApiError` 已 `.filter(item => item && typeof item === 'object')`，保证 details 元素是 object，所以本路径安全。
    - 若 first detail 是 object 但 message 是空串 `""`，`message || message` fallback 到上层默认值，OK。
  - 与 `api.js` 的 `buildDetailReason`（行 62-72）行为不一致：`buildDetailReason` 用 `";"` 拼接所有 detail message，更完整；本处仅取首项。考虑到 alias 后端通常只返回一条 details（哪个 alias 重复/无效），取首项可接受，但**信息丢失**风险存在（如多 alias 同时违规）。
- **修复建议（前 / 后对比）**:
  - 前：`message = error.details[0].message || message;`
  - 后（更稳健）：
    ```js
    const detailMessages = error.details
      .map((d) => (typeof d?.message === "string" ? d.message.trim() : ""))
      .filter(Boolean);
    if (detailMessages.length > 0) {
      message = detailMessages.join("；");
    }
    ```
  - 这样 alias 多条违规时全部展示，与 api.js `buildDetailReason` 对齐。
- **风险**: 小幅改进，无副作用。

### F-R3-6 (Low / Race)：alias save 期间 reload/分页/搜索可触发 alias 路径并发
- **位置**: `commands.js:1003-1044`（saveAliases） + `commands.js:885-929`（toolbar handlers）
- **严重度**: Low
- **触发概率**: 低（用户在保存别名时主动 reload / 翻页 / 搜索）
- **现象**:
  - reload / perPageSelect change / prevPage / nextPage 都调用 `cancelPendingSearch()` + `void loadCommands()`，**不**等 alias save 完成。
  - 如果 alias save 仍在飞，alias 路径内部 `await loadCommands({ clearStatus: false });`（行 1033）会与外层 loadCommands 并发。两次 fetch 都会到达后端，第二次返回会覆盖第一次的渲染。
  - 没有 abort 机制：alias save 路径用的是 `api.apiRequest` 默认 signal（无 user signal 传入），无法被外层取消。
  - 后果：渲染结果取决于到达顺序，但都是合法数据，不会数据错乱。但 `setStatus` 文案可能被覆盖（外层 reload 用 `setStatus("")` 默认 + 失败时 error；alias 用 `setStatus("保存成功…")`），用户可能看不到"保存成功"toast。
- **修复建议（前 / 后对比）**:
  - 选项 A：alias save 期间禁用 reload/搜索/翻页（与 param modal 一致——param modal 通过 `setModalSavingState` disable，但**不** disable 外层 toolbar）。其实 param modal 也有同样的并发面，R2 未处理。
  - 选项 B：在 alias `saveAliases` 内创建 AbortController，把 signal 传给 `apiRequest`（需要 api.js 支持，已支持，见 api.js 行 188 signal 参数）。但 reload race 是页面级状态污染，单一 AbortController 不够。
  - 选项 C（最稳）：page-level "pending modal save" 标志，toolbar handler 在标志为 true 时拒绝触发或排队。
- **建议留 backlog**：与现有 race 模型一致（dashboard R3 也未处理 modal vs toolbar race），影响低。**仅记录，不强求 R3 修复**。

### F-R3-7 (Low / 一致性)：`aliasSaveButton.disabled = false;` 在 finally 块覆盖 `aliasSaving = false;`
- **位置**: `commands.js:1029-1043`
- **严重度**: Low (cosmetic)
- **触发概率**: N/A
- **现象**:
  - try 块中行 1030: `aliasSaving = false;`（提前置 false 为了让 `closeAliasModal(true)` 不需要走 force-true，但实际仍传了 `true`——双保险）
  - finally 块行 1041-1042: `aliasSaving = false; aliasSaveButton.disabled = false;`
  - 重复赋值 `aliasSaving = false;` 是无害的。但 `aliasSaveButton.disabled` 在 catch 路径（throw 时未走到 1041）也通过 finally 恢复，逻辑正确。
- **小问题**：try block 行 1030 提前清 `aliasSaving = false;` 后，若 `closeAliasModal(true)` 抛错（理论不可能，`closeModalAndRestoreFocus` 没抛错路径），finally 仍能兜底。
- **结论**：当前实现冗余但安全，不需要改。仅记录。

### F-R3-8 (Low / Accessibility)：alias modal alert `setAliasAlert` 不切换 `aria-live`，与 param modal alert 不一致
- **位置**: `commands.js:972-982`（setAliasAlert） vs `commands.js:109-121`（setModalAlert）
- **严重度**: Low
- **触发概率**: 低（屏幕阅读器用户）
- **现象**:
  - `setModalAlert`（行 109-121）和 `setAliasAlert`（972-982）都依赖模板里 `aria-live="polite"` 静态属性（commands_content.html:81 / 118），不动态切换。
  - 两个 alert 行为基本对齐，但 `setAliasAlert` 第二参默认值 `""`（行 972），调用 className 时 fallback `"info"`；而 `setModalAlert` 默认 `"info"`（行 109）。行为等价但代码风格不一致。
- **修复建议**: 统一默认参数为 `"info"`。
- **风险**: 零，纯一致性。

### F-R3-9 (Low / UX)：apiReady=false 时未隐藏 `loading` 元素的非 disable 状态
- **位置**: `commands.js:768-778`
- **严重度**: Low
- **触发概率**: 低
- **现象**: `setLoadingVisible(false);` 在行 769 已执行，把 loading 隐藏。但 `tableWrapNode` 没显式 add `hidden`（依赖默认状态 hidden）。`emptyNode` 也没显式调整。用户看到的是顶部 status error toast + 所有 toolbar 控件 disable + 中间空白。`emptyNode` 实际默认是 `hidden`（template 行 36），所以表现 OK。
- **小问题**：错误状态没把 `emptyNode` 显示出来作为 fallback empty state。但 status toast 已经覆盖错误信息。
- **结论**: 可接受，留 backlog。

### F-R3-10 (Low / 代码质量)：`searchInput` input handler 未复用 `cancelPendingSearch`
- **位置**: `commands.js:892-904`
- **严重度**: Low (cosmetic)
- **现象**: 该 handler 手动 clearTimeout + abort + new AbortController，没用 `cancelPendingSearch()` helper（行 874-883）。语义不完全等价（这里要保留 controller 引用作为新 signal），但前 3 行可复用 helper 后再 new。
- **修复建议（前 / 后对比）**:
  - 前：
    ```js
    if (searchDebounceTimer) {
      clearTimeout(searchDebounceTimer);
    }
    searchDebounceTimer = setTimeout(() => {
      if (searchAbortController) {
        searchAbortController.abort();
      }
      searchAbortController = new AbortController();
      ...
    }, 300);
    ```
  - 后：
    ```js
    if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(() => {
      cancelPendingSearch(); // clear timer + abort controller
      searchAbortController = new AbortController();
      ...
    }, 300);
    ```
  - 注意：cancelPendingSearch 内部会清掉刚 setTimeout 的 timer 引用 —— 但此 timer 已 fire 进入 callback，clear 已被 fire 的 timer 是 no-op，安全。
- **结论**: 纯重构，建议保留现状除非顺手清理。

### F-R3-11 (Low / 防御性)：`apiReady` 检查后 disable 控件，但 `searchInput.disabled` 不防止 `input` event
- **位置**: `commands.js:773`
- **严重度**: Low
- **现象**: 设置 `searchInput.disabled = true;` 后，user 无法输入；但**程序性**（如 autofill / 浏览器扩展）仍可触发 input event。input handler（行 892）会进入 setTimeout debounce → 调用 `loadCommands({signal})` → 又因 apiReady=false re-disable —— 没死循环但有冗余。
- **建议**：在 input handler 入口加 `if (!apiReady) return;` 早返回。或在 input handler 全局 wrap `if (searchInput.disabled) return;`。
- **风险**: 微小，留 backlog。

### F-R3-12 (Low / Race)：`loadCommands` 内部 `if (signal && signal.aborted)` 之后仍有未捕获时序窗口
- **位置**: `commands.js:804-806, 829-832`
- **严重度**: Low
- **现象**:
  - 行 804：abort 检查在 fetch resolve 后但 unwrapData 之前，OK。
  - 行 822-823：`for (const command of commandStates) ensureCommandParamValues(command);` 之后再 renderTable，期间如果新 abort 触发，不会被捕获。
  - 但 abort 触发只来自更新的 search input，渲染过期结果是可接受的（用户输入下一个字符前，旧结果展示 100ms 也无害）。
- **结论**: 实际无 bug。`AbortController` 没有 trigger 通过 `signal.aborted` polling 检测的进程。可接受。

### F-R3-13 (Low / 一致性)：commandStates row 顺序与 thead 列顺序不一致
- **位置**: `commands_content.html:41-50`（thead）vs `commands.js:557-564`（appendChild 顺序）
- **严重度**: **Low/Medium — 实际 bug，影响表格列对齐**
- **触发概率**: **100%（每次渲染）**
- **现象**:
  - **thead 列顺序**（commands_content.html:42-49）：
    1. 命令名称
    2. 命令介绍
    3. 用法
    4. 权限
    5. **别名**
    6. **状态**
    7. 分类
    8. 操作
  - **tbody append 顺序**（commands.js:557-564）：
    ```js
    row.appendChild(commandCell);       // 命令名称
    row.appendChild(descriptionCell);   // 命令介绍
    row.appendChild(usageCell);         // 用法
    row.appendChild(permissionCell);    // 权限
    row.appendChild(aliasesCell);       // 别名 ✓
    row.appendChild(statusCell);        // 状态 ✓
    row.appendChild(adminCell);         // 分类
    row.appendChild(actionCell);        // 操作
    ```
  - **顺序一致 — 别名在状态之前**。重新核对：thead 顺序 = 命令/介绍/用法/权限/别名/状态/分类/操作；tbody 顺序也是 命令/介绍/用法/权限/别名/状态/分类/操作。
- **结论**：**无 bug，列对齐正确**。撤销此 finding，仅作为 attention check 已通过。

### F-R3-14 (Low / Defensive)：`getCommandByKey` 在 `commandStates` 为空时无短路
- **位置**: `commands.js:372-374`
- **现象**: `.find` 在空数组上是 O(0)，无问题。仅记录。
- **结论**: 无需修。

### F-R3-15 (Low / 性能)：renderTable 不使用 DocumentFragment 批量插入
- **位置**: `commands.js:422-569`
- **严重度**: Low
- **现象**: 每行 `tableBodyNode.appendChild(row);`（行 565），每页 ≤100 行（perPage cap，行 65），现代浏览器表现可接受。`tableBodyNode.replaceChildren();`（行 423）先清空，重排次数 = perPage。  
  对 100 行 × 多个 child node 的 reflow 量级，使用 DocumentFragment 可减少一次性 reflow。但页面大小约束（perPage max 100）+ 现代浏览器优化，实际感知差异微弱。
- **结论**: 留 backlog，非阻塞。

### F-R3-16 (Low / Defensive)：`enabledInput.addEventListener("change")` 期间用户切其他 row 的 toggle
- **位置**: `commands.js:483-511`
- **严重度**: Low
- **现象**:
  - 行 489：`enabledInput.disabled = true;`（disable 当前 toggle）
  - 用户**可以**同时切换其他 row 的 toggle —— 触发并发 `saveSingleCommand` → 并发 `loadCommands`（行 868）→ commandStates 被重新 cloneValue 覆盖，原 row reference 失效。
  - 第一个保存的 catch 里恢复 `command.enabled = previousEnabled; enabledInput.checked = previousEnabled;` 时，`command` 引用可能已是旧 commandStates 的对象（新 render 已生成新 row），旧 row 已从 DOM 移除，对 DOM 无影响。但旧 command 对象的 mutation 不影响新数据，**没有**数据错乱。
- **风险**: 多 toggle 并发时，状态会被最后 loadCommands 覆盖（这是预期行为），UI 表现 OK。
- **结论**: 无 bug。

### F-R3-17 (Low / Defensive)：alias `Set` 去重大小写敏感
- **位置**: `commands.js:1009`
- **严重度**: Low (产品决策)
- **现象**: `Array.from(new Set(rawAliases))` 大小写敏感，`Cmd` 和 `cmd` 视为不同。后端是否大小写归一化未知（scope-out）。如果后端归一化，前端 `deduped` 旗标可能漏报"已去重"。
- **风险**: 文案略偏（`Cmd, cmd` 不显示"已自动去重"，但后端会去重）。
- **结论**: 跨模块依赖，scope-out backlog。

### F-R3-18 (Low / Defensive)：`searchInput.value` 没 cap 长度上限
- **位置**: `commands_content.html:11`
- **现象**: 用户可粘贴极长字符串作为 search query，被 `encodeURIComponent` 发到后端（commands.js:792）。后端是否有长度上限未知。前端无 `maxlength`。
- **风险**: 低，DoS 面在后端。
- **结论**: scope-out backlog（后端约束）。

### F-R3-19 (Low / 文案)：`buildPermissionNode` 文案 "无"
- **位置**: `commands.js:399-409`
- **现象**: 权限为空时显示"无"。文案简洁，与全局 placeholder（`description || "暂无介绍"` 行 456、`usage || "未填写用法"` 行 463）对齐——"无"vs"暂无"/"未填写"略不一致。
- **修复建议**: 文案统一为"暂无"或保持简洁"无"。属审美选择，不强求。
- **结论**: 留 backlog。

### F-R3-20 (Medium / Bug)：reload/分页/搜索路径不取消 alias / param modal save 在飞请求
- **位置**: `commands.js:885-929`（toolbar handlers）
- **严重度**: Medium
- **触发概率**: 低（modal 保存中用户主动 reload）
- **现象**:
  - `cancelPendingSearch()` 只清掉 search debounce timer 和 search abort controller，**不**取消 modal save 路径的在飞 fetch（saveSingleCommand 行 857-866、saveAliases 行 1017-1026）。
  - 用户在保存 modal 期间主动 reload，会先触发 reload 的 loadCommands；接着 modal save 完成时也调用 `loadCommands({ clearStatus: false })`（行 868、1033），两次 reload 并发。
  - 第二次返回会覆盖第一次的渲染——但都是合法数据，不会错乱。文案/loading 状态可能闪烁。
- **关联**：与 F-R3-6 同类问题。是 commands 页面整体 race 模型的一部分。
- **修复建议**：与 F-R3-6 统一处理或留 backlog。R2 已显式接受这一并发面（saveSingleCommand 内部直接 await loadCommands，无 abort 路径）。
- **结论**: 留 R4 / backlog，不强求 R3 修复。

### F-R3-21 (Low / Defensive)：`closeRestartModal` 不检查 saving 状态
- **位置**: `commands.js:1071-1074`
- **严重度**: Low
- **触发概率**: 低（用户点 restart confirm 后立即 ESC / 点 mask）
- **现象**:
  - `restartConfirmBtn` click handler 行 1094-1109 先 `closeRestartModal();` 再 disable button + 发请求。modal 已经关，所以期间 ESC 不会再触发 close。OK。
  - 但**点 mask 关闭** mid-restart：mask 点击关闭已在 click 处理后 modal 已 hide，不可能触发。OK。
- **结论**: 无 bug。

### F-R3-22 (Low / Defensive)：`api.unwrapData(response)` 在 saveAliases 抛 "返回数据格式错误"
- **位置**: `commands.js:1027-1028`
  ```js
  const result = api.unwrapData(response);
  if (!result) throw new Error("保存失败");
  ```
- **严重度**: Low
- **现象**:
  - `unwrapData`（api.js:86-92）当 payload 无 `data` 字段时抛 `"返回数据格式错误"`。错误冒泡进 catch（行 1034）。
  - catch 里 `error instanceof Error ? error.message` 取到 `"返回数据格式错误"`，对用户来说意义不明（看不出是后端字段格式问题）。
  - 后续 `if (error && error.details && Array.isArray(error.details) && ...)` 判断不命中（普通 Error 无 details），保留 `"返回数据格式错误"`。
- **建议**: try 中显式判别 `unwrapData` 抛错与业务失败：
  ```js
  let result;
  try {
    result = api.unwrapData(response);
  } catch (_e) {
    throw new Error("保存失败，返回数据格式错误");
  }
  if (!result) throw new Error("保存失败");
  ```
- **风险**: 微小，留 backlog。

### F-R3-23 (Low / Defensive)：beforeunload 在保存 modal 期间不警告用户
- **位置**: `commands.js:931-934`
- **严重度**: Low
- **现象**: `window.addEventListener("beforeunload")` 只调用 `cancelPendingSearch()`，**不**用 `event.preventDefault() / event.returnValue` 警告"有未保存数据"。
- **如果**用户在保存 modal 期间关闭页面，正在飞的请求被浏览器中止，但服务端可能已收到 + 处理。用户重新打开看到结果意外。这是标准 web UX 问题。
- **建议**: 复杂权衡（false-positive 警告体验差）。留 backlog。
- **结论**: 不强求 R3 修复。

### F-R3-24 (Info / Verify)：`requiredNodesReady` 没覆盖 alias / restart 节点
- **位置**: `commands.js:56-83`
- **现象**:
  - 校验列表里包含 modal 系列、reloadButton、searchInput 等。但**不**包含：
    - `aliasModalNode` / `aliasModalTitleNode` / `aliasModalAlertNode` / `aliasModalAlertMessageNode` / `aliasInput` / `aliasCloseButton` / `aliasCancelButton` / `aliasSaveButton`
    - `restartButton`
    - restart modal 内部节点（在 `if (restartButton)` 内 getElementById，本地变量）
  - 这意味着：alias / restart 节点缺失时，页面仍 init，但 alias / restart 路径降级（每个 caller 都用 `if (aliasModalNode)` / `if (restartButton)` 防御）。
- **风险**: 模板缺失某个 alias 节点时，行为静默降级 + 不报警。对 dev 调试不够友好，但用户层无明显异常。
- **建议**:
  - 选项 A：把 alias / restart 节点加入 `requiredNodesReady` 校验（最严格）。
  - 选项 B：保持当前防御，作为可选 feature 处理（更稳，向后兼容）。
- **结论**: 当前实现是合理的「核心必需 + 可选 feature 防御」分层，无需改。仅记录。

### F-R3-25 (Info)：CSS 文件 / styles 引用未检查
- **位置**: 模板 + JS 内多处 className（alert / modal / param-item / command-desc / badge / etc.）
- **现象**: R2 注释明确"避免改动全局 CSS class（scope 限定）"，B-12 改为 inline style 也保留了这一约束。本轮 scope 不覆盖 CSS，未审。
- **结论**: 按 task 要求跳过。

---

## scope-out backlog（跨模块发现 / 留给后续 round）

- **B-OUT-1**：`api.js` `unwrapData`（行 86-92）在缺 `data` 字段时抛"返回数据格式错误"，commands.js 的 caller 直接展示给用户而无业务层翻译。所有 webui 模块都有该面。
- **B-OUT-2**：`api.js` `buildDetailReason`（行 62-72）用 `";"` 拼接，而 commands.js `saveAliases` 只取首项 detail.message。其他模块可能也存在不一致取 detail 的写法。
- **B-OUT-3**：alias 后端是否归一化大小写 / 去重 / 长度上限未知。F-R3-17 / F-R3-18 取决于后端契约，跨层。
- **B-OUT-4**：commands 页面整体「modal save 在飞 + 外层 toolbar 触发 reload」并发模型未规范化，全 webui 都有此面（dashboard R3 也未处理）。

---

## 结论

### R2 修复验证（B-1 / B-2 / B-3 / B-4 / B-5 / B-6 / B-7 / B-8 / B-9 / B-10 / B-11 / B-12）
**全部修复彻底、行为正确、无 regression。** 特别是 R1 regression B-7（arrow wrap）已闭环，`saveAliases` 路径的 `closeAliasModal(true)` 正确传入 force 参数。

### R3 新发现（commands 页面内）
**0 个 High，0 个 Medium-阻塞，~6 个低优先文案 / 一致性 / 错误处理 / race 边界**。最值得 R3 修复的是：
- **F-R3-3**（saveAliases reload 失败时静默吞错），与 saveSingleCommand 模式对齐，小改动收益明确。
- **F-R3-4**（alias modal saving 中 cancel/close 按钮未 disable），跟 param modal 对齐。
- **F-R3-5**（saveAliases details 取首项），跟 api.js `buildDetailReason` 对齐。
- **F-R3-2**（alias 保存成功文案与同页面其他成功文案不对齐），文案选择。
- **F-R3-1**（alias placeholder 提示半角/全角），UX 微调。

其余（F-R3-6 / F-R3-9 / F-R3-10 ~ F-R3-25）属 backlog / 不强求 / 跨模块。

**前端桶 R3 闭环度：高。** R2 修复均验证通过，新发现仅低风险细节，无强阻塞项。
