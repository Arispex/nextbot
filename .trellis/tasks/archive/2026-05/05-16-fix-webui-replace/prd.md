# fix(webui): 商店导入文案 REPLACE 统一为「全量替换」与抽奖一致

## Bug

商店导入与抽奖导入的"全量替换"二次确认输入框文案不一致：
- shop：`输入 REPLACE 以启用` / placeholder `REPLACE` / JS 校验 `=== "REPLACE"` / 错误文案 `请输入 REPLACE 以确认全量替换`
- lottery：label `二次确认` / placeholder `请精确键入「全量替换」以启用导入` / JS 校验 `=== "全量替换"` / 后端也 require `confirm == "全量替换"`

用户体验断层。

## 决议

统一为 lottery 已用的 **「全量替换」**（中文）：
- 视觉与模式描述（"全量替换（先删除现有所有 X 和 X，再按 JSON 重建）"）一致
- lottery 前后端已用此，shop 仅是前端 gate 改 string，零 backend 风险
- 全中文界面下中文确认词更直观

并把 lottery modal 内 label 也微调为统一形态。

## 改动

### 1) shop 前端

**`server/webui/templates/shop_content.html:311-313`**：
```html
<!-- 之前 -->
<span class="form-label">输入 <code>REPLACE</code> 以启用</span>
<input id="shop-import-replace-confirm" class="input" type="text" autocomplete="off" placeholder="REPLACE" />
```
改：
```html
<span class="form-label">输入 <code>全量替换</code> 以启用</span>
<input id="shop-import-replace-confirm" class="input" type="text" autocomplete="off" placeholder="全量替换" />
```

**`server/webui/static/js/shop.js`**：把所有出现的字符串字面量 `"REPLACE"` 改为 `"全量替换"`；注释里的 `REPLACE` 改为 `全量替换`；错误文案 `"请输入 REPLACE 以确认全量替换"` 改为 `"请输入「全量替换」以确认"`。

具体涉及行号（依命中 grep 结果）：801 / 811 / 816 / 831 / 842 / 845 / 848 / 976。

### 2) lottery 模板视觉对齐（可选但建议）

**`server/webui/templates/lottery_content.html:323-325`**：
```html
<!-- 之前 -->
<span class="form-label">二次确认</span>
<input id="lottery-import-confirm-input" class="input" type="text" placeholder="请精确键入「全量替换」以启用导入" autocomplete="off" />
```
改：
```html
<span class="form-label">输入 <code>全量替换</code> 以启用</span>
<input id="lottery-import-confirm-input" class="input" type="text" placeholder="全量替换" autocomplete="off" />
```

lottery JS / 后端无需改动（已用 "全量替换"）。

## Scope

3 个文件：
- `server/webui/templates/shop_content.html`
- `server/webui/static/js/shop.js`
- `server/webui/templates/lottery_content.html`

不动后端、CSS、其它 page。

## Acceptance

- shop / lottery 导入 modal 的 "全量替换" 确认输入框 label、placeholder 字符串一致
- 输入 "全量替换" 才能启用"导入"按钮（两边）
- 输入错误时错误文案统一形态
- 现有 merge / replace_all 切换行为不变
- 后端 lottery `confirm == "全量替换"` 检查不变
- 后端 shop 仍无 confirm 字段（不引入 backend 行为变化）

## Out of Scope

- 给 shop 后端加 confirm 字段（同 lottery 的 defense-in-depth）— 是 backlog 防御性议题，不在本任务
- "新建奖池 / 新建奖品" 等 action 字段去对象名（同 shop M-18 的反向项）— 单独任务
