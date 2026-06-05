"""菜单分类排序测试。

依赖轻量：不连网、不出图。可在 pytest 下运行
（``uv run pytest tests/test_menu_category_order.py``）或作为脚本直接运行
（``uv run python tests/test_menu_category_order.py``）。

覆盖：
  - _group_by_category：CATEGORY_ORDER 内分类按列表序、extras 按字母序追加。
  - 「查询系统」紧随「用户系统」（序号 2）。
"""
from __future__ import annotations

import sys
from pathlib import Path

# allow `python tests/test_menu_category_order.py` from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nonebot


# menu 模块顶层调用 on_command(...)，需要 NoneBot 已初始化。
def _ensure_nonebot() -> None:
    try:
        nonebot.get_driver()
    except ValueError:
        nonebot.init()


_ensure_nonebot()

from nextbot.plugins import menu


def _cmd(category: str, key: str) -> dict[str, str | bool]:
    return {"is_registered": True, "category": category, "command_key": key}


def test_query_system_follows_user_system() -> None:
    items = [
        _cmd("用户系统", "user.a"),
        _cmd("查询系统", "query.a"),
        _cmd("经济系统", "econ.a"),
    ]
    cat_names, _ = menu._group_by_category(items)
    assert cat_names[:3] == ["用户系统", "查询系统", "经济系统"], cat_names
    assert cat_names.index("查询系统") == cat_names.index("用户系统") + 1, cat_names


def test_query_system_in_category_order_before_economy() -> None:
    order = menu.CATEGORY_ORDER
    assert "查询系统" in order
    assert order.index("查询系统") == order.index("用户系统") + 1, order
    assert order.index("查询系统") < order.index("经济系统"), order


def test_unused_ordered_categories_are_filtered_out() -> None:
    # CATEGORY_ORDER 含「玩家查询」「服务器管理」但无命令使用 → 不出现在结果里。
    items = [
        _cmd("用户系统", "user.a"),
        _cmd("查询系统", "query.a"),
    ]
    cat_names, _ = menu._group_by_category(items)
    assert cat_names == ["用户系统", "查询系统"], cat_names


def test_extras_appended_after_ordered_in_alpha_order() -> None:
    # 不在 CATEGORY_ORDER 的分类按字母序追加在末尾，未分类最后。
    items = [
        _cmd("用户系统", "user.a"),
        _cmd("查询系统", "query.a"),
        _cmd("Zeta", "z.a"),
        _cmd("Alpha", "a.a"),
        _cmd("", "uncat.a"),
    ]
    cat_names, _ = menu._group_by_category(items)
    assert cat_names == ["用户系统", "查询系统", "Alpha", "Zeta", "未分类"], cat_names


def _run() -> int:
    tests = [
        test_query_system_follows_user_system,
        test_query_system_in_category_order_before_economy,
        test_unused_ordered_categories_are_filtered_out,
        test_extras_appended_after_ordered_in_alpha_order,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as exc:  # noqa: PERF203 - tiny test loop
            failed += 1
            print(f"FAIL {t.__name__}: {exc}")
        else:
            print(f"PASS {t.__name__}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
