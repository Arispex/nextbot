# fix(rob): 抢劫图片加 QQ 头像 + 中央流向图标增强

## 现状

- 当前 `server/templates/rob.html` 的 `.rob-display` 三段（robber-card / 中间小图标 / victim-card）只显示文字（名字 + QQ）
- 中间小箭头不够明显，看不出钱在哪两方间流向

## 改动

### 1) 加 QQ 头像

`rob.html` 内 robber-card / victim-card 上方加 QQ 头像 `<img>`：

```html
<img class="player-avatar" src="" id="rob-robber-avatar" alt="" />
```

JS 端填充：
```js
const avatar = (qq) => `https://q1.qlogo.cn/g?b=qq&nk=${encodeURIComponent(qq)}&s=100`;
document.getElementById("rob-robber-avatar").src = avatar(data.robber_qq);
document.getElementById("rob-victim-avatar").src = avatar(data.victim_qq);
```

注意：
- 用 https（与 audit S1 修复对齐，7 个模板已统一）
- `encodeURIComponent` 防 qq 字符注入（虽然 build_payload 已 strip）
- CSS：`.player-avatar { width: 64px; height: 64px; border-radius: 50%; object-fit: cover; }`
- alt="" 装饰性图片

### 2) 中央流向图标增强

把当前小箭头替换为更清晰的"流向 + 金额"图标：

#### 5 状态各自的流向表达

| result_kind | 流向 | 视觉 |
|---|---|---|
| `crit` | victim → robber（大成功）| `🔥 ←` 大字 + coral + amber 双色 |
| `success` | victim → robber | `←` 大字 + coral |
| `counter` | robber → victim（反被抢） | `→` 大字 + accent-amber |
| `police` | 钱凭空消失（地牢守卫罚款） | `🚨 ↓` 下沉图标 + muted-strong |
| `fail` | 钱凭空消失 | `❌` + muted |

`.rob-flow` 容器（替代当前小箭头），按 result_kind 切 class 显示不同：

```html
<div class="rob-flow" id="rob-flow">
  <div class="rob-flow-icon" id="rob-flow-icon"></div>
  <div class="rob-flow-amount type-display-md mono" id="rob-flow-amount"></div>
  <div class="rob-flow-label type-caption-uppercase" id="rob-flow-label"></div>
</div>
```

JS 切换：
- `rob-flow-icon` 大字 emoji（按上表）+ 配色
- `rob-flow-amount` 显示 `💰 N`（实际入账或损失金额）
- `rob-flow-label` 显示流向描述（"抢走 / 反抢 / 罚款 / 凭空消失"）

#### CSS 细节
- `.rob-flow` 居中、flex-column、gap-sm
- `.rob-flow-icon` font-size 48px serif；按 result_kind class 切色
- `.rob-flow-amount` mono 24px ink
- `.rob-flow-label` 12px muted uppercase letter-spacing 1.5px
- 状态 class：`.rob-flow-crit`（coral）/ `.rob-flow-success`（coral）/ `.rob-flow-counter`（amber）/ `.rob-flow-police`（muted-strong）/ `.rob-flow-fail`（muted）

### 3) robber-card / victim-card 视觉强化"流向源/目标"

- 钱的"流出方"加 `.is-source` class（半透明 + amber dot 角标）
- "流入方"加 `.is-target` class（cream-strong 高亮 + coral 边）
- 状态映射：
  - crit / success: source=victim, target=robber
  - counter: source=robber, target=victim
  - police / fail: 双方都不加（钱凭空消失）

## Scope

仅 `server/templates/rob.html`（HTML + CSS + JS 一体改）。

## Acceptance

- 5 种 result_kind 都能正确显示头像 + 中央流向图标 + source/target 高亮
- QQ 头像加载成功（playwright 网络可达）
- 头像加载失败时 fallback 显示空头像区（不破版）
- HTML 平衡

## DO NOT

- 不改 rob_page.py / rob.py / web_server.py / render.py
- 不改 payload schema（schema 已含 robber_qq / victim_qq）
- 不引外部 CDN（QQ 头像是已批准的 q1.qlogo.cn）
- 不动其它模板
- 不 commit
