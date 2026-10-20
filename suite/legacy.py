"""加载原稿并统一外观；业务函数保持原样，各工具在各自进程使用。"""

import importlib.util
import sys
from pathlib import Path

from . import palette as p
from .registry import ROOT, get_tool


def load_original(key: str):
    tool = get_tool(key)
    path = ROOT / "original_scripts" / tool.filename
    spec = importlib.util.spec_from_file_location("greatlibrary_" + key, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    from .enhancements import enhance
    enhance(module, key)
    return module


def common_ttk_theme(root):
    from tkinter import ttk
    style = ttk.Style(root)
    style.theme_use("clam")
    root.option_add("*Font", (p.FONT, 10))
    style.configure(".", font=(p.FONT, 10), foreground=p.TEXT, background=p.BG)
    for name in ("Primary.TButton", "Accent.TButton"):
        style.configure(name, background=p.PRIMARY, foreground="white", relief="flat",
                        borderwidth=0, padding=(16, 9), font=(p.FONT, 10, "bold"))
        style.map(name, background=[("disabled", "#dce3f1"), ("active", p.PRIMARY_HOVER)],
                  foreground=[("disabled", "#8f9db5")])
    for name in ("Ghost.TButton", "Secondary.TButton", "Small.TButton"):
        style.configure(name, background=p.CARD, foreground=p.TEXT, padding=(12, 7),
                        bordercolor=p.BORDER, relief="flat")
        style.map(name, background=[("active", "#edf2ff")])
    style.configure("TCombobox", padding=5, fieldbackground="white", foreground=p.TEXT)
    style.configure("TEntry", padding=6, fieldbackground="white")
    style.configure("Treeview", background=p.CARD, fieldbackground=p.CARD, rowheight=30)
    style.configure("Treeview.Heading", background="#eef2fa", font=(p.FONT, 10, "bold"))
    style.configure("Modern.Horizontal.TProgressbar", troughcolor="#e6ebf4", background=p.PRIMARY,
                    lightcolor=p.PRIMARY, darkcolor=p.PRIMARY, thickness=6)
    if "SuiteCheck.indicator" not in style.element_names():
        import tkinter as tk
        off = tk.PhotoImage(master=root, file=str(ROOT / "assets" / "checkbox-off.png"))
        on = tk.PhotoImage(master=root, file=str(ROOT / "assets" / "checkbox-on.png"))
        root._suite_check_images = (off, on)
        style.element_create("SuiteCheck.indicator", "image", off, ("selected", on), sticky="")
    def check_layout(layout):
        for index, (name, options) in enumerate(layout):
            if name.endswith(".indicator"):
                layout[index] = ("SuiteCheck.indicator", options)
            if "children" in options:
                check_layout(options["children"])
        return layout
    style.layout("TCheckbutton", check_layout(style.layout("TCheckbutton")))
    style.layout("Modern.TCheckbutton", style.layout("TCheckbutton"))


def apply_palette(module, key):
    if key == "matcher":
        cls = module.ExcelMatcherApp
        for name in ("BG", "CARD", "TEXT", "MUTED", "BORDER", "PRIMARY", "SUCCESS", "WARNING", "DANGER"):
            setattr(cls, name, getattr(p, name))

        def build_shell(self):
            import tkinter as tk
            header = tk.Frame(self.root, bg=p.HEADER, height=82)
            header.pack(fill="x")
            header.pack_propagate(False)
            title_wrap = tk.Frame(header, bg=p.HEADER)
            title_wrap.pack(fill="both", expand=True, padx=28, pady=13)
            tk.Label(title_wrap, text="商品编码匹配工具", font=(p.FONT, 18, "bold"),
                     fg="white", bg=p.HEADER).pack(anchor="w")
            self.shell_subtitle = tk.Label(title_wrap, text="聚水潭 SKU 编码 → 抖店商家编码",
                                          font=(p.FONT, 9), fg="#c4cee2", bg=p.HEADER)
            self.shell_subtitle.pack(anchor="w", pady=(4, 0))
            self.container = tk.Frame(self.root, bg=p.BG)
            self.container.pack(fill="both", expand=True, padx=28, pady=22)

        cls._build_shell = build_shell
    elif key == "generator":
        module.UI.update(bg=p.BG, card=p.CARD, text=p.TEXT, muted=p.MUTED, line=p.BORDER,
                         accent=p.PRIMARY, accent_hover=p.PRIMARY_HOVER, accent_soft="#eaf0ff",
                         success=p.SUCCESS, warning=p.WARNING, danger=p.DANGER, log_bg="#f8faff")
    elif key == "joiner":
        values = {"BG": p.BG, "SURFACE": p.CARD, "TEXT": p.TEXT, "TEXT_2": p.MUTED,
                  "TEXT_3": "#8d9bb2", "BORDER": p.BORDER, "PRIMARY": p.PRIMARY,
                  "PRIMARY_HOVER": p.PRIMARY_HOVER, "SUCCESS": p.SUCCESS, "WARNING": p.WARNING,
                  "DANGER": p.DANGER, "HEADER": p.HEADER, "FONT": p.FONT}
        for name, value in values.items():
            setattr(module, name, value)


def decorate_header(root, key):
    """Replace the header description without changing tool behavior."""
    import tkinter as tk
    def replace_subtitle(widget):
        for child in widget.winfo_children():
            if isinstance(child, tk.Label):
                text = str(child.cget("text"))
                if len(text) > 14 and not child.cget("image"):
                    child.configure(text=get_tool(key).description)
                    return True
            elif replace_subtitle(child):
                return True
        return False
    for header in root.winfo_children():
        try:
            if str(header.cget("bg")).lower() == p.HEADER.lower():
                replace_subtitle(header)
                break
        except tk.TclError:
            continue


def run_legacy(key: str):
    import tkinter as tk
    from .branding import identify_component
    identify_component(key)
    tool = get_tool(key)
    module = load_original(key)
    apply_palette(module, key)
    root = tk.Tk()
    icon = tk.PhotoImage(file=str(ROOT / "assets" / ("logo-" + key + ".png")))
    root.iconphoto(True, icon)
    if sys.platform == "win32":
        root.iconbitmap(default=str(ROOT / "assets" / ("logo-" + key + ".ico")))
    root._suite_icon = icon
    app = getattr(module, tool.app_class)(root)
    common_ttk_theme(root)
    root.title("大图书馆工具箱 · " + tool.title)
    # 原生成器的标题颜色是局部常量；只调整标题区域，保留所有业务逻辑。
    if key == "generator":
        def recolor(widget):
            try:
                if str(widget.cget("bg")).lower() == "#172033":
                    widget.configure(bg=p.HEADER)
            except tk.TclError:
                pass
            for child in widget.winfo_children():
                recolor(child)
        recolor(root)
    decorate_header(root, key)
    from .guide_tk import install_tk_guide
    install_tk_guide(app, key)
    root.mainloop()
    return app
