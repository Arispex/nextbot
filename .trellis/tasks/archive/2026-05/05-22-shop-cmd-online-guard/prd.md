# fix: 商店指令商品「全部服务器 + 不要求在线」不再强制报错

## Goal

商店购买指令类商品时，`require_online=False` + `target_server_id=None`（全部服务器）的组合被 `_buy_command` 早期守卫硬拒：
```
❌ 购买失败，命令商品配置错误，必须设置 require_online=True 或指定 target_server_id
```
（来源：`nextbot/plugins/shop.py:695-706`，原 SF-4.3 守卫）

但抽奖 `lottery.py` 同等配置直接放行（看 lottery.py:618-666，仅在 `require_online=True` 时才做在线检查，否则直接对全部目标服务器发指令）。两个 entry 行为不一致。

实际场景：很多指令商品（如 `/spawn_boss <player>`、`/event start`、`/give_world_buff` 等）**不依赖玩家在线状态**，强制 require_online=True 不合理。守卫的原意是防止 `/give` 类指令在玩家不在线时 silent fail，但这本质是"配置正确性"问题，应该让管理员决定，与 lottery 模型对齐。

## Requirements

- 删除 `nextbot/plugins/shop.py:695-706` 的守卫块：
  ```python
  if not require_online and target_server_id is None:
      await bot.send(...)
      return
  ```
- 删除/调整与之相关的 SF-4.3 注释。
- 不动 `require_online=True` 路径的在线检查逻辑（line 740+），保留原"全部目标服务器都需玩家在线"行为。
- 不动 `target_server_id` 单服务器路径行为。

## Acceptance Criteria

- [ ] 商店购买指令商品时，配置 `require_online=False` + `target_server_id=None` 不再报"命令商品配置错误"。
- [ ] 配置 `require_online=False` + `target_server_id=None` → 直接对所有服务器并行 broadcast 指令（与 lottery 同样行为）。
- [ ] 配置 `require_online=True` → 原在线检查 + 仅在线服务器执行 行为不变。
- [ ] 配置 `target_server_id=<某个>` + `require_online=False` → 行为不变（单服务器执行）。
- [ ] 配置 `target_server_id=<某个>` + `require_online=True` → 行为不变（先检查该服务器在线再执行）。
- [ ] 抽奖（lottery）零改动，不引入回归。
- [ ] WebUI 商店管理 / 抽奖管理页面创建 / 编辑指令奖品时不再出现 require_online + target 组合约束（如果前端有相应校验也一并放开）。

## Definition of Done

- 通过 trellis-check。
- 不破坏 give 类商品（type=item）的逻辑。
- 不破坏现有商店购买回执 / 金币扣费 / 上限校验。

## Out of Scope

- 不修改 lottery（已经是放行行为）。
- 不修改其他 type=item（give 类）商品的逻辑。
- 不引入新配置项警告管理员（让管理员自负责配置正确性）。
- 不动 `MAX_SHOP_CMD_EXECUTIONS` 上限。

## Technical Notes

- 守卫位置：`nextbot/plugins/shop.py:685 _buy_command` 函数体顶部
- 对照实现：`nextbot/plugins/lottery.py:618-666` 抽奖指令奖品分发
- 守卫历史原因（SF-4.3）：怕 `/give` 在玩家不在线时 silent fail，用户付 N 倍金币只拿到 1 倍东西。**反驳**：指令商品本身就是任意指令，是否依赖玩家在线由指令决定，与"全部 vs 单服务器"无关；用户付的是"广播 N 次指令"的钱，不是"保证每次都生效"的钱。lottery 早就是这个模型，shop 应该对齐。

## 前端 / WebUI 影响检查

请 implement 阶段 grep 一下前端代码是否也有这条配置约束（如 `require_online` + `target_server_id` 联动校验）：
- `server/webui/static/js/shop.js`
- `server/webui/templates/shop_content.html`

如果有同款前端守卫，一并删除。
