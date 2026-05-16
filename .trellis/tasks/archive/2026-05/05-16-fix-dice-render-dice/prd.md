# fix(dice): 注册 /render/dice 路由

## Bug

掷骰子命令成功后发的图片显示 `{"detail": "Not Found"}`。原因：上次任务 (`feat(dice)` commit 漏改) 只新建了 `server/pages/dice_page.py` 和 `server/templates/dice.html`，把 helper 加到 `web_server.py`，**但没在 `server/routes/render.py` 注册路由**。每个 render page 在 render.py 必须有 `@router.get("/render/<name>/{token}")` 显式入口。

## 改动

`server/routes/render.py`：

1. 在文件顶部 import 列表（line 12）按字母序加 `dice_page`
2. 仿照其它 render 端点新增一段（建议放在 ban_list 之后 / red_packet 之前，保持文件按字母大致顺序）：
   ```python
   @router.get("/render/dice/{token}")
   async def render_dice(request: Request, token: str) -> Response:
       return await _render_page(request, token, page_type="dice", renderer=dice_page.render)
   ```

## Scope

仅 `server/routes/render.py`。

## Acceptance

- 命令 "掷骰子 大 100" 返回的图片 URL 不再 404
- 浏览器访问 `/render/dice/<token>` 返回 dice HTML

## DO NOT

- 不动其它文件
- 不 commit
