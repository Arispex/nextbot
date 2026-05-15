# feat(webui): 商店商品切换 kind 加确认 dialog（对齐 lottery）

## 背景

抽奖奖品在编辑时切换 kind（实物 / 金币 / 命令）会弹自写 dialog 提示 "切换类型会清空 XX 配置"（commit `22e6e79`）。商店商品也有 kind 切换（`item` ↔ `command`），但 `handleKindUserChange` 直接清空对端字段，缺少二次确认 → 误操作易丢已填配置。

## 改动

`server/webui/static/js/shop.js`：

1. `state` 加 `editingItemOriginalKind: null`
2. `openItemModal(item)` 内：`state.editingItemOriginalKind = item ? item.kind : null`
3. `closeItemModal()` 内：`state.editingItemOriginalKind = null`
4. `handleKindUserChange` 改 async：
   - 若 `state.editingItemId !== null && state.editingItemOriginalKind && originalKind !== newKind` → revert select 视觉值 → `await window.webuiConfirm("切换类型会清空「<prevLabel>」配置，确定继续？", {title:"切换商品类型", confirmText:"继续", danger:true})` → 取消 return；确认后 apply newKind + 更新 originalKind 防反复弹窗
   - 否则维持原行为（重置对端字段 + applyKindVisibility）
   - `prevLabel`：`originalKind === "item" ? "商品" : "命令"`

## Scope

仅 `server/webui/static/js/shop.js`。

## Acceptance

- 新建商品切 kind：不弹窗，直接清空对端字段（原行为）
- 编辑商品切 kind 与原 kind 相同：不弹窗
- 编辑商品切 kind 与原 kind 不同：弹 confirm dialog；取消 → 还原 select；确认 → 应用新 kind + 清空对端字段
- 弹窗期间 select 不闪烁新值（先 revert 再 await）
- dialog 标题"切换商品类型"、主按钮 danger 红色"继续"

## DO NOT

- 不动 lottery
- 不动 webui.js / api.js / 后端
- 不引入新模块
- 不 commit

## Out of Scope

- 后端 kind 切换的语义不动（PUT shop item 仍按 payload 覆盖）
- 不为新建商品加确认（仅编辑场景需要保护）
