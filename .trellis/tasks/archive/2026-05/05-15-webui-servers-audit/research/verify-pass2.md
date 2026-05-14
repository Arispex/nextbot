# WebUI 服务器管理页面审计 — 主代理二次审核

## 整体结论

**严重度集中在 2 个维度**：

1. **TShock token 全链路明文（High）** —— 后端 API 直返明文 + 前端 DOM 持久化 + editor 强制回填。是 servers 桶最严重的设计缺陷。
2. **文案大面积违规（High → Medium）** —— 进行时文案含对象名、verify 暴露后端 enum、空态文案不一致。

其余 Medium 集中在性能优化空间（搜索 debounce / DELETE reindex）和 plugin-config 白名单。

## 终判清单

### 🔴 High（5 项，必修）

| ID | 文件 / 行号 | 一句话 | 验证 |
|---|---|---|---|
| **A-1+A-2** Backend token 链 | `webui_servers.py:41, 176` | list/create/update 响应明文返回 token + update 强制 token 回填 → 形成"明文出库→回前端→回库"设计回环 | ✅ `"token": str(server.token)` + `server.token = validated.token` |
| **F-A-3** Frontend token DOM | `servers.js:354, 355, 514` | token 明文写 textContent + title attribute + editor input.value 回填 → 浏览器扩展 / 录屏 / DevTools 全暴露 | ✅ `tokenText.textContent = ... server.token` 等 3 处 |
| **C-2-N** verify 暴露后端 enum | `servers.js:1007` | `验证完成：${probeStatus}` 直接拼后端 enum（"Ok" / "Skipped" / "Failed"）→ 违反"API 原始字段不展示" + 不符合"动作+结果" | ✅ 字符串字面值精确匹配 |
| **C-2-G** 进行时文案含对象名 | `servers.js:659, 660, 967, 1024` | "正在删除服务器 #X" / "正在测试服务器 #X 连通性" / "正在保存地址和 Token" 全部违反 CLAUDE.md "不得含对象名" | ✅ 4 处 grep 全部命中 |
| **C-2-C** 空态文案不一致 | `servers_content.html:36` vs `servers.js:310` | HTML 初始 "暂无服务器" / JS render 后 "暂无服务器配置。"（带句号）—— 双重不一致 | ✅ 行号匹配 |

### 🟡 Medium（9 项，建议修）

| ID | 文件 / 行号 | 一句话 |
|---|---|---|
| **F-A-4** | `servers.js:759` | plugin-config password 字段（NextBot Token）回填 input.value 同 A-3 风险 |
| **B-1** | `servers.js:1063-1066` | 搜索框无 debounce + 无 AbortController（commands R1 同模式已修，servers 漏） |
| **B-2** | `servers.js:1068-1088` | 翻页 / per-page 切换无 abort，并发请求 race |
| **B-4** | `webui_servers.py:254` | `/test` endpoint 默认 5s timeout 偏紧 |
| **B-7** | `servers.js` 多处 | 缺 timeoutMs override（test/verify 应 30s+） |
| **A-4** | `webui_servers.py:362-371` | plugin-config-update 无字段白名单，客户端任意 key/value 透传 TShock |
| **C-2-D** | `servers.js:486-494` | emptyNode 错误时塞完整错误文案，与 status bar 重复 |
| **C-2-L** | `servers.js:881, 887` | "无可保存的字段" / "未修改任何字段" → 改 "没有可保存的修改" 或禁用按钮 |
| **C-2-M** | `servers.js:795` | "该服务器未返回可编辑的配置字段" 晦涩 → "当前没有可编辑的配置" |

### 🟢 Low（5+ 项，可选修）

- **B-3** testServerConnectivity 无全局并发上限 / 无取消（servers.js:1021）
- **B-6** create 用 `func.max(id)+1` 无 UNIQUE 冲突保护（webui_servers.py:120-122）
- **C-1** 所有 500 path message="内部错误"，前端拿不到原因（webui_servers.py 9 处）
- **C-2-E** "返回数据格式错误" 开发者视角（servers.js:461, 826）
- **C-2-K** 加载 placeholder ASCII `...` 改全角 `…`（servers.js:809）
- **C-2-G** ASCII `...` → `…`（servers.js:620, 893）
- **D-2** logger 缺 client_ip / user_agent（与 webui.py login 风格不一致）
- **B-4** renderTable 全量重绘（token 显隐切换触发整表重建）
- **A-8** path 参数无 ge=1 边界
- **A-9** keyword 参数无长度上限

### ❌ Scope-out backlog（不修）

- **A-3** Backend CSRF 防护（涉及 webui.py middleware 跨模块）
- B-3 async DB 改造（项目级议题）
- A-5 上游 error message 白名单（跨 TShock 协同）
- A-7 DELETE reindex 副作用（涉及多模块 server_id 引用）
- D-2 _client_ip 跨模块复用

## 关键修复前后对比

### 🔴 H-1 TShock token 链（A-1 + A-2 + F-A-3）

**修复前**：
```python
# webui_servers.py:41
"token": str(server.token),  # list/create/update 全返回明文
# webui_servers.py:176
server.token = validated.token  # update 强制覆盖
```
```javascript
// servers.js:354-355
tokenText.textContent = tokenVisible ? server.token : formatMaskedToken(server.token);
tokenText.title = tokenVisible ? server.token : "已隐藏";
// servers.js:514
tokenInput.value = server.token;
```

**修复后**：
1. **后端 list 响应去 token**：返回 `"token_set": true/false` 或末 4 位 mask
2. **后端 update token 字段可选**：`null` / 缺省 → 跳过赋值（不再强制回填）
3. **前端表格不展示完整 token**：保留眼睛图标按钮，点击后通过专门的 `GET /webui/api/servers/{id}/reveal-token` 临时取
4. **编辑表单不回填**：placeholder "留空表示不修改"

完整改造涉及前后端协同 + token 持久化策略调整，建议**独立 task** 推进。

### 🔴 H-2 verify 暴露后端 enum（C-2-N）

**修复前**：
```javascript
// servers.js:1007
message ? `${message}${suffix}` : `验证完成：${probeStatus || "未知状态"}`
```
用户看到 `验证完成：Ok` / `验证完成：Skipped` —— 后端 enum 直接暴露

**修复后**：
```javascript
const verbResult =
    probeStatus === "Ok" ? "验证成功" :
    probeStatus === "Skipped" ? "验证已跳过" :
    "验证失败";
const detail = message ? `，${message}${suffix}` : (suffix ? `${suffix}` : "");
const final = `${verbResult}${detail}`;
```

### 🔴 H-3 进行时文案去对象名（C-2-G）

**修复前 → 修复后**：

| 位置 | 修复前 | 修复后 |
|---|---|---|
| `servers.js:659` | `正在删除服务器 #${id}...` | `正在删除…` |
| `servers.js:660` | 同上 | `正在删除…` |
| `servers.js:967` | `正在保存地址和 Token...` | `正在保存…` |
| `servers.js:1024` | `正在测试服务器 #${id} 连通性...` | `正在测试…` |
| `servers.js:986` | `正在验证连通性...` | `正在验证…` |
| `servers.js:620` | `正在保存...` | `正在保存…`（半角 → 全角省略号）|
| `servers.js:893` | `正在保存...` | `正在保存…` |

### 🔴 H-4 空态文案统一（C-2-C）

| 位置 | 修复前 | 修复后 |
|---|---|---|
| `servers_content.html:36` | `暂无服务器` | `暂无服务器配置` |
| `servers.js:310` 当前页空 | `当前页暂无数据。`（带句号） | `当前页暂无数据`（去句号） |
| `servers.js:310` 全空 | `暂无服务器配置。`（带句号） | `暂无服务器配置`（去句号） |

### 🟡 M-1 plugin-config 字段白名单（A-4）

**修复前**：`webui_servers.py:362-371` 客户端任意 key/value 透传

**修复后**：维护字段白名单（与 TShock NextBot 插件支持的 config key 对齐），白名单外的 key 直接 422

### 🟡 M-2 搜索 debounce（B-1）

**修复前**：`servers.js:1063-1066` 每次按键打满 API

**修复后**：参考 commands R1 模式，300ms debounce + AbortController + signal 透传

## 主代理终判

- **0 Critical**
- **5 High**（A-1+A-2 / F-A-3 / C-2-N / C-2-G / C-2-C）
- **9 Medium**
- **10 Low**
- **5 scope-out backlog**

**servers 是 webui 中迄今为止**问题最多的页面**（特别是 token 全链路明文），但绝大部分是**设计缺陷 + 文案违规**，修复路径清晰。

## 推荐修复梯队

| 选项 | 内容 | 复杂度 |
|---|---|---|
| **A** | 仅修 5 High（token 链 + 文案 4 处）| 复杂（token 链涉及前后端协同 + 数据库 token 持久化策略）|
| **B** | A + 9 Medium | 较复杂 |
| **C** | 全修 High + Medium + Low（含排版）| 工作量大 |
| **D** | 按 ID 逐条选（推荐先 token 链独立 task + 文案小修）|
| **E** | 仅修文案部分（H-2/H-3/H-4 + Medium C-2-D/C-2-L/C-2-M），token 链留独立 task | 中等 |
