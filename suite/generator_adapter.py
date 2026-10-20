import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path

from .submission import generate, create_input_template
from .tasks import TkTask
from .palette import HEADER
from .master_data import load_master


def apply(module):
    module.load_master = lambda path: load_master(module, path)
    module.CLASS_RULES = [
        (name, ["针织裤", "梭织裤"] if name == "休闲裤" else words, standard, safety)
        for name, words, standard, safety in module.CLASS_RULES
    ]
    module.OPTIONAL_INPUT_COLUMNS = ["图片"]
    module.generate = lambda input_path, master_path, output_path, settings, log_cb=None, **kwargs: generate(
        module, input_path, master_path, output_path, settings, log_cb, **kwargs
    )
    cls = module.App
    build_main = cls._build_main
    build_header = cls._build_header

    def header(self):
        build_header(self)
        top = self.root.winfo_children()[0]
        title_box = top.winfo_children()[0]
        labels = title_box.winfo_children()
        if len(labels) > 1:
            labels[1].configure(text="根据商品资料和主数据生成送审表。")

        def recolor(widget):
            try:
                if widget.cget("bg") == "#172033":
                    widget.configure(bg=HEADER)
            except tk.TclError:
                pass
            for child in widget.winfo_children():
                recolor(child)

        recolor(top)

    def file_row(self, parent, row, label, hint, variable, command):
        ttk.Label(parent, text=label, style="Card.TLabel").grid(
            row=row, column=0, sticky="w", padx=(0, 12), pady=3
        )
        ttk.Entry(parent, textvariable=variable, style="Modern.TEntry").grid(
            row=row, column=1, sticky="ew", pady=3
        )
        ttk.Button(
            parent, text="选择文件", command=command, style="Secondary.TButton"
        ).grid(row=row, column=2, padx=(10, 0), pady=3)

    def main(self):
        from .ai_controls import GeminiPanel

        build_main(self)
        outer = self.root.nametowidget(
            self.main_canvas.itemcget(self._main_window, "window")
        )
        file_card = outer.winfo_children()[0]
        self.ai_panel = GeminiPanel(outer, "AI 面料 / 图片补全 / 上下装判断", component="generator")
        self.ai_panel.pack(fill="x", after=file_card, pady=(10, 0))
        self.task = TkTask(self.root)
        self.cancel_button = ttk.Button(
            self.btn.master, text="取消", state="disabled", command=self.task.cancel.set
        )
        self.cancel_button.pack(side="right", before=self.btn, padx=(8, 0))

        def template():
            path = filedialog.asksaveasfilename(
                parent=self.root,
                title="保存输入模板",
                defaultextension=".xlsx",
                initialfile="送审输入模板.xlsx",
                filetypes=[("Excel", "*.xlsx")],
            )
            if path:
                create_input_template(path)
                self.log("输入模板已保存；上下装自动判断，无须填写。图片列可选，支持插入图片或本地图片路径；加绒标准价另加20。")

        ttk.Button(self.ai_panel, text="输入模板", command=template).grid(
            row=1, column=0, sticky="w"
        )
        self._close_requested = False

        def close():
            self.ai_panel.persist()
            if self.task.busy or self.ai_panel.task.busy:
                self._close_requested = True
                self.task.cancel.set()
                self.ai_panel.task.cancel.set()
                self._set_status("正在结束", "running")
                self.root.after(100, wait_close)
            else:
                self.root.destroy()

        def wait_close():
            if self.task.busy or self.ai_panel.task.busy:
                self.root.after(100, wait_close)
            else:
                self.root.destroy()

        self.root.protocol("WM_DELETE_WINDOW", close)

    def run(self, auto_reason=None):
        if self.task.busy:
            return
        input_path, master_path, output_path = (
            variable.get().strip()
            for variable in (self.var_input, self.var_master, self.var_output)
        )
        if not output_path and input_path:
            output_path = str(
                Path(input_path).with_name(Path(input_path).stem + "_送审导入.xlsx")
            )
            self.var_output.set(output_path)
        error = self._validate_paths(input_path, master_path, output_path)
        if error:
            messagebox.showwarning("请检查文件", error)
            return
        settings = self.get_settings()
        client = self.ai_panel.client(self.task.cancel)
        self.btn.configure(state="disabled")
        self.open_btn.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress.start(12)
        self._set_status("生成中", "running")
        if auto_reason != "color_update":
            self.clear_log()
        if not client and self.ai_panel.enabled.get():
            self.log(self.ai_panel.status.get(), "warning")
        self.log("开始生成" + ("（AI 已启用）" if client else "（本地规则，原文分列）"))

        def work(cancel, log):
            return module.generate(
                input_path,
                master_path,
                output_path,
                settings,
                log,
                ai_client=client,
                cancel=cancel,
            )

        def done(result):
            if self._close_requested:
                return
            warnings, count, missing_colors, color_issues = result
            self.last_output_path = output_path
            self.open_btn.configure(state="normal")
            self._set_status("生成完成", "success")
            for warning in warnings:
                self.log(warning, "warning")
            if missing_colors and module.load_master(master_path)[3]:
                messagebox.showwarning(
                    "核对颜色", f"已生成 {count} 行，请连接缺失颜色后重新生成。"
                )
                self.open_color_panel(missing_colors, master_path)
            elif missing_colors:
                messagebox.showwarning(
                    "核对颜色",
                    f"已生成 {count} 行。\n主数据未提供颜色编码，相关字段已留空。\n请补入【颜色】表后重新生成。",
                )
            elif color_issues:
                messagebox.showwarning(
                    "核对颜色", self._color_issue_summary(color_issues)
                )
            else:
                messagebox.showinfo(
                    "生成完成",
                    f"已生成 {count} 行。\n请检查黄色单元格和“AI核对”工作表。\n{output_path}",
                )

        def failed(message):
            self.log(message, "error")
            self._set_status(
                "已取消" if self.task.cancel.is_set() else "生成失败", "error"
            )
            if not self._close_requested and not self.task.cancel.is_set():
                messagebox.showerror("生成失败", message)

        def finished():
            self.progress.stop()
            self.btn.configure(state="normal")
            self.cancel_button.configure(state="disabled")

        self.task.start(work, done, failed, finished, lambda message: self.log(message))

    cls._build_header, cls._build_main, cls._add_file_row, cls.run = (
        header,
        main,
        file_row,
        run,
    )
