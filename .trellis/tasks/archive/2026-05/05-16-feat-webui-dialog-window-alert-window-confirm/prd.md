# feat(webui): 用自写 dialog 替换 window.alert / window.confirm

## 背景

WebUI 还残留 2 处原生浏览器 dialog：
- `server/webui/static/js/webui.js:227` `window.alert(...)` — 退出失败兜底反馈
- `server/webui/static/js/lottery.js:624` `window.confirm(...)` — 奖品 kind 切换二次确认

原生 alert / confirm 是 **同步阻塞**、样式不可控、移动端弹出形态不一致，与本项目其它 modal 风格断层。

## 方案

在 shell 层提供两个 Promise-based 共享 dialog API：
- `webuiAlert(message, opts?) → Promise<void>`（单"知道了"按钮）
- `webuiConfirm(message, opts?) → Promise<boolean>`（取消 / 确认，true=确认）

`opts`: `{ title?: string, confirmText?: string, cancelText?: string, danger?: boolean }`

共享一个 HTML modal `<div id="webui-dialog">`，根据调用区分按钮显示。视觉与 `#logout-confirm-modal` 一致，复用 app-shell.css 已下沉的 `.modal` / `.confirm-modal-card` 等样式。

## 文件

- `server/webui/templates/app_shell_base.html` — 末尾追加 `#webui-dialog` modal
- `server/webui/static/js/webui.js` — 实现 `webuiAlert` / `webuiConfirm`，暴露到 `window`；同文件内 logout 失败兜底调 `webuiAlert` 替代 `window.alert`
- `server/webui/static/js/lottery.js` — kind 切换调 `window.webuiConfirm` 替代 `window.confirm`

## HTML 结构

```html
<div id="webui-dialog" class="modal hidden" role="dialog" aria-modal="true" aria-labelledby="webui-dialog-title" aria-describedby="webui-dialog-body">
  <div class="modal-mask" data-webui-dialog-close="1"></div>
  <div class="modal-card confirm-modal-card">
    <div class="modal-head">
      <h3 id="webui-dialog-title" class="modal-title">提示</h3>
      <button id="webui-dialog-close-btn" type="button" class="btn btn-icon modal-close-btn" aria-label="关闭">✕</button>
    </div>
    <div class="modal-body confirm-modal-body">
      <p id="webui-dialog-body" class="confirm-modal-text"></p>
    </div>
    <div class="modal-foot">
      <button id="webui-dialog-cancel-btn" type="button" class="btn">取消</button>
      <button id="webui-dialog-confirm-btn" type="button" class="btn btn-primary">确定</button>
    </div>
  </div>
</div>
```

cancel 按钮在 alert 模式下 `hidden`；confirm 按钮文本 / variant 由 opts 切换。

## JS API 行为

```js
window.webuiAlert(message, { title, buttonText } = {}) → Promise<void>
window.webuiConfirm(message, { title, confirmText, cancelText, danger } = {}) → Promise<boolean>
```

- 打开：移除 `hidden`、记录 `previousFocus`、`document.body.style.overflow = "hidden"`、focus 主按钮
- 关闭：加回 `hidden`、还原 body.style.overflow、focus 回 previousFocus
- ESC：关闭 + alert 模式 resolve()；confirm 模式 reject 等价于"取消"，统一 resolve(false)
- Mask / × / 取消：alert 模式 resolve()；confirm 模式 resolve(false)
- 确认按钮：alert 模式 resolve()；confirm 模式 resolve(true)
- 同时只能开一个；并发调用排队（用 `_dialogQueue: Promise<void>` 链式 await）
- `danger=true` 时确认按钮加 `.btn-danger` 类

注：与现有 `#logout-confirm-modal` 共存（不重构 logout 流程），仅替换 window.alert / window.confirm 两处。

## lottery kind 切换的同步语义保留

原 `window.confirm` 是同步，select 仍保持旧值直到用户确认。改 async 后需要先把 select 视觉值 revert 到 original，await 确认后才应用新值，避免 visual flash：

```js
async function handleKindChange(ev) {
  const newKind = els.prizeFieldKind.value;
  if (state.editingPrizeId !== null && state.editingPrizeOriginalKind &&
      state.editingPrizeOriginalKind !== newKind) {
    const prevLabel = kindLabel(state.editingPrizeOriginalKind);
    // 先 revert 视觉值，避免 dialog 弹出期间显示新值
    els.prizeFieldKind.value = state.editingPrizeOriginalKind;
    const ok = await window.webuiConfirm(
      `切换类型会清空「${prevLabel}」配置，确定继续？`,
      { title: "切换奖品类型", confirmText: "继续", danger: true }
    );
    if (!ok) return;
    els.prizeFieldKind.value = newKind;
    state.editingPrizeOriginalKind = newKind;
  }
  resetFieldsForOtherKinds(els.prizeFieldKind.value);
  applyKindVisibility();
}
```

## Acceptance

- 退出失败时弹自写 dialog（标题"退出失败"、内容原始 reason、单按钮"知道了"）— 不再是浏览器原生 alert
- 抽奖编辑奖品切换 kind 弹自写 confirm dialog；取消保留原 kind，确认才清空字段
- ESC / mask 关闭 = 取消语义
- dialog 在所有页面视觉一致
- 屏幕阅读器读出 title + body
- `grep window.alert\\|window.confirm server/webui/static/js/` 在 PRD 描述的两个 callsite 之外应零命中

## DO NOT

- 不动 `#logout-confirm-modal`（保留独立的 logout 确认体验）
- 不动其它 page 的 modal-stack
- 不动后端
- 不引入新 ESM 模块
- 不 commit

## Out of Scope

- 不替换 page-level `showAlert(...)` toast（那是 in-page 状态条，不是浏览器 alert）
- 不替换 `<input type="file">` / `confirm input` 等其它 dialog 替代品
