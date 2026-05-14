# commands R2 复审 — 主代理二次审核

## 整体结论

**R1 ~20 项修复整体生效**，但 **R1 引入 1 处真实 regression（B-7）+ 3 处 P1 边界遗留**。R2 必修。

后端 0 Critical / 0 High（commands 文件内）/ 3 Medium 可修；前端 0 Critical / **4 P1 必修**（其中 B-7 是 R1 regression）+ 5 P2 + 6 P3。

## R1 修复复审：8/10 PASS + 2 边界遗留

| R1 修复 | 状态 |
|---|---|
| A-3 command_key regex 白名单 | ✅ PASS（实测 87 plugin command_key 全部覆盖，最长 33 字符） |
| A-4 _validate_param_values | ✅ PASS（unicode 安全，three 阈值合理） |
| A-5 _validate_aliases_list | ✅ PASS |
| A-6 isinstance 短路 | ✅ PASS |
| C-6 except Exception 兜底 | ✅ PASS |
| D-2 str(exc)[:500] | ✅ PASS（Python code-point 切片不会乱码） |
| _map_validation_error helper | ✅ PASS |
| P0-T1 文案 | ✅ PASS（4 处 setStatus 全字符一致） |
| P1-Race debounce + AbortController | ⚠️ 通过但有 P1 race 边界（B-1）|
| P2-A modal focus | ⚠️ 通过但 4 处 P1 边界（B-3 / B-4 / B-5 / B-6） |
| P2-ESC / P2-Loading / P2-T1/T2 | ✅ PASS |
| P3 排版 / 一致性 | ⚠️ alias saving guard 有 R1 regression（B-7） |

## R2 关键新发现（行号 verify）

### 🔴 P1 必修（4 项，其中 1 项是 R1 regression）

#### B-7 ⭐【R1 引入 regression】closeAliasModal 直接绑定 click listener 绕过 saving guard

**文件 / 行号 verify ✅**：`commands.js:942-943`

```javascript
if (aliasCancelButton) aliasCancelButton.addEventListener("click", closeAliasModal);
if (aliasCloseButton) aliasCloseButton.addEventListener("click", closeAliasModal);
```

**Bug 链**：
1. `closeAliasModal(force = false)` 函数签名（line 894）
2. `if (aliasSaving && !force) return;` saving guard（line 896）
3. **listener 绑定时传入 MouseEvent 当 force 参数**
4. `!MouseEvent === false`（MouseEvent 是 truthy 对象）
5. 等价 `aliasSaving && false === false` → guard 不生效
6. 用户在 alias 保存中点 "取消" / "✕" → modal 直接关闭，丢失 saving 状态

**对比 param modal**（line 847-853）：用 arrow function `() => closeParamModal()` 包裹不传参，guard 正常生效。

**修复前**：
```javascript
if (aliasCancelButton) aliasCancelButton.addEventListener("click", closeAliasModal);
if (aliasCloseButton) aliasCloseButton.addEventListener("click", closeAliasModal);
```

**修复后**（1 行 ×2）：
```javascript
if (aliasCancelButton) aliasCancelButton.addEventListener("click", () => closeAliasModal());
if (aliasCloseButton) aliasCloseButton.addEventListener("click", () => closeAliasModal());
```

**触发概率**：中（用户保存 alias 时不耐烦点取消的场景）；触发后行为不可预期。

---

#### B-1 debounce 期间点击 reload/分页 → 旧 debounce 触发过期请求

**位置**：`commands.js:801-803, 821-825, 827-841`

**修复前**：reloadButton / perPageSelect / prev / next click handler 不取消 pending debounce，搜索 250ms 后用户点刷新 → reload 触发 loadCommands → 50ms 后 debounce timer 触发又一个 loadCommands → race。

**修复后**：抽 `cancelPendingSearch()` helper：
```javascript
const cancelPendingSearch = () => {
    if (searchDebounceTimer) { clearTimeout(searchDebounceTimer); searchDebounceTimer = null; }
    if (searchAbortController) { searchAbortController.abort(); searchAbortController = null; }
};
// 各 click handler 内：cancelPendingSearch() 后再 loadCommands()
```

---

#### B-3 previousFocus 节点销毁时 fallback 缺失

**位置**：`commands.js:177`

**修复前**：`document.contains(previousFocus)` 失败时静默丢焦点到 body。

**触发场景**：用户在 param modal 已打开（previousFocus = 表格"参数"按钮）→ 表格 reload 销毁该按钮节点 → 关 modal → focus 丢失。

**修复后**：fallback 到 reloadButton / `[role=main]`。

---

#### B-4 3 个 window keydown ESC listener 不阻止冒泡

**位置**：`commands.js:862-866, 953-957, 983-987`

**现状**：3 个 listener 在每次 ESC 都被全部触发，功能正确（modal 互斥打开），但脆弱。

**修复后**：合并到一个 listener + modal stack 路由。

---

### 🟡 Medium 必修（3 项，后端）

| ID | 文件 / 行号 | 一句话 |
|---|---|---|
| **M-B2** | `webui_commands.py` PATCH /commands/{key} | `{"enabled": null}` silent 接受 → no-op DB write，admin 以为切换成功 |
| **M-B3** | `webui_commands.py:150, 206, 216, 261, 272` | 5 处 logger 缺 `client_ip` / `user_agent`，与 login / dashboard M-A4 风格不一致 |
| **M-B4** | `webui_commands.py:_validate_param_values` | param_values key 字符集未限制（可含特殊字符 / 控制字符） |

---

### 🟢 P2 / P3（可选修，~11 项）

前端：B-2 / B-5 / B-6 / B-8 / B-9 / B-10 / B-11 / B-12 / B-13
后端：L-B1 (422 details shape 不一致) / L-B2 (alias strip 时序)

详见 `backend.md` / `frontend.md`。

---

### ❌ 超 scope（不修，作 backlog）

- **H-B1** 后端共享 helper（`api.js` / `read_json_object`）无 body size limit —— 涉及共享层，超本任务 scope
- **C-5** 跨模块字符串耦合 —— service 层 backlog

---

## 主代理终判

| 类别 | dashboard 桶内 | 跨桶 |
|---|---|---|
| Critical | 0 | 0 |
| High | 0 | 1（H-B1 共享层，不修） |
| **P1 / Medium** | **4 P1** + **3 Medium**（M-B2/B-3/B-4）| — |
| P2 | 5+ | — |
| P3 | 6+ | — |

**优先修 B-7（R1 regression）**：1 行 ×2，必修。

## 修复梯队

| 选项 | 内容 | 行数 |
|---|---|---|
| **A** | 仅修 B-7（R1 regression）| 2 行 |
| **B** | A + 4 P1 前端 + 3 Medium 后端 | ~50 行 |
| **C** | B + 5 P2 前端 | ~100 行 |
| **D** | 全修 | ~150 行 |
| **E** | 按 ID 逐条选 |
