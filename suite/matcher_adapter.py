import tkinter as tk
from tkinter import ttk
import unicodedata
import re

from . import palette as p


def size_key(value):
    """Ignore balanced parenthesized notes, keeping the remaining size exact."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    while True:
        stripped = re.sub(r"\([^()]*\)", "", text)
        if stripped == text:
            return re.sub(r"\s+", "", text).upper()
        text = stripped


def color_key(text):
    text = unicodedata.normalize("NFKC", str(text)).strip().replace(" ", "").lower()
    return text[:-1] if text.endswith("色") else text


def family(text):
    text = color_key(text)
    return next(
        (
            name
            for name in (
                "灰",
                "蓝",
                "绿",
                "粉",
                "黑",
                "白",
                "卡其",
                "棕",
                "红",
                "黄",
                "紫",
                "橙",
            )
            if name in text
        ),
        text,
    )


def similarity(source, target):
    a, b = {color_key(value) for value in source}, {
        color_key(value) for value in target
    }
    exact = len(a & b)
    near = len(
        ({family(value) for value in a - b} & {family(value) for value in b - a}) - {""}
    )
    return (exact * 2 + near * 0.5) / max(1, len(a | b)), exact, near


def source_colors(app, style):
    if hasattr(app, "_source_color_index"):
        return app._source_color_index.get(app.norm_id(style), [])
    frame = app.jushuitan_df
    if frame is None:
        return []
    subset = frame.loc[frame["款式编码"].map(app.norm_id) == app.norm_id(style), "颜色"]
    return list(
        dict.fromkeys(app.norm_text(value) for value in subset if app.norm_text(value))
    )


def pid_colors(app, pid):
    if hasattr(app, "_pid_color_index"):
        return app._pid_color_index.get(app.norm_id(pid), [])
    frame = app.doudian_df
    if frame is None:
        return []
    subset = frame.loc[
        frame["商品id(必填)"].map(app.norm_id) == app.norm_id(pid), "商品规格"
    ]
    return list(
        dict.fromkeys(
            app.extract_color(value) for value in subset if app.extract_color(value)
        )
    )


def colors_label(parent, values, width=420):
    label = tk.Label(
        parent,
        text=" · ".join(values) or "—",
        bg=p.CARD,
        fg=p.TEXT,
        anchor="w",
        justify="left",
        wraplength=width,
        font=(p.FONT, 9),
    )
    label.pack(fill="x", padx=10, pady=(0, 6))
    parent.bind(
        "<Configure>",
        lambda event: label.configure(wraplength=max(120, event.width - 24)),
        add="+",
    )
    return label


def apply(module):
    cls = module.ExcelMatcherApp
    build_lookup = cls._build_js_lookup
    resolve_code = cls._resolve_code

    def normalized_lookup(self):
        lookup = {}
        for (style, color, size), codes in build_lookup(self).items():
            normalized = size_key(size)
            if not normalized:
                continue
            target = lookup.setdefault((style, color, normalized), [])
            for code in codes:
                if code not in target:
                    target.append(code)
        return lookup

    def normalized_resolve(self, pid, dd_color, dd_size, js_lookup):
        return resolve_code(self, pid, dd_color, size_key(dd_size), js_lookup)

    cls._build_js_lookup = normalized_lookup
    cls._resolve_code = normalized_resolve
    page_header = cls._page_header

    def header(self, step, title, description):
        self._guide_step = step
        methods = {
            "步骤 1 / 3": "选择聚水潭与抖店导出表。",
            "步骤 2 / 3": "关联款号与对应的商品 PID。",
            "步骤 3 / 3": "配对颜色并导出商品编码。",
        }
        page_header(self, step, title, methods.get(step, "连接颜色 → 导出"))

    def style_card(self, parent, style):
        card = tk.Frame(
            parent, bg=p.CARD, highlightbackground=p.BORDER, highlightthickness=1
        )
        card.pack(fill="x", pady=4, padx=1)
        top = tk.Frame(card, bg=p.CARD)
        top.pack(fill="x", padx=10, pady=(7, 3))
        title = tk.Label(
            top,
            text=style,
            bg=p.CARD,
            fg=p.TEXT,
            font=(p.FONT, 10, "bold"),
            cursor="hand2",
        )
        title.pack(side="left")
        ttk.Button(
            top,
            text="选择此款",
            style="Small.TButton",
            command=lambda: self._activate_style(style),
        ).pack(side="left", padx=8)
        ttk.Button(
            top,
            text="+ PID",
            style="Small.TButton",
            command=lambda: (self._activate_style(style), self._add_pid_row(style)),
        ).pack(side="right")
        label = colors_label(card, source_colors(self, style))
        for widget in (card, title, label):
            widget.bind("<Button-1>", lambda event, s=style: self._activate_style(s))
        rows = tk.Frame(card, bg=p.CARD)
        rows.pack(fill="x", padx=8, pady=(0, 6))
        self.style_card_map[style] = {"card": card, "rows_frame": rows}
        self.style_pid_rows[style] = []

    def new_row(self, style, value=""):
        frame = tk.Frame(self.style_card_map[style]["rows_frame"], bg="#FAFAFA")
        frame.pack(fill="x", pady=3)
        line = tk.Frame(frame, bg="#FAFAFA")
        line.pack(fill="x")
        number = tk.Label(
            line, text="PID", width=5, bg="#FAFAFA", fg=p.MUTED, font=(p.FONT, 9)
        )
        number.pack(side="left", padx=(8, 4), pady=5)
        combo = ttk.Combobox(line, values=self.dd_pid_list, font=(p.FONT, 10), width=30)
        combo.pack(side="left", fill="x", expand=True, padx=4, pady=5)
        if value:
            combo.set(value)
        remove = ttk.Button(line, text="删除", style="Small.TButton")
        remove.pack(side="right", padx=6, pady=4)
        info = {"frame": frame, "combobox": combo, "label": number, "remove": remove}
        self.style_pid_rows[style].append(info)
        remove.configure(command=lambda: self._remove_pid_row(style, info))
        self._renumber_pid_rows(style)
        preview = tk.Label(
            info["frame"],
            text="",
            bg="#FAFAFA",
            fg=p.MUTED,
            anchor="w",
            justify="left",
            font=(p.FONT, 9),
            wraplength=390,
        )
        info["colors_label"] = preview
        frame.bind(
            "<Configure>",
            lambda event: preview.configure(wraplength=max(120, event.width - 20)),
        )

        def update(event=None):
            chosen = self.norm_id(info["combobox"].get())
            colors = pid_colors(self, chosen) if chosen in self.dd_pid_list else []
            preview.configure(text="抖店：" + " · ".join(colors) if colors else "")
            if colors:
                preview.pack(side="bottom", fill="x", padx=8, pady=(0, 5))
            else:
                preview.pack_forget()
            self._update_style_summary()

        info["combobox"].bind("<<ComboboxSelected>>", update)
        info["combobox"].bind("<KeyRelease>", update)
        info["combobox"].bind("<FocusIn>", lambda event: self._activate_style(style))
        info["combobox"].bind("<MouseWheel>", lambda event: "break")
        update()
        return info

    def activate(self, style):
        self._active_style = style
        for name, values in self.style_card_map.items():
            values["card"].configure(
                highlightbackground=p.PRIMARY if name == style else p.BORDER
            )
        if (
            hasattr(self, "_pid_reference_inner")
            and self._pid_reference_inner.winfo_exists()
        ):
            self._render_pid_reference()

    def choose(self, pid):
        style = getattr(self, "_active_style", None)
        if not style:
            return
        rows = self.style_pid_rows[style]
        existing = [self.norm_id(info["combobox"].get()) for info in rows]
        if pid in existing:
            return
        empty = next(
            (info for info in rows if not self.norm_id(info["combobox"].get())), None
        )
        info = empty or self._add_pid_row(style)
        info["combobox"].set(pid)
        info["combobox"].event_generate("<<ComboboxSelected>>")

    def render_reference(self, *_):
        parent = self._pid_reference_inner
        for widget in parent.winfo_children():
            widget.destroy()
        selected = source_colors(self, getattr(self, "_active_style", ""))
        query = self._pid_search.get().strip().lower()
        entries = [(pid, pid_colors(self, pid)) for pid in self.dd_pid_list]
        entries.sort(key=lambda item: -similarity(selected, item[1])[0])
        self._pid_reference_label.configure(
            text="抖店 PID"
            + (
                " · 对照 " + self._active_style
                if getattr(self, "_active_style", None)
                else ""
            )
        )
        for pid, colors in entries:
            if query and query not in (pid + " " + " ".join(colors)).lower():
                continue
            card = tk.Frame(
                parent, bg=p.CARD, highlightthickness=1, highlightbackground=p.BORDER
            )
            card.pack(fill="x", pady=4, padx=1)
            row = tk.Frame(card, bg=p.CARD)
            row.pack(fill="x", padx=10, pady=(7, 3))
            tk.Label(
                row, text=pid, bg=p.CARD, fg=p.TEXT, font=(p.FONT, 10, "bold")
            ).pack(side="left")
            _, exact, near = similarity(selected, colors)
            tk.Label(
                row,
                text=f"同名 {exact} · 近色 {near}",
                bg=p.CARD,
                fg=p.MUTED,
                font=(p.FONT, 8),
            ).pack(side="left", padx=8)
            ttk.Button(
                row,
                text="选择 PID",
                style="Small.TButton",
                command=lambda value=pid: self._choose_reference_pid(value),
            ).pack(side="right")
            colors_label(card, colors)
            for widget in (card, *card.winfo_children()):
                widget.bind(
                    "<Enter>",
                    lambda event: setattr(self, "_mousewheel_canvas", self._pid_canvas),
                )

    def show_page(self, reset=False):
        previous = {} if reset else self.style_to_pid.copy()
        self._clear_page()
        source_index, target_index = {}, {}
        if self.jushuitan_df is not None:
            for style, color in zip(
                self.jushuitan_df["款式编码"], self.jushuitan_df["颜色"]
            ):
                name, color = self.norm_id(style), self.norm_text(color)
                if name and color:
                    source_index.setdefault(name, {})[color] = None
        if self.doudian_df is not None:
            for pid, spec in zip(
                self.doudian_df["商品id(必填)"], self.doudian_df["商品规格"]
            ):
                name, color = self.norm_id(pid), self.extract_color(spec)
                if name and color:
                    target_index.setdefault(name, {})[color] = None
        self._source_color_index = {
            name: list(colors) for name, colors in source_index.items()
        }
        self._pid_color_index = {
            name: list(colors) for name, colors in target_index.items()
        }
        self._page_header("步骤 2 / 3", "匹配款式与 PID", "")
        self.style_pid_rows = {style: [] for style in self.style_list}
        self.style_card_map = {}
        tools = tk.Frame(self.page, bg=p.BG)
        tools.pack(fill="x", pady=(0, 8))
        ttk.Button(
            tools, text="← 返回", command=self.show_file_page, style="Ghost.TButton"
        ).pack(side="left")
        self.style_summary_label = tk.Label(
            tools, bg=p.BG, fg=p.MUTED, font=(p.FONT, 9)
        )
        self.style_summary_label.pack(side="right")
        panes = tk.PanedWindow(
            self.page, orient="horizontal", bg=p.BG, sashwidth=8, borderwidth=0
        )
        panes.pack(fill="both", expand=True)
        left, right = (tk.Frame(panes, bg=p.BG) for _ in range(2))
        panes.add(left, minsize=380, stretch="always")
        panes.add(right, minsize=380, stretch="always")
        tk.Label(
            left, text="聚水潭款号", bg=p.BG, fg=p.TEXT, font=(p.FONT, 10, "bold")
        ).pack(anchor="w", pady=(0, 6))
        self._pid_reference_label = tk.Label(
            right, text="抖店 PID", bg=p.BG, fg=p.TEXT, font=(p.FONT, 10, "bold")
        )
        self._pid_reference_label.pack(anchor="w", pady=(0, 6))
        self._style_search = tk.StringVar()
        ttk.Entry(left, textvariable=self._style_search).pack(fill="x", pady=(0, 6))
        self._pid_search = tk.StringVar()
        ttk.Entry(right, textvariable=self._pid_search).pack(fill="x", pady=(0, 6))
        wrap, canvas, inner = self._make_scroller(left)
        wrap.pack(fill="both", expand=True)
        for style in self.style_list:
            self._build_style_card(inner, style)
            self._set_style_pids(style, previous.get(style, []))
            card = self.style_card_map[style]["card"]

            def bind_enter(widget):
                widget.bind(
                    "<Enter>", lambda event: setattr(self, "_mousewheel_canvas", canvas)
                )
                for child in widget.winfo_children():
                    bind_enter(child)

            bind_enter(card)
        wrap, self._pid_canvas, self._pid_reference_inner = self._make_scroller(right)
        wrap.pack(fill="both", expand=True)
        self._pid_search.trace_add("write", self._render_pid_reference)

        def filter_left(*_):
            query = self._style_search.get().strip().lower()
            for style, values in self.style_card_map.items():
                values["card"].pack_forget()
                if (
                    query
                    in (style + " " + " ".join(source_colors(self, style))).lower()
                ):
                    values["card"].pack(fill="x", pady=4, padx=1)

        self._style_search.trace_add("write", filter_left)
        bottom = tk.Frame(self.page, bg=p.BG)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Button(
            bottom,
            text="下一步：颜色配对",
            command=self.go_color_page,
            style="Primary.TButton",
        ).pack(side="right")
        self._activate_style(self.style_list[0] if self.style_list else "")
        self._update_style_summary()

    cls._page_header, cls._build_style_card, cls._add_pid_row = (
        header,
        style_card,
        new_row,
    )
    cls._activate_style, cls._render_pid_reference, cls._choose_reference_pid = (
        activate,
        render_reference,
        choose,
    )
    cls.show_style_page = show_page
    if hasattr(cls, "import_mapping_table"):
        delattr(cls, "import_mapping_table")
