# 管理员列表

## Goal

新增「管理员列表」命令，读取 `owner_id` 配置，通过 QQ 相关接口获取头像和昵称，生成卡片式网页截图输出，适配双主题。

## 数据来源

- **管理员 ID 列表**：`get_owner_ids()`（`nextbot/access_control.py`）
- **QQ 头像**：`http://q1.qlogo.cn/g?b=qq&nk={qq}&s=100`（直接嵌入 `<img>` src，Playwright 截图时加载）
- **QQ 昵称**：`https://users.qzone.qq.com/fcg-bin/cgi_get_portrait.fcg?uins={qq}`
  - 返回 JSONP：`portraitCallBack({"QQ":["头像url",..,"昵称",...]})`
  - 昵称在数组 index 6
  - 响应编码为 **GBK**，需以 GBK 解码
  - 请求失败或解析失败时昵称留空（不阻断渲染）

## 命令

- 命令名：`管理员列表`
- 权限：`admin.list`（默认加入 guest 权限组？No，建议只给 owner 自动有，其他按需配置）
- `command_control` 参数：无

## 页面设计

- 卡片布局（横向排列，每行最多 3 个），每张卡片：
  - 顶部：圆形头像（80×80）
  - 昵称（大字）
  - QQ 号（小字，灰色）
- 页眉：标题「管理员列表」+ 生成时间
- 双主题（亮色/暗色）
- 截图宽度：820px

## 后端实现

### 昵称获取

在 `nextbot/plugins/admin_list.py` 中：

```python
import asyncio, json, re
import httpx

async def _fetch_nickname(qq: str) -> str:
    url = f"https://users.qzone.qq.com/fcg-bin/cgi_get_portrait.fcg?uins={qq}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
        raw = resp.content.decode("gbk", errors="replace")
        m = re.search(r'portraitCallBack\((\{.*\})\)', raw)
        if not m:
            return ""
        data = json.loads(m.group(1))
        arr = data.get(qq, [])
        return str(arr[6]).strip() if len(arr) > 6 else ""
    except Exception:
        return ""
```

### 页面数据结构

```python
admins = [
    {"user_id": "123", "nickname": "名字"},
    ...
]
```

## Files to Create / Modify

- `nextbot/plugins/admin_list.py`（新建）
- `server/pages/admin_list_page.py`（新建）
- `server/templates/admin_list.html`（新建）
- `server/routes/render.py`：新增路由
- `server/web_server.py`：新增 `create_admin_list_page`

## Acceptance Criteria

- [ ] `管理员列表` 命令输出截图
- [ ] 每位管理员显示头像、昵称、QQ 号
- [ ] 昵称从 QZ 接口获取，GBK 正确解码
- [ ] 昵称获取失败时显示空白/「未知」，不报错
- [ ] 亮色/暗色主题均正常渲染
