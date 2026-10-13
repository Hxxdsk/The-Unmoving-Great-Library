"""独立进程入口；仅加载选定工具。"""

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", choices=["color", "matcher", "generator", "joiner"])
    args = parser.parse_args()
    if args.tool == "color":
        from color_tool.ui import main as run
        run()
    elif args.tool:
        from suite.legacy import run_legacy
        run_legacy(args.tool)
    else:
        from suite.hub import main as run
        run()


if __name__ == "__main__":
    main()
