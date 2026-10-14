"""各工具仅保留 AI 开关；配置与凭据统一由工具箱首页管理。"""

import tkinter as tk
from tkinter import ttk
from color_tool.gemini import GeminiError
from .shared_ai import shared_client
from .tasks import TkTask


class GeminiPanel(ttk.Frame):
    def __init__(self, parent, label="启用 AI", component="shared"):
        super().__init__(parent, style="Card.TFrame", padding=(12, 8))
        self.enabled = tk.BooleanVar(value=True)
        self.status = tk.StringVar()
        self.task = TkTask(self.winfo_toplevel())
        ttk.Checkbutton(self, text=label, variable=self.enabled).grid(
            row=0, column=0, sticky="w"
        )
        self.columnconfigure(1, weight=1)

    def persist(self):
        pass

    def client(self, cancel=None):
        if not self.enabled.get():
            return None
        try:
            return shared_client(cancel)
        except GeminiError as exc:
            self.status.set(str(exc))
            return None
