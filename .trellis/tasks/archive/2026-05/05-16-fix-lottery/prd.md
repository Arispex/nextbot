# fix(lottery): 抽奖标题去「结果」+ 抽奖发图加 @ 调用者

## 改动

### 1) `server/templates/lottery_result.html`

- line 310 `<h1 class="header-title type-display-lg">抽奖结果</h1>` → `抽奖`

`<title>` 标签（line 6 浏览器 tab，截图不可见）保留不动。

### 2) `nextbot/plugins/lottery.py` `handle_lottery_draw`

调用 `render_and_send_screenshot`（约 line 959）追加 `at_user_id=user_id`。

注：line 311 + 451 是其它 handler（奖池列表 / 查看奖池）的调用，本任务**不**触碰；仅改"抽奖"成功路径（line 959）。

## Scope

- `server/templates/lottery_result.html`
- `nextbot/plugins/lottery.py`

## Acceptance

- 截图 hero 标题显示「抽奖」（不再带"结果"）
- "抽奖" 命令 V11 消息形态 `@玩家 [图片]`
- 奖池列表 / 查看奖池 行为不变
- `python3 -m py_compile nextbot/plugins/lottery.py` 通过

## DO NOT

- 不改 lottery_result_page.py / 后端
- 不改 lottery_list / lottery_view 任何相关
- 不动其它 plugin
- 不 commit
