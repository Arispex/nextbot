# 修复 SQLAlchemy rowcount 类型告警

## Goal

最近 4 次审计修复（commit `0206834` / `fe11241` / `6ca05b8` 等）大量使用 `session.execute(update(...)).rowcount` 模式，basedpyright 报错：

```
无法访问 "Result[Any]" 类的 "rowcount" 属性
属性 "rowcount" 未知 basedpyrightreportAttributeAccessIssue
```

原因：SQLAlchemy 2.0 的 `session.execute()` 返回 `Result[Any]`，但 `rowcount` 实际只在 `CursorResult` 子类上。**运行时正确**（execute UPDATE/DELETE 实际返回 CursorResult），仅类型不准确。

## 影响范围

```
nextbot/plugins/economy.py        3
nextbot/plugins/guess_number.py   1
nextbot/plugins/dice.py           1
nextbot/plugins/rob.py            7
nextbot/plugins/rob_protection.py 1
nextbot/plugins/red_packet.py     3
合计                              16
```

## 方案选择

### 方案 A：抽 helper（推荐）

在 `nextbot/db.py` 或新 `nextbot/db_helpers.py` 加：

```python
from typing import Any
from sqlalchemy import Executable
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session


def execute_rowcount(session: Session, stmt: Executable) -> int:
    """执行 INSERT/UPDATE/DELETE 并返回 rowcount。
    
    封装类型转换：session.execute() 在类型 stub 中返回 Result[Any]，
    但实际 INSERT/UPDATE/DELETE 返回 CursorResult，这里 cast 一下让
    pyright 接受 .rowcount 属性。
    """
    result = session.execute(stmt)
    return int(getattr(result, "rowcount", 0))
```

调用点改为：
```python
# 改前：
rowcount = session.execute(update(User).where(...).values(...)).rowcount

# 改后：
rowcount = execute_rowcount(session, update(User).where(...).values(...))
```

### 方案 B：每处加 `# type: ignore[attr-defined]`

**缺点**：16 处分散注释，未来维护困难。**不采用**。

### 方案 C：每处用 `cast(CursorResult, ...)`

**缺点**：每处 import + cast，比方案 A 啰嗦。**不采用**。

## 决定：方案 A —— 抽 helper

## 改动清单

1. **`nextbot/db.py`**：新增 `execute_rowcount(session, stmt) -> int` 函数（或者新建 `nextbot/db_helpers.py`，取决于实施代理判断哪个更合适）
2. **6 个 plugin 文件**：所有 `session.execute(...).rowcount` 调用替换为 `execute_rowcount(session, ...)`
3. 移除可能 import 的 `cast` / `CursorResult` 等冗余 import（如果是从 commit history 加进去的）

## 验收标准

1. **无破坏性**：行为完全一致（rowcount 数值不变）
2. **basedpyright / pyright 通过**：所有 `.rowcount` 类型告警消失
3. **不引入新告警**

## Acceptance Criteria

- [ ] `nextbot/db.py` 或 `nextbot/db_helpers.py` 有 `execute_rowcount` helper
- [ ] 6 个 plugin 文件全部接入
- [ ] `grep -rn "\.rowcount" nextbot/plugins/ --include="*.py" | wc -l` = 0（所有调用点都通过 helper 走）
- [ ] `grep -rn "execute_rowcount" nextbot --include="*.py" | wc -l` ≥ 17（helper 定义 + 16 调用）
- [ ] `python3 -c "import ast; ..."` 全部通过
- [ ] pyright 0 个 `reportAttributeAccessIssue`（与 rowcount 相关的）

## Non-goals

- 不改 `_claim_slot_atomic` 的 rowcount（如有）
- 不重构其他 SQLAlchemy 调用风格

## Definition of Done

- 单一 commit
- 用户测试通过后再提交
