# WebUI 命令配置页面审计 — 主代理二次审核

## 整体结论

**0 Critical / 0 High**，主要是中等严重度的输入校验、文案违规、性能优化空间。前端**无 XSS / CSRF / DOM 注入风险**，textContent 使用规范、URL 编码到位、SameSite=Lax 已覆盖 CSRF。

## 终判清单

### 🔶 Medium / P0-P1（必修，4 项）

| ID | 文件 / 行号 | 一句话 | 验证 |
|---|---|---|---|
| **A-3** 后端 | `webui_commands.py:84, 144` | `command_key` 路径参数无白名单 / 长度上限校验 → 攻击者可发 `/webui/api/commands/<10MB>` 进入 DB query + 日志注入面 | ✅ `command_key: str` 直接透传，仅下游 `.strip()` |
| **A-5** 后端 | `webui_commands.py:151-160` | aliases 数组无元素数与单元素长度上限 → 可提交 `["a"*10000] * 10000` 污染 matcher 表 | ✅ 仅 `isinstance(raw_aliases, list)` 校验 |
| **P0-T1** 前端 | `commands.js:343, 606` | "列表刷新失败" / "参数保存成功" 含对象名违反 CLAUDE.md | ✅ 字符串字面值精确匹配 |
| **P1-Race** 前端 | `commands.js:708` | `searchInput.addEventListener("input", ...)` 无 debounce + 无 AbortController → 请求风暴 + 结果 race | ✅ 行号匹配 |

### 🟢 Low / P2（建议修，7 项）

| ID | 文件 / 行号 | 一句话 |
|---|---|---|
| **A-4** | `webui_commands.py:91-95` | `param_values` payload 无大小限制（内存放大） |
| **C-1** | `webui_commands.py:101` | error.message "至少需要提供 enabled 或 param_values" 违反 CLAUDE.md（拼"动作"） |
| **C-6** | `webui_commands.py:159-188` | `update_aliases` 缺 `except Exception` 兜底（与 update_config 不对称） |
| **P2-T1** | `commands.js:876` | restart apiRequest 未传 `action: "重启"` → fallback 文案缺动作前缀 |
| **P2-T2** | `commands.js:434` | "当前命令没有可配置参数。" 在 modal body + modal alert 重复显示 |
| **P2-A** | `commands_content.html:74, 92, 111` 三个 modal | focus trap / 自动 focus / 关闭返还焦点 全缺失（dashboard R1+R2 焦点规范应对齐） |
| **P2-ESC** | `commands.js:754, 842 vs restart modal` | restart-confirm-modal 缺 ESC 关闭（其他 2 个 modal 已实现） |
| **P2-Loading** | `commands_content.html:35` | loading 节点无 `aria-live` / `aria-busy`（dashboard R1+R2 已规范） |

### 🟢 Low / P3（信息 / 排版，可不修）

- **A-6** `param_values` route 层缺 isinstance 短路（与 aliases route 风格不一致）
- **B-1** `list_command_configs` 全量加载（当前 < 100 命令量 OK，未来需 DB 分页）
- **C-3** aliases PATCH 全量替换实际是 PUT 语义（文档化即可）
- **C-2** PATCH 未变化时仍写 `updated_at`
- **C-5** route 通过中文 message 字符串硬编码识别 404 / 409（属 service 层耦合，**不在本任务 scope**，归 `command_config.py` 已闭环 backlog）
- **D-2 / D-3 / D-6** 日志细节（reason 截断 / logger.exception 冗余 / 中英文空格）
- **P3 排版** `commands.js:333/595/800` "正在保存..." ASCII → `…` (U+2026)
- **P3 排版** `commands.js:564/575/586` `${paramLabel}: ${message}` 半角冒号 → `：` 全角
- **P3 一致性** `commands.js:786-790` alias modal 缺 "saving 中阻止关闭"（与 param modal 不对称）
- **P3 一致性** `commands.js:270` "暂无可配置命令。" 带句号 vs `commands_content.html:36` 不带句号

## 关键修复前后对比

### P0 文案 (commands.js)

**修复前**：
```javascript
// :343
setStatus("保存成功，已立即生效；列表刷新失败，请手动刷新页面确认最新状态", "warning");
// :606
setStatus("参数保存成功，已立即生效；列表刷新失败，请手动刷新页面确认最新状态", "warning");
```

**修复后**：
```javascript
// :343
setStatus("保存成功，已立即生效；刷新失败，请手动刷新页面", "warning");
// :606
setStatus("保存成功，已立即生效；刷新失败，请手动刷新页面", "warning");
```

**理由**：CLAUDE.md "用户操作反馈文案规范" 反例 "删除服务器成功" 应为 "删除成功"。"参数 / 列表" 都是对象名违规。

### A-3 command_key 校验 (webui_commands.py)

**修复前**：
```python
async def webui_commands_api_update(command_key: str, request: Request):
    # command_key 直接透传到 DB query + 日志
    logger.warning(f"...command_key={command_key}...")
```

**修复后**：
```python
import re
_COMMAND_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.\-/]{1,64}$")

async def webui_commands_api_update(command_key: str, request: Request):
    if not _COMMAND_KEY_PATTERN.fullmatch(command_key):
        return api_error(400, "invalid_request_parameter", "命令 key 格式错误")
    ...
```

### A-5 aliases 数组校验 (webui_commands.py)

**修复前**：
```python
if not isinstance(raw_aliases, list):
    return api_error(422, "validation_error", "...")
# 无 len 上限
```

**修复后**：
```python
if not isinstance(raw_aliases, list):
    return api_error(422, "validation_error", "aliases 必须是数组")
if len(raw_aliases) > 32:
    return api_error(422, "validation_error", "别名数量上限 32")
for a in raw_aliases:
    if not isinstance(a, str) or len(a) > 32:
        return api_error(422, "validation_error", "单个别名长度上限 32")
```

### P1 search debounce + abort (commands.js)

**修复前**：
```javascript
searchInput.addEventListener("input", () => {
    currentPage = 1;
    void loadCommands();
});
```

**修复后**：
```javascript
let searchDebounceTimer = null;
let searchAbortController = null;

searchInput.addEventListener("input", () => {
    if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(() => {
        if (searchAbortController) searchAbortController.abort();
        searchAbortController = new AbortController();
        currentPage = 1;
        void loadCommands(searchAbortController.signal);
    }, 300);
});

// loadCommands(signal) 内透传 signal 给 apiRequest
```

## 主代理拒绝 / scope-creep 避免

- **C-5 跨模块字符串耦合**：子代理已明确标注 "fix 涉及 `command_config.py`，不在本任务范围" —— 主代理同意，记录为 service 层 backlog
- 子代理（含本任务）均**未扩散**到其他 webui 模块 / shell / api.js / 基础设施层 —— scope 严格遵守

## 修复推荐梯队

| 选项 | 内容 | 行数 |
|---|---|---|
| **A** | 4 项必修（A-3 + A-5 + P0-T1 + P1-Race）| ~30 行 |
| **B** | A + 7 项 P2（含 modal focus / aria / restart 一致性）| ~80 行 |
| **C** | 全修（B + P3 排版/一致性）| ~120 行 |
| **D** | 按 finding ID 逐条选 |

**dashboard R3 scope 失控教训**：本任务 finding 全部限定在 commands 3 个前端文件 + 1 个后端文件，不报跨模块项（C-5 已明确放回 service 层 backlog）。
