# fix(webui): 商店 / 抽奖页面 CRUD 成功后缺失 toast 反馈

## Bug

商店页面和抽奖页面的"新建 / 保存 / 删除"操作成功后只关 modal + reload 列表，**没有显示成功 toast**。导出 / 导入有显示（"导出成功" / "导入成功"），但其它 CRUD 都没有，用户体验不一致。

## 范围 — 12 处缺失

### shop.js
1. `saveShop` 新建路径（~line 415-420 `action: "新建"`）
2. `saveShop` 编辑路径（~line 422-427 `action: "保存"`）
3. `confirmDeleteShop`（~line 472 `action: "删除"`）
4. `saveShopItem` 新建路径（~line 620 `action: "新建"`）
5. `saveShopItem` 编辑路径（~line 629 `action: "保存"`）
6. `confirmDeleteShopItem`（~line 679 `action: "删除"`）

### lottery.js
1. `savePool` 新建（~line 489 `action: "新建奖池"`）
2. `savePool` 编辑（~line 496 `action: "保存奖池"`）
3. `confirmDeletePool`（~line 525 `action: "删除奖池"`）
4. `savePrize` 新建（~line 733 `action: "新建奖品"`）
5. `savePrize` 编辑（~line 740 `action: "保存奖品"`）
6. `confirmDeletePrize`（~line 774 `action: "删除奖品"`）

## 修复

每处成功路径（catch 之前、`finally` 之前），在已有的 `closeXxxModal()` / `await loadXxx()` 之后追加：
```js
showAlert(els.alert, "<动作>成功", "success");
```

`<动作>` 严格按 CLAUDE.md 规范（不含对象名）：
- 新建 → "新建成功"
- 保存 → "保存成功"
- 删除 → "删除成功"

注意 lottery.js 当前 `action` 字段还含"奖池 / 奖品"对象名（与 shop M-18 已修不同）— 本任务**不**改 action 字段（那是另一个文案统一议题），仅添加成功 toast 用的是简洁文案。

## Scope

仅 2 个文件：
- `server/webui/static/js/shop.js`
- `server/webui/static/js/lottery.js`

不动模板 / CSS / 后端 / 其它 page JS。

## Acceptance

- 商店：新建 / 保存 / 删除商店 + 商品 共 6 类操作成功后 `els.alert` 显示 "新建成功" / "保存成功" / "删除成功"
- 抽奖：奖池 + 奖品 6 类操作同上
- 文案严格"动作 + 结果"，不带对象名
- 失败路径行为不变

## Out of Scope

- lottery action 文案统一（同 shop M-18）— 单独任务
- 其它页面（users / groups / warehouse 已经有 success toast 或不在本反馈范围）
- toast queue / auto-dismiss timer 重构
