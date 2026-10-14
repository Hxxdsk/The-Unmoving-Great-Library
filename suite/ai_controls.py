import tkinter as tk
from tkinter import ttk
from pathlib import Path

from color_tool.credentials import KeyStore, CredentialError
from color_tool.gemini import GeminiClient
from color_tool.settings import Settings
from .tasks import TkTask


def shared_ai_paths(component="shared"):
    from PySide6.QtCore import QCoreApplication, QStandardPaths

    QCoreApplication.setOrganizationName("GreatLibrary")
    QCoreApplication.setApplicationName("ColorImageAssistant")
    directory = Path(
        QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppConfigLocation
        )
    )
    settings = Settings(directory / (component + "-ai.json"))
    if not settings.get("model"):
        settings.data["model"] = Settings(directory / "preferences.json").get("model")
    return settings, KeyStore(directory / "gemini-key.dpapi")


class GeminiPanel(ttk.Frame):
    def __init__(
        self, parent, label="启用 AI", settings=None, store=None, component="shared"
    ):
        super().__init__(parent, style="Card.TFrame", padding=(12, 8))
        self.settings, self.store = (
            (settings, store) if settings and store else shared_ai_paths(component)
        )
        self.enabled = tk.BooleanVar(value=True)
        self.key = tk.StringVar()
        self.model = tk.StringVar(value=self.settings.get("model"))
        self.status = tk.StringVar(value="填入 Key → 检测模型 → 选择模型")
        self.verified = False
        self._save_timer = None
        self.task = TkTask(self.winfo_toplevel())
        ttk.Checkbutton(self, text="Gemini · " + label, variable=self.enabled).grid(
            row=0, column=0, padx=(0, 10)
        )
        ttk.Entry(self, textvariable=self.key, show="●", width=30).grid(
            row=0, column=1, sticky="ew"
        )
        self.detect = ttk.Button(self, text="检测模型", command=self.detect_models)
        self.detect.grid(row=0, column=2, padx=8)
        self.models = ttk.Combobox(
            self, textvariable=self.model, state="readonly", width=35
        )
        self.models.grid(row=0, column=3, sticky="ew")
        self.models.bind("<<ComboboxSelected>>", lambda event: self.persist())
        self.models.bind("<MouseWheel>", lambda event: "break")
        ttk.Button(self, text="忘记 Key", command=self.forget).grid(
            row=0, column=4, padx=(8, 0)
        )
        ttk.Label(self, textvariable=self.status, style="Muted.TLabel").grid(
            row=1, column=1, columnspan=4, sticky="w", pady=(4, 0)
        )
        self.columnconfigure(1, weight=1)
        self.columnconfigure(3, weight=1)
        try:
            restored = self.store.load()
            self.key.set(restored)
            self.verified = bool(
                restored
                and self.model.get()
                and self.model.get() in self.store.validated_models
            )
            if restored:
                self.models.configure(
                    values=[self.model.get()] if self.model.get() else []
                )
                self.status.set(
                    "已恢复 Key 和模型" if self.verified else "已恢复 Key，请检测模型"
                )
        except (CredentialError, OSError):
            self.status.set("请重新填入 Key")
        self.key.trace_add("write", self.key_changed)

    def key_changed(self, *_):
        self.verified = False
        if self._save_timer:
            self.after_cancel(self._save_timer)
        self._save_timer = self.after(700, self.persist)

    def persist(self):
        self._save_timer = None
        try:
            if self.model.get():
                self.settings.set("model", self.model.get())
            if self.key.get().strip():
                self.store.save(
                    self.key.get().strip(), self.model.get() if self.verified else ""
                )
        except (CredentialError, OSError):
            self.status.set("Key 本次可用；系统加密记忆不可用")

    def forget(self):
        try:
            self.store.forget()
        except OSError:
            self.status.set("删除失败，请检查配置目录")
            return
        self.key.set("")
        if self._save_timer:
            self.after_cancel(self._save_timer)
            self._save_timer = None
        self.status.set("已忘记 Key")

    def detect_models(self):
        try:
            client = GeminiClient(self.key.get())
        except Exception as exc:
            self.status.set(str(exc))
            return
        self.detect.configure(state="disabled")
        self.status.set("正在检测…")
        detected_key = self.key.get().strip()

        def done(models):
            if self.key.get().strip() != detected_key:
                self.verified = False
                self.status.set("Key 已改变，请重新检测")
                return
            names = [item["name"] for item in models]
            self.models.configure(values=names)
            self.model.set(self.model.get() if self.model.get() in names else names[0])
            self.verified = True
            self.persist()
            self.status.set("模型已就绪")

        def work(cancel, log):
            client.cancel = cancel
            return client.list_models()

        self.task.start(
            work,
            done,
            self.status.set,
            finished=lambda: self.detect.configure(state="normal"),
        )

    def client(self, cancel=None):
        if (
            self.enabled.get()
            and self.verified
            and self.key.get().strip()
            and self.model.get()
        ):
            self.persist()
            return GeminiClient(self.key.get(), self.model.get(), cancel)
        return None
