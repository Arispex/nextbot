# Replace native confirm() with dialog + unify delete copy

## Goal

去掉 webui 残留的浏览器原生 `confirm()` 调用（视觉与 warm-canvas 风格不一致），统一使用 modal dialog；同时把删除 / 封禁 confirm 文案中被操作对象的引号统一为中文 book quotes 「」。

## What I already know

**1) Native confirm() 残留**：仅 1 处
- `commands.js:852` — `confirm("确定要重启吗？")` 用于"重启"按钮

**2) 删除文案不统一**：
- shop / lottery / warehouse templates 使用 `「<span class="confirm-modal-highlight">name</span>」` ✓
- groups.js / servers.js / users.js 动态文案使用中文双引号 `"name"` ✗
  - groups.js:370 `确定要删除身份组 "name" 吗？此操作无法撤销。`
  - servers.js:541 `确定要删除服务器 "name" 吗？此操作无法撤销。`
  - users.js:585 `确定要删除用户 "name" 吗？此操作无法撤销。`
  - users.js:539 `确定要封禁用户 "name" 吗？`

## Requirements

### Phase A: confirm() → dialog
- 在 `commands_content.html` 新增 `restart-confirm-modal`（沿用 confirm-modal-card 模板：标题、说明、取消/确认按钮）
- `commands.js` 改造：`#restart-btn` click 不再调用 `confirm()`，而是 show restart-confirm-modal；modal 的"确认"按钮调用原来的 restart 逻辑
- 移除 `if (!confirm("确定要重启吗？")) return;` 行

### Phase B: 文案统一
- groups.js / servers.js / users.js 中的删除 / 封禁文案：
  - 引号 `"..."` → `「...」`
  - 句式与 shop/lottery 模板保持一致（"确定删除XX「name」吗？此操作不可恢复。"）
  - "确定要删除" → "确定删除"（瘦身）
  - 封禁 confirm 也加结尾「此操作可在用户列表中解封」之类的确认提示？— 否，按钮文案已是"封禁"，不需赘述
- 检查并保持 shop / lottery / warehouse template 已经正确的文案（不动）

## Acceptance Criteria

- [ ] 全 webui `grep -nE "alert\(|confirm\(|prompt\("` 零残留（除新加入的 modal-based 实现）
- [ ] 重启按钮点击后弹出 warm-canvas 风格的 confirm modal（不再是浏览器原生弹窗）
- [ ] 所有删除 / 封禁 confirm 中被操作对象都用 `「name」` 包裹

## Out of Scope

- 修改其它 modal 行为
- 后端 API 调整
