# 抽奖概率精度提升至 0.01%

## Goal

WebUI 抽奖管理的奖品概率输入步进当前是 `0.1`，导致管理员无法设置 < 0.1% 的小概率奖品（典型场景：稀有 0.05% 神秘奖品）。后端 `Float` 列 + `float(raw)` 解析早已支持任意精度（0-100 范围内），瓶颈完全在前端 `step="0.1"` + 显示用 `toFixed(1)`。

## Requirements

### A. 输入精度（admin）

- `server/webui/templates/lottery_content.html:168`：奖品概率 input 的 `step="0.1"` → `step="0.01"`
- 校验文案 `<span class="form-label">概率（0-100，留空则平分剩余）</span>` 不变（已暗含支持小数）

### B. 显示精度（admin 列表）

- `server/webui/static/js/lottery.js:260`：当前 `(probabilityPct || 0).toFixed(1) + "%"` 改为智能格式：
  - 值能被一位小数表示就用一位（`5.0` / `5.5`），保持现有视觉
  - 否则用两位（`0.01` / `5.55`）
- 抽出公共 helper（如 `formatProbabilityPct(n)`），方便复用

### C. 显示精度（公开「查看奖池」截图）

- `server/templates/lottery_view.html:526`：每个奖品 `prob.textContent = (Number(p.probability) || 0).toFixed(1)` 改为同款智能格式
- `server/templates/lottery_view.html:364`：未中奖率 `missEl.textContent = ` `未中奖率 ${missPct.toFixed(1)}%` 同款智能格式

模板内是独立 JS（不共享 helper），需要在该文件 `<script>` 内复制一份格式化函数（保持单文件自包含的渲染管线惯例）。

## Acceptance Criteria

- [ ] WebUI 抽奖管理 → 编辑奖品 → 概率输入框，可输入 / 微调 0.01（step 按钮 +0.01）
- [ ] 编辑后保存 → 列表里的概率徽章正确显示，例如 0.05% → "0.05%"
- [ ] 没改值的现有奖品（5%, 10%, 50% 等整数 / 一位小数）显示为 `5.0%` / `10.0%` / `50.0%`，**视觉无回归**
- [ ] 「查看奖池」截图里，0.01% 奖品显示为 `0.01%` 不再被截到 `0.0%`
- [ ] 后端 API 接受 0.01 提交，DB 存储无精度损失（`SELECT weight FROM lottery_prize` 查到原值）

## Definition of Done

- 三处 frontend 改动全部到位、视觉无回归
- 不动后端、不改 DB 模型、不改任何 Python
- 不重命名 / 移动任何文件

## Technical Approach

### 智能格式化函数

```javascript
function formatProbabilityPct(n) {
  const v = Number(n) || 0;
  const r2 = Math.round(v * 100) / 100;  // 圆整到 2 位
  const r1 = Math.round(v * 10) / 10;    // 圆整到 1 位
  return r2 === r1 ? r1.toFixed(1) : r2.toFixed(2);
}
```

行为：
- 5 → `"5.0"`
- 5.5 → `"5.5"`
- 0.5 → `"0.5"`
- 5.55 → `"5.55"`
- 0.01 → `"0.01"`
- 5.05 → `"5.05"`

### 文件级改动

| 文件 | 行 | 改动 |
|------|----|----|
| `server/webui/templates/lottery_content.html` | 168 | `step="0.1"` → `step="0.01"` |
| `server/webui/static/js/lottery.js` | 顶部某处 | 新增 `formatProbabilityPct` helper |
| `server/webui/static/js/lottery.js` | 260 | `(probabilityPct \|\| 0).toFixed(1) + "%"` → `formatProbabilityPct(probabilityPct) + "%"` |
| `server/templates/lottery_view.html` | `<script>` 块顶部 | 复制一份 `formatProbabilityPct`（模板渲染管线习惯单文件自包含） |
| `server/templates/lottery_view.html` | 526 | `(Number(p.probability) \|\| 0).toFixed(1)` → `formatProbabilityPct(p.probability)` |
| `server/templates/lottery_view.html` | 364 | `${missPct.toFixed(1)}%` → `${formatProbabilityPct(missPct)}%` |

### 后端确认（无需改动）

- `nextbot/db.py:312` `weight: Mapped[Optional[float]] = mapped_column(Float, nullable=True)` — IEEE 754 double，0.01 无精度损失
- `server/routes/webui_lottery.py:159` `weight = float(raw_weight)` — 解析任意 float，无 round
- 校验 line 162: `if weight is not None and (weight < 0.0 or weight > 100.0)` — 只看区间
- `nextbot/plugins/lottery.py` 抽奖核心逻辑全部走 `float`，无精度截断

## Decision (ADR-lite)

**Context**：用 `toFixed(2)` 简单粗暴 vs 智能格式化

**Decision**：智能格式化（保持整数 / 一位小数原样，仅在更精细时显示两位）

**Consequences**：
- 优：现有 5.0% / 10.0% 视觉零回归，老管理员不会突然觉得 UI 变了；新功能用户输入 0.01 也能完整显示
- 劣：多一个 helper 函数（lottery.js 共享 + lottery_view.html 复制一份），但函数本身是 4 行
- 替代：`toFixed(2)` 一刀切，所有概率都变两位（`5.00%`），实现简单但视觉变更大

## Out of Scope

- 不改后端 / DB schema（已支持）
- 不改 lottery_result 截图的稀有度阈值逻辑（≤1% / ≤5% / ≤15% / ≤40% 仍按当前阈值，不引入 0.01 段位）
- 不改 `查看奖池` 命令的命中概率算法
- 不引入小数掩码 / 数字格式化第三方库

## Technical Notes

- 后端验证已就绪：`server/routes/webui_lottery.py:154-163`
- DB 列：`nextbot/db.py:312` SQLAlchemy `Float` (SQLite `REAL`)
- 抽奖核心：`nextbot/plugins/lottery.py:75-101`（resolve_probabilities）
- 渲染模板单文件自包含原则：`server/templates/lottery_view.html` 里的 `<script>` 块不依赖外部 JS（render 后跟 PNG 截图，没有打包）
