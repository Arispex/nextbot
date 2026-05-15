"""NextBot Web / Render 服务子系统。

- ``web_server``: FastAPI app 工厂 + uvicorn 启动
- ``screenshot``: Playwright 截图入口
- ``page_store``: 渲染 token cache
- ``settings_service``: 设置持久化
- ``server_config``: 运行期配置加载
"""

from __future__ import annotations
