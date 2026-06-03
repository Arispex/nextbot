# 从原型移植到生产 —— 实现地图

渲染管线已在 `temp/xnb_probe/` 完整跑通并逐项对照游戏内验证。本文件给出原型清单 + 目标结构 + 移植要点，配合 `terraria_render_spec.md`（图层/帧/绘制顺序）与 `dye_shader_spec.md`（染料公式）一起看。

## 原型文件清单（`temp/xnb_probe/`，参考实现，勿直接引用其路径到生产）
| 文件 | 作用 | 移植去向 |
|---|---|---|
| `lzx_xnb.py` | 纯 Python LZX-XNB 解码（块长 24 位是关键坑）+ XNB→raw | **仅提取脚本用**，不进运行时 |
| `xnb_to_png.py` | XNB Texture2D 解析 → RGBA → PNG（含 unpremultiply、PNG 写出） | 提取脚本用 |
| `compose_player.py` | 合成器：图层解析、composite 全帧 cell（torso 0/18、前臂2、后臂20、前肩9/27、后肩10/28）、变体 fallback、头发遮挡（FULLHAIR/HATHAIR/BACKHAIR 集 + hair_mode）、装备/装饰逐部位、装备绘制顺序 | → `compositor.py` 核心 |
| `armor_colored_impl.py` | `apply_armor_colored(rgba, uColor, uSat)` 精确 ArmorColored shader（含 D3DX preshader c0/c1/c2） | → `dye.py` |
| `fx_parse.py` / `ps_disasm.py` / `pres_decode.py` | shader 逆向工具 | 不进生产（已得公式） |
| `render_cxk.py` | 渲染调用示例（base/armor/vanity/dye） | 参考用法 |

## 目标模块结构（`nextbot/terraria_render/`，不依赖 NoneBot）
```
nextbot/terraria_render/
  __init__.py          # 暴露 render_character(...)
  compositor.py        # 图层合成 + 头发遮挡 + 装备/装饰（移植 compose_player.py）
  dye.py               # ArmorColored 系 shader（移植 armor_colored_impl.py）
  data/                # 查表 JSON（入库，由反编译源烘焙）
    equip_slots.json   # netID -> {"head"|"body"|"legs": slot}（全量，从 Item.cs SetDefaults）
    dyes.json          # dye netID -> {"pass","color":[r,g,b],"sat",...}（DyeInitializer）
    hair_sets.json     # fullHair/hatHair/backhair_only headSlot 集（Player.GetHairSettings）
    variants.json      # MALE_VARIANTS、fallback 链
  assets/              # 预提取 PNG（入库，方案 A）
    Player_*.png  Player_Hair_*.png  Player_HairAlt_*.png
    Armor_Head_*.png  ArmorBody_*.png  Armor_Legs_*.png
```

## 查表数据来源（`temp/decomp/full/`）
- **equip_slots.json**：`Terraria/Item.cs` SetDefaults，每个 `case N:` 的 `headSlot=/bodySlot=/legSlot=`。需写生成脚本正则解析全部 case（数百条）。
- **dyes.json**：`Terraria.Initializers/DyeInitializer.cs`。基础色：`LoadBasicColorDye(base,r,g,b,sat)` → base="ArmorColored"、base+12="ArmorColoredAndBlack"、base+31=bright(color*0.5+0.5)、base+44="ArmorColoredAndSilverTrim"。exotic（gradient/rainbow/reflective/living/martian/invert…）记录其 pass 名，运行时无法精确还原的回退不染色。
- **hair_sets.json**：`Terraria/Player.cs` `GetHairSettings` switch（fullHair / hatHair / backonly{0,259} / 其余=none）。已在 compose_player.py 内联，抽成 JSON。
- 女性身体甲走 `ArmorBodyComposite`(=`Images/Armor/Armor_{slot}`) + +2 行偏移（**不是** FemaleBody 贴图）。

## 提取脚本（一次性，非运行时）
读 Terraria 安装 `Content/Images/`（含 `Armor/` 子目录），用 `lzx_xnb`+`xnb_to_png` 全量转 PNG 到 `assets/`：
Player_0..11 各层、Player_Hair_1..N、Player_HairAlt_1..N、Armor_Head_*、Armor/Armor_*（→ 命名 ArmorBody_*）、Armor_Legs_*。约 1200 张、数 MB。安装路径做成参数。

## 渲染范围与绘制顺序（细节见 terraria_render_spec.md §C + 装备扩展）
后→前：back hair → 后臂(skin+armor) → 身体皮肤 → 腿(皮肤→腿甲/裤鞋) → 躯干(皮肤→身甲/衣)+后肩 → 头(头/眼白/瞳/眼睑) → front hair → 头甲 → 前臂(skin+armor)+前肩。
装备替换默认衣物；装饰逐部位优先于装备；染料逐部位作用于最终显示件（dye.py）。

## 接入点（生产代码参考，实现时自行定位）
- 命令：`nextbot/plugins/player_query.py` 的 `handle_my_inventory`（本人）/ `handle_user_inventory`（他人）模式。
- 卡片：`server/pages/inventory_page.py` / `user_info_page.py` 模板模式 + `server/web_server.py create_inventory_page` + `nextbot/screenshot_render.py render_and_send_screenshot`，风格 `DESIGN.md`。
- 权限：`nextbot/db.py` `DEFAULT_GUEST_PERMISSIONS` + `command_config`。
- API：`request_server_api(server, f"/nextbot/users/{quote(name,safe='')}/appearance")`。
</content>
