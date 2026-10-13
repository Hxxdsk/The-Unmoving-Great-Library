"""独立进程入口；仅加载选定工具。"""

import argparse
import os
import sys


def main():
    from suite.registry import TOOLS, get_tool
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", choices=[tool.key for tool in TOOLS])
    parser.add_argument("--validated-runtime")
    args = parser.parse_args()
    if args.tool == "color":
        from color_tool.ui import main as run
        run()
    elif args.tool:
        tool = get_tool(args.tool)
        if tool.entrypoint:
            import importlib
            module, function = tool.entrypoint.split(":")
            getattr(importlib.import_module(module), function)()
        else:
            from suite.legacy import run_legacy
            run_legacy(args.tool)
    else:
        from suite.hub import main as run
        run(args.validated_runtime)


if __name__ == "__main__":
    main()
