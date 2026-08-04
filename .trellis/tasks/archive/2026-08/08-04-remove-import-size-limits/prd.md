# 解除商店 / 抽奖导入的大小限制

## Goal

Web UI 商店管理 / 抽奖管理页面「导入配置文件」在配置较大时会失败。解除两处限制：**导入端点的请求体字节上限**、**商店导入的数量上限**。其它 API 的全局 256 KiB 上限保持不变。

## What I already know

- **共享字节上限**：`server/routes/__init__.py` `MAX_JSON_BODY_BYTES = 256 * 1024`，在 `read_json_object(request)` 中做两道校验（Content-Length 预检 + 流式逐块累加），超限返回 413 `payload_too_large`「请求体过大」。**30 个调用方共享**该函数。
- **商店导入数量上限**：`server/routes/webui_shop.py:46-47` `_IMPORT_MAX_SHOPS = 200` / `_IMPORT_MAX_ITEMS = 5000`，在导入端点（约 :554-578）超限返回 413「单次导入商店数过多 / 商品总数过多」。
- **抽奖导入**（`webui_lottery.py:586 /webui/api/lottery/import`）**无**数量上限，仅受共享字节上限约束。
- 前端 `shop.js` / `lottery.js` **无**文件字节校验（限制全在后端）。
- 导入端点：`webui_shop.py:517`（商店导入）、`webui_lottery.py:588`（抽奖导入）。

## Requirements

- `read_json_object` 增加**可选参数**（如 `max_bytes: int | None = MAX_JSON_BODY_BYTES`），传 `None` 表示不限制字节；两道校验（Content-Length 预检 + 流式累加）都遵循该参数。
- **仅**商店导入（`webui_shop.py:517`）与抽奖导入（`webui_lottery.py:588`）两个调用点传入「不限制」；**其余 28 个调用方行为完全不变**（保持 256 KiB）。
- 删除商店导入的 `_IMPORT_MAX_SHOPS` / `_IMPORT_MAX_ITEMS` 上限与对应 413 分支（常量若无其它引用一并删除）。
- 其它校验（Content-Type、JSON 解析、结构校验、字段长度校验、权重校验等）**全部保留**。
- 不改前端、不改全局 `MAX_JSON_BODY_BYTES` 常量值。

## Acceptance Criteria

- [ ] 商店 / 抽奖导入可提交超过 256 KiB 的配置文件，不再返回 413「请求体过大」。
- [ ] 商店导入不再因商店数 >200 或商品数 >5000 返回 413。
- [ ] 其余 28 个 `read_json_object` 调用方仍在 >256 KiB 时返回 413（不回归）。
- [ ] Content-Type 校验、JSON 解析失败、结构/字段校验等行为不变。
- [ ] ruff / pyright / 相关测试全绿。

## Decision (ADR-lite)

- **Context**：导入大配置文件被两处限制拦截；字节上限由 30 个端点共享，不能直接删。
- **Decision**：给 `read_json_object` 加可选 `max_bytes`（默认沿用 256 KiB），仅两个导入端点传 `None` 解除；删除商店导入的数量上限。
- **Consequences**：导入端点不再有字节/数量上限，超大 payload 会占用更多内存与更长事务（用户已明确接受）；其它端点防护不变。

## Out of Scope

- 不改前端 `shop.js` / `lottery.js`。
- 不改全局 `MAX_JSON_BODY_BYTES` 值、不改其它端点。
- 不给抽奖导入新增数量上限（本就没有）。
- 不做分批 / 流式导入优化。

## Technical Notes

改动：`server/routes/__init__.py`（`read_json_object` 加 `max_bytes` 参数）、`server/routes/webui_shop.py`（导入端点传 None + 删数量上限常量与分支）、`server/routes/webui_lottery.py`（导入端点传 None）、`tests/`（如有覆盖）。
