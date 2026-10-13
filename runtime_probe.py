"""仅标准库启动的能力检查器；供原生 EXE 与工具箱首页调用。"""

from __future__ import annotations

import argparse
import importlib
import io
import json
import os
import platform
import sys
from pathlib import Path


def check_runtime(gui: bool = True) -> dict:
    checks = []

    def check(name, fn, package=""):
        try:
            info = fn()
            checks.append({"name": name, "ok": True, "package": package, "detail": str(info or "正常")})
        except Exception as exc:
            checks.append({"name": name, "ok": False, "package": package, "detail": str(exc)[:800]})

    def syntax():
        root = Path(__file__).resolve().parent
        for parent, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"build", "dist", "__pycache__"}]
            for name in files:
                if name.endswith(".py"):
                    path = Path(parent) / name
                    compile(path.read_bytes(), str(path), "exec")
        return "源码可解析"

    check("Python 语法", syntax)

    def tk_test():
        import tkinter as tk
        if gui:
            root = tk.Tk()
            try:
                root.withdraw()
                root.update_idletasks()
            finally:
                root.destroy()
        else:
            tk.Tcl().eval("info patchlevel")
        return f"Tk {tk.TkVersion}" + ("（已创建窗口）" if gui else "（仅 Tcl 检查）")

    check("Tk 界面", tk_test)

    def qt_test():
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import __version__
        from PySide6.QtWidgets import QApplication, QWidget
        app = QApplication.instance() or QApplication([])
        window = QWidget()
        window.resize(100, 100)
        window.show()
        app.processEvents()
        window.close()
        return __version__ + "（已创建窗口）"

    check("Qt 界面", qt_test, "PySide6")

    def pillow_test():
        from PIL import Image, __version__
        for fmt in ("PNG", "JPEG"):
            buffer = io.BytesIO()
            Image.new("RGB", (16, 16), "white").save(buffer, fmt)
            buffer.seek(0)
            with Image.open(buffer) as image:
                image.load()
        from PIL import ImageTk
        return __version__

    check("图片读写", pillow_test, "Pillow")

    def excel_test():
        import openpyxl
        stream = io.BytesIO()
        wb = openpyxl.Workbook()
        wb.active.append(["商品编码", "颜色"])
        wb.active.append(["TEST01", "黑色"])
        wb.save(stream)
        stream.seek(0)
        loaded = openpyxl.load_workbook(stream)
        try:
            assert loaded.active["A2"].value == "TEST01"
        finally:
            loaded.close()
        return openpyxl.__version__

    check("Excel 读写", excel_test, "openpyxl")

    def pandas_test():
        import pandas as pd
        stream = io.BytesIO()
        pd.DataFrame({"编码": ["TEST01"]}).to_excel(stream, index=False, engine="openpyxl")
        stream.seek(0)
        assert pd.read_excel(stream, engine="openpyxl").iloc[0, 0] == "TEST01"
        return pd.__version__

    check("商品表处理", pandas_test, "pandas")
    check("旧版 XLS 支持", lambda: importlib.import_module("xlrd").__version__, "xlrd")

    def entry_test():
        from suite.legacy import load_original
        from color_tool import data, gemini
        for key in ("matcher", "generator", "joiner"):
            load_original(key)
        return "四个工具均可导入"

    check("四个独立入口", entry_test)
    return {"ready": all(item["ok"] for item in checks), "checks": checks,
            "python": sys.executable, "version": platform.python_version(), "architecture": platform.machine(),
            "gui_checked": gui}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-gui", action="store_true")
    args = parser.parse_args()
    result = check_runtime(not args.no_gui)
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
