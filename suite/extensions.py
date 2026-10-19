"""独立工具注册；新增入口自动进入首页与环境依赖检查。"""


def register_extensions(register):
    from .registry import Tool, Dependency

    register(
        Tool(
            "listing",
            "抖店批量上架",
            "逐货号填写 · 保存草稿",
            "你收好素材和标题，我陪每一款走到草稿箱。",
            entrypoint="suite.listing.ui:main",
            dependencies=(
                Dependency("playwright.sync_api", "playwright"),
                Dependency("websockets.asyncio.server", "websockets>=13"),
            ),
        )
    )
