# 排行榜命令崩溃修复（theme 残留）

## Goal

所有排行榜命令（金币 / 连续签到 / 抢劫收益 / 抢劫损失 / etc.）调用都报：
```
TypeError: _render_and_send() missing 1 required keyword-only argument: 'theme'
```
这是 RENDER_THEME 多主题适配清理时漏掉的内部 helper：`_render_and_send(theme: str, ...)` 没默认值，但 16 个调用点都已不再传 theme。

## Requirements

修改 `nextbot/plugins/leaderboard.py`：

1. 删除 `_render_and_send(...)` 函数签名中的 `theme: str,` 参数（line 94）
2. 删除函数体中传给 `create_leaderboard_page(...)` 的 `theme=theme,` 实参（line 103）

`create_leaderboard_page` 的形参 `theme: str = "dark"` 有默认值，所以不传不会报错（默认值无功能影响，渲染层早已统一为单主题）。

## Acceptance Criteria

- [ ] 任意排行榜命令（如 `金币排行榜`）成功生成截图，无 TypeError
- [ ] 截图视觉无变化（只是删除了死参数）
- [ ] 16 个调用点零改动

## Definition of Done

- bug 解除
- 不引入新依赖、不动其他文件

## Technical Approach

两处删除即可：

```python
# Before (line 82-95):
async def _render_and_send(
    bot: Bot,
    event: Event,
    *,
    title: str,
    value_label: str,
    page: int,
    limit: int,
    entries: list[dict],
    total_pages: int,
    file_prefix: str,
    self_entry: dict | None = None,
    theme: str,        # ← 删除
) -> None:
    page_url = create_leaderboard_page(
        title=title,
        value_label=value_label,
        page=page,
        total_pages=total_pages,
        entries=entries,
        self_entry=self_entry,
        theme=theme,   # ← 删除
    )
```

## Out of Scope

- 不清理 `server/web_server.py` / `server/pages/*.py` 里的其他 `theme= "light"/"dark"` 死代码（约 15+ 处，单独任务处理；它们因为有默认值不会崩，仅是残留）
- 不重构 `create_leaderboard_page` 的 `theme` 形参（同样是死参数但向后兼容）

## Technical Notes

- 受影响：`金币排行榜` `连续签到排行榜` `抢劫收益排行榜` `抢劫损失排行榜` `抢劫成功率排行榜` 等共 16 个排行榜命令
- 完整 stale theme 列表已在调研中识别（见调用点扫描）；本任务仅修崩溃
