"""独立工具注册；新增入口自动进入首页与环境依赖检查。"""


def register_extensions(register):
    from .registry import Tool, Dependency

    register(
        Tool(
            "listing",
            "抖店批量上架",
            "逐货号填写 · 保存草稿",
            "按货号批量填写商品信息并保存抖店草稿。",
            entrypoint="suite.listing.ui:main",
            dependencies=(
                Dependency("playwright.sync_api", "playwright"),
                Dependency("websockets.asyncio.server", "websockets>=13"),
            ),
        )
    )
