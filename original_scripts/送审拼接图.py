import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk, ImageFilter, ImageOps
import os
import threading
import traceback
import shutil
import re
import math
import colorsys


# =========================
# 基础配置
# =========================
APP_TITLE = "送审拼接图 · 智能排序版"
WINDOW_SIZE = "1120x820"
MIN_W, MIN_H = 900, 680

THUMB_SIZE = 150
CARD_W = 178
CARD_H = 214
CARD_GAP = 16
TOP_GAP = 14

# 智能排序参数：尽量保持原有识别逻辑，只增强“近似颜色配对”和“低置信度待确认”。
STRICT_COLOR_GROUP_THRESHOLD = 0.19
RELAXED_COLOR_PAIR_THRESHOLD = 0.30
SIDE_PAIR_MIN_GAP = 3.0

BG = "#F4F7FB"
SURFACE = "#FFFFFF"
SURFACE_ALT = "#F8FAFC"
TEXT = "#111827"
TEXT_2 = "#475569"
TEXT_3 = "#94A3B8"
BORDER = "#E2E8F0"
PRIMARY = "#2563EB"
PRIMARY_HOVER = "#1D4ED8"
SUCCESS = "#059669"
SUCCESS_HOVER = "#047857"
WARNING = "#D97706"
WARNING_HOVER = "#B45309"
DANGER = "#DC2626"
HEADER = "#0F172A"
HEADER_2 = "#CBD5E1"

FONT = "Microsoft YaHei UI"

try:
    RESAMPLE_FILTER = Image.Resampling.LANCZOS
except AttributeError:
    try:
        RESAMPLE_FILTER = Image.LANCZOS
    except AttributeError:
        RESAMPLE_FILTER = Image.ANTIALIAS


# =========================
# 通用工具
# =========================
def natural_sort_key(filename):
    """自然排序：1, 2, 3 ... 10, 11。"""
    parts = re.split(r"(\d+)", filename)
    return [int(part) if part.isdigit() else part.lower() for part in parts]


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def rgb_to_hex(rgb):
    r, g, b = [int(clamp(round(v), 0, 255)) for v in rgb]
    return f"#{r:02x}{g:02x}{b:02x}"


def circular_hue_mean(hues, weights=None):
    if not hues:
        return 0.0
    if weights is None:
        weights = [1.0] * len(hues)
    x = 0.0
    y = 0.0
    for h, w in zip(hues, weights):
        angle = 2 * math.pi * h
        x += math.cos(angle) * w
        y += math.sin(angle) * w
    if abs(x) < 1e-9 and abs(y) < 1e-9:
        return hues[0]
    return (math.atan2(y, x) / (2 * math.pi)) % 1.0


def color_name_from_hsv(h, s, v):
    """把代表色转换成便于界面展示的中文色名。仅用于分组提示，不区分款式。"""
    if v < 0.23:
        return "黑色"

    if s < 0.065:
        if v >= 0.88:
            return "白色"
        if v <= 0.38:
            return "深灰色"
        return "灰色"

    deg = (h * 360.0) % 360.0

    # 低亮度暖色更接近棕色
    if 15 <= deg < 50 and v < 0.58:
        base = "棕色"
    elif deg < 15 or deg >= 345:
        base = "红色"
    elif deg < 45:
        base = "橙色"
    elif deg < 70:
        base = "黄色"
    elif deg < 165:
        base = "绿色"
    elif deg < 195:
        base = "青色"
    elif deg < 255:
        base = "蓝色"
    elif deg < 292:
        base = "紫色"
    else:
        base = "粉色"

    # 给低饱和、高亮度的颜色增加“浅”提示，便于区分浅蓝、浅绿等
    if v > 0.70 and s < 0.30 and base not in ("黄色", "橙色"):
        return "浅" + base
    if v < 0.42 and base not in ("棕色",):
        return "深" + base
    return base


def filename_side_hint(filename):
    """如果文件名本身带正反面信息则优先使用。"""
    lower = filename.lower()
    front_tokens = ["正面", "front", "_f.", "_f_", "-f-", "前面"]
    back_tokens = ["背面", "back", "rear", "_b.", "_b_", "-b-", "后面"]
    if any(t in lower for t in front_tokens):
        return "正面"
    if any(t in lower for t in back_tokens):
        return "背面"
    return None


def color_distance(sig_a, sig_b):
    """HSV 感知距离。越小代表越接近同一颜色。"""
    h1, s1, v1 = sig_a
    h2, s2, v2 = sig_b
    dh = abs(h1 - h2)
    dh = min(dh, 1.0 - dh)

    # 灰白黑：色相意义小，重点看明度/饱和度
    if s1 < 0.09 and s2 < 0.09:
        return abs(v1 - v2) * 1.65 + abs(s1 - s2) * 0.60

    return dh * 2.20 + abs(s1 - s2) * 0.65 + abs(v1 - v2) * 0.85


def _hue_gap(h1, h2):
    d = abs(h1 - h2)
    return min(d, 1.0 - d)


def colors_are_close_enough_for_soft_pair(sig_a, sig_b):
    """
    仅用于“两个都待确认的单张颜色组”之间的二次配对。

    目标不是把颜色分类放宽，而是处理同一件衣服正/背面因光线、褶皱、
    开合状态不同导致代表色略有偏差的情况。
    """
    h1, s1, v1 = sig_a
    h2, s2, v2 = sig_b
    dist = color_distance(sig_a, sig_b)
    if dist > RELAXED_COLOR_PAIR_THRESHOLD:
        return False

    # 黑/白/灰一类低饱和颜色：重点限制明度差，避免黑色和浅灰被误合并。
    if s1 < 0.11 and s2 < 0.11:
        return abs(v1 - v2) <= 0.18

    # 彩色商品：允许色相有小幅漂移，但不能跨到明显不同色系。
    if _hue_gap(h1, h2) > 0.075:   # 约 27°
        return False
    if abs(v1 - v2) > 0.24:
        return False
    if abs(s1 - s2) > 0.36:
        return False
    return True


def open_rgba(path):
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    return img.convert("RGBA")


def composite_for_analysis(rgba):
    """透明图先铺白底，避免透明区域干扰识别。"""
    base = Image.new("RGB", rgba.size, "white")
    base.paste(rgba, (0, 0), rgba)
    return base


def analyze_image(pil_rgb):
    """
    识别两件事：
    1) 服装代表色（用于同色分组）
    2) 正面倾向分数（同色组内分辨正/反面）

    设计前提：同一文件夹是一款，不做款式分类；只做颜色 + 正反面。
    算法不依赖 OpenCV / AI 模型，仅依赖 Pillow，方便直接在原环境运行。
    """
    sample = pil_rgb.copy()
    sample.thumbnail((240, 240), RESAMPLE_FILTER)
    w, h = sample.size
    px = sample.load()

    if w < 8 or h < 8:
        return {
            "color_sig": (0.0, 0.0, 1.0),
            "color_name": "未知色",
            "color_rgb": (220, 220, 220),
            "front_score": 0.0,
        }

    # 估计背景色：取四角区域平均，兼容白底/浅灰底等商品图
    r = max(3, min(w, h) // 28)
    corners = []
    for yy in list(range(r)) + list(range(max(0, h - r), h)):
        for xx in list(range(r)) + list(range(max(0, w - r), w)):
            corners.append(px[xx, yy])
    bg = tuple(sum(c[i] for c in corners) / len(corners) for i in range(3))

    mask = Image.new("L", (w, h), 0)
    mp = mask.load()
    fg_pixels = []

    # 与角落背景有一定色差的区域视作商品主体
    for y in range(h):
        for x in range(w):
            c = px[x, y]
            d = math.sqrt(
                (c[0] - bg[0]) ** 2 +
                (c[1] - bg[1]) ** 2 +
                (c[2] - bg[2]) ** 2
            )
            if d > 30:
                mp[x, y] = 255
                fg_pixels.append(c)

    bbox = mask.getbbox()
    if not bbox or len(fg_pixels) < 30:
        return {
            "color_sig": (0.0, 0.0, 1.0),
            "color_name": "未知色",
            "color_rgb": (220, 220, 220),
            "front_score": 0.0,
        }

    # ---------- 代表色 ----------
    # 对商品主体做加权 HSV 统计：
    # 彩色像素权重大，近白高光权重低，可以更稳定识别格纹/条纹主色。
    hues = []
    sats = []
    vals = []
    weights = []
    for c in fg_pixels:
        hh, ss, vv = colorsys.rgb_to_hsv(c[0] / 255.0, c[1] / 255.0, c[2] / 255.0)
        highlight_penalty = 1.0 - clamp((vv - 0.90) / 0.10, 0.0, 1.0)
        wt = (0.14 + ss ** 1.45) * (0.35 + 0.65 * highlight_penalty)
        hues.append(hh)
        sats.append(ss)
        vals.append(vv)
        weights.append(wt)

    sw = sum(weights) or 1.0
    rep_h = circular_hue_mean(hues, weights)
    rep_s = sum(s * wt for s, wt in zip(sats, weights)) / sw
    rep_v = sum(v * wt for v, wt in zip(vals, weights)) / sw

    # 防止浅色商品被白底/高光稀释得太厉害
    rep_s = clamp(rep_s, 0.0, 1.0)
    rep_v = clamp(rep_v, 0.0, 1.0)
    rr, gg, bb = colorsys.hsv_to_rgb(rep_h, max(rep_s, 0.12), rep_v)
    rep_rgb = (rr * 255, gg * 255, bb * 255)
    cname = color_name_from_hsv(rep_h, rep_s, rep_v)

    # ---------- 正反面倾向 ----------
    # 正面通常有门襟/纽扣/口袋/开领，中心区域结构更复杂；
    # 背面通常更整洁。由于不同花型本身边缘密度差异大，所以分数只在“同色组内”做相对比较。
    gray = sample.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    eroded = mask.filter(ImageFilter.MinFilter(7))  # 去掉衣服轮廓边缘，避免把轮廓当结构细节
    ep = edges.load()
    em = eroded.load()
    gp = gray.load()

    x0, y0, x1, y1 = bbox
    bw = max(1, x1 - x0)
    bh = max(1, y1 - y0)

    def mean_edge(rx0, ry0, rx1, ry1):
        sx = int(x0 + bw * rx0)
        ex = int(x0 + bw * rx1)
        sy = int(y0 + bh * ry0)
        ey = int(y0 + bh * ry1)
        total = 0.0
        count = 0
        for yy in range(max(0, sy), min(h, ey)):
            for xx in range(max(0, sx), min(w, ex)):
                if em[xx, yy] > 0:
                    total += ep[xx, yy]
                    count += 1
        return total / count if count else 0.0

    def mean_gray(rx0, ry0, rx1, ry1):
        sx = int(x0 + bw * rx0)
        ex = int(x0 + bw * rx1)
        sy = int(y0 + bh * ry0)
        ey = int(y0 + bh * ry1)
        total = 0.0
        count = 0
        for yy in range(max(0, sy), min(h, ey)):
            for xx in range(max(0, sx), min(w, ex)):
                if mp[xx, yy] > 0:
                    total += gp[xx, yy]
                    count += 1
        return total / count if count else 0.0

    all_edge = mean_edge(0.12, 0.08, 0.88, 0.92)
    center_edge = mean_edge(0.43, 0.12, 0.57, 0.88)
    upper_edge = mean_edge(0.20, 0.12, 0.80, 0.52)

    center_gray = mean_gray(0.44, 0.15, 0.56, 0.80)
    side_gray = (
        mean_gray(0.24, 0.15, 0.38, 0.80) +
        mean_gray(0.62, 0.15, 0.76, 0.80)
    ) / 2.0
    center_dark_contrast = max(0.0, side_gray - center_gray)

    # 左右不对称：正面口袋、门襟等通常会比背面增加一些不对称细节
    diffs = []
    sy0 = int(y0 + bh * 0.15)
    sy1 = int(y0 + bh * 0.80)
    sx0 = int(x0 + bw * 0.20)
    sx1 = int(x0 + bw * 0.50)
    for yy in range(max(0, sy0), min(h, sy1), 2):
        for xx in range(max(0, sx0), min(w, sx1), 2):
            mx = int(x0 + x1 - 1 - xx)
            if 0 <= mx < w and em[xx, yy] > 0 and em[mx, yy] > 0:
                diffs.append(abs(gp[xx, yy] - gp[mx, yy]))
    asym = (sum(diffs) / len(diffs)) if diffs else 0.0

    front_score = (
        center_edge * 0.25 +
        upper_edge * 0.15 +
        all_edge * 0.10 +
        center_dark_contrast * 0.40 +
        asym * 0.10
    )

    return {
        "color_sig": (rep_h, rep_s, rep_v),
        "color_name": cname,
        "color_rgb": rep_rgb,
        "front_score": front_score,
    }


class ImageJoinerApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry(WINDOW_SIZE)
        self.root.minsize(MIN_W, MIN_H)
        self.root.configure(bg=BG)
        self.root.report_callback_exception = self._on_tk_error

        # 第 1 条路径仍作为主路径：保持原有单文件夹操作习惯，
        # 同时用于多文件夹模式下的输出目录。
        self.folder_path = tk.StringVar()
        self.folder_vars = [self.folder_path]
        self.folder_rows_container = None
        self.status_text = tk.StringVar(value="请选择文件夹或多选图片")
        self.summary_text = tk.StringVar(value="0 张图片")
        self.auto_sort_var = tk.BooleanVar(value=True)

        self.image_list = []
        self.drag_index = None
        self.drag_item = None
        self.drag_photo = None
        self.drop_preview_index = None
        self.drop_side = None
        self.drop_preview_rect = None
        self.cols = 5
        self._loading = False

        self._build_styles()
        self._build_ui()

    # =========================
    # UI
    # =========================
    def _build_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure(
            "Modern.Horizontal.TProgressbar",
            troughcolor="#E2E8F0",
            background=PRIMARY,
            bordercolor="#E2E8F0",
            lightcolor=PRIMARY,
            darkcolor=PRIMARY,
            thickness=6,
        )

        style.configure(
            "Modern.TCheckbutton",
            background=SURFACE,
            foreground=TEXT_2,
            font=(FONT, 9),
            focuscolor=SURFACE,
        )
        style.map(
            "Modern.TCheckbutton",
            background=[("active", SURFACE)],
            foreground=[("active", TEXT)],
        )

    def _make_button(self, parent, text, command, bg, hover, fg="white", width=None, font_size=9, bold=False):
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=hover,
            activeforeground=fg,
            relief="flat",
            bd=0,
            padx=15,
            pady=9,
            cursor="hand2",
            font=(FONT, font_size, "bold" if bold else "normal"),
        )
        if width:
            btn.configure(width=width)
        btn.bind("<Enter>", lambda e: btn.configure(bg=hover))
        btn.bind("<Leave>", lambda e: btn.configure(bg=bg))
        return btn

    def _build_ui(self):
        # 顶部标题
        header = tk.Frame(self.root, bg=HEADER, height=72)
        header.pack(side="top", fill="x")
        header.pack_propagate(False)

        tk.Label(
            header,
            text="送审拼接图",
            bg=HEADER,
            fg="white",
            font=(FONT, 17, "bold"),
        ).pack(anchor="w", padx=24, pady=(12, 0))

        tk.Label(
            header,
            text="同款图片可来自多个文件夹 · 智能识别颜色与正/反面 · 自动按“同色：正面 → 背面”排序",
            bg=HEADER,
            fg=HEADER_2,
            font=(FONT, 9),
        ).pack(anchor="w", padx=24, pady=(3, 0))

        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="both", expand=True, padx=20, pady=16)

        # 输入工具栏
        input_card = tk.Frame(main, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        input_card.pack(fill="x", pady=(0, 12))

        left = tk.Frame(input_card, bg=SURFACE)
        left.pack(side="left", fill="x", expand=True, padx=(16, 8), pady=13)

        title_row = tk.Frame(left, bg=SURFACE)
        title_row.pack(fill="x")
        tk.Label(
            title_row,
            text="图片来源",
            bg=SURFACE,
            fg=TEXT,
            font=(FONT, 10, "bold"),
        ).pack(side="left")
        tk.Label(
            title_row,
            text="可添加多个文件夹，全部视为同一款；输出固定保存到第 1 个文件夹",
            bg=SURFACE,
            fg=TEXT_3,
            font=(FONT, 8),
        ).pack(side="left", padx=(10, 0))

        self.folder_rows_container = tk.Frame(left, bg=SURFACE)
        self.folder_rows_container.pack(fill="x", pady=(7, 0))
        self._render_folder_rows()

        right = tk.Frame(input_card, bg=SURFACE)
        right.pack(side="right", padx=(8, 16), pady=13, anchor="n")

        self._make_button(right, "选择图片", self.select_images, SUCCESS, SUCCESS_HOVER).pack(side="left", padx=4)

        # 预览卡片
        preview_card = tk.Frame(main, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        preview_card.pack(fill="both", expand=True)

        preview_header = tk.Frame(preview_card, bg=SURFACE, height=58)
        preview_header.pack(fill="x")
        preview_header.pack_propagate(False)

        title_box = tk.Frame(preview_header, bg=SURFACE)
        title_box.pack(side="left", padx=16, pady=10)
        tk.Label(
            title_box,
            text="预览与排序",
            bg=SURFACE,
            fg=TEXT,
            font=(FONT, 11, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_box,
            text="拖拽可微调顺序 · 点击右侧“正面 / 背面 / 待确认”徽标即可手动切换",
            bg=SURFACE,
            fg=TEXT_3,
            font=(FONT, 8),
        ).pack(anchor="w", pady=(2, 0))

        tk.Label(
            preview_header,
            textvariable=self.summary_text,
            bg="#EFF6FF",
            fg=PRIMARY,
            font=(FONT, 9, "bold"),
            padx=10,
            pady=4,
        ).pack(side="right", padx=16)

        sep = tk.Frame(preview_card, height=1, bg=BORDER)
        sep.pack(fill="x")

        canvas_wrap = tk.Frame(preview_card, bg=SURFACE)
        canvas_wrap.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(
            canvas_wrap,
            bg=SURFACE_ALT,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)

        self.scrollbar = ttk.Scrollbar(canvas_wrap, orient="vertical", command=self.canvas.yview)
        self.scrollbar.pack(side="right", fill="y", padx=(0, 8), pady=10)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(1, "units"))
        self.canvas.bind("<ButtonPress-1>", self.on_mouse_press)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_release)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # 底部操作区
        bottom = tk.Frame(self.root, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        bottom.pack(side="bottom", fill="x")

        bottom_inner = tk.Frame(bottom, bg=SURFACE)
        bottom_inner.pack(fill="x", padx=20, pady=(10, 7))

        status_box = tk.Frame(bottom_inner, bg=SURFACE)
        status_box.pack(side="left", fill="x", expand=True)

        self.progress_var = tk.DoubleVar(value=0)
        self.progress = ttk.Progressbar(
            status_box,
            variable=self.progress_var,
            maximum=100,
            style="Modern.Horizontal.TProgressbar",
            length=210,
        )
        self.progress.pack(anchor="w", pady=(0, 5))

        tk.Label(
            status_box,
            textvariable=self.status_text,
            bg=SURFACE,
            fg=TEXT_2,
            font=(FONT, 9),
        ).pack(anchor="w")

        actions = tk.Frame(bottom_inner, bg=SURFACE)
        actions.pack(side="right")

        ttk.Checkbutton(
            actions,
            text="加载后自动识别排序",
            variable=self.auto_sort_var,
            style="Modern.TCheckbutton",
        ).pack(side="left", padx=(0, 10))

        self._make_button(
            actions,
            "重新智能排序",
            self.auto_group_sort,
            "#475569",
            "#334155",
        ).pack(side="left", padx=4)

        self._make_button(
            actions,
            "按当前顺序重命名",
            self.rename_by_order,
            WARNING,
            WARNING_HOVER,
        ).pack(side="left", padx=4)

        self._make_button(
            actions,
            "开始拼接",
            self.start_join,
            PRIMARY,
            PRIMARY_HOVER,
            font_size=10,
            bold=True,
        ).pack(side="left", padx=(4, 0))

        foot = tk.Label(
            bottom,
            text="输出规格：800×800 白底 · 每两张拼接一张 · 多文件夹模式下保存到第 1 个文件夹的「处理后」",
            bg=SURFACE,
            fg=TEXT_3,
            font=(FONT, 8),
        )
        foot.pack(anchor="w", padx=20, pady=(0, 8))

    # =========================
    # 事件 / 状态
    # =========================
    def _on_mousewheel(self, event):
        delta = int(-1 * (event.delta / 120))
        self.canvas.yview_scroll(delta, "units")
        return "break"

    def _on_tk_error(self, exc_type, exc_value, exc_traceback):
        err_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        messagebox.showerror("程序错误", f"发生异常：\n{err_msg}")

    def _on_canvas_configure(self, event):
        usable = max(1, event.width - CARD_GAP)
        new_cols = max(1, usable // (CARD_W + CARD_GAP))
        if new_cols != self.cols:
            self.cols = new_cols
            self.refresh_canvas()

    def set_progress(self, value):
        self.progress_var.set(clamp(value, 0, 100))

    def update_summary(self):
        total = len(self.image_list)
        if not total:
            self.summary_text.set("0 张图片")
            return
        colors = []
        front = 0
        back = 0
        unsure = 0
        for item in self.image_list:
            gid = item.get("group_id")
            if gid is not None and gid not in colors:
                colors.append(gid)
            side = item.get("side", "待确认")
            if side == "正面":
                front += 1
            elif side == "背面":
                back += 1
            else:
                unsure += 1
        extra = f" · 待确认 {unsure}" if unsure else ""
        folder_count = max(1, len(self._collect_selected_folders()))
        folder_text = f" · {folder_count} 个文件夹" if folder_count > 1 else ""
        self.summary_text.set(
            f"{total} 张{folder_text} · {len(colors)} 色 · 正面 {front} · 背面 {back}{extra}"
        )

    # =========================
    # 加载图片
    # =========================
    def _render_folder_rows(self):
        """重绘文件夹路径行。第 1 行固定保留，额外行可删除。"""
        if self.folder_rows_container is None:
            return

        for child in self.folder_rows_container.winfo_children():
            child.destroy()

        for idx, var in enumerate(self.folder_vars):
            row = tk.Frame(self.folder_rows_container, bg=SURFACE)
            row.pack(fill="x", pady=(0 if idx == 0 else 6, 0))

            entry_wrap = tk.Frame(row, bg="#F8FAFC", highlightthickness=1, highlightbackground=BORDER)
            entry_wrap.pack(side="left", fill="x", expand=True)
            entry = tk.Entry(
                entry_wrap,
                textvariable=var,
                relief="flat",
                bd=0,
                bg="#F8FAFC",
                fg=TEXT_2,
                insertbackground=PRIMARY,
                font=(FONT, 10),
            )
            entry.pack(fill="x", padx=10, pady=8)
            entry.bind("<Return>", lambda e: self._load_selected_folders())

            self._make_button(
                row,
                "选择文件夹",
                lambda i=idx: self._choose_folder_for_row(i),
                PRIMARY,
                PRIMARY_HOVER,
                width=9,
            ).pack(side="left", padx=(8, 4))

            if idx == 0:
                add_btn = self._make_button(
                    row,
                    "+",
                    self._add_folder_row,
                    "#E2E8F0",
                    "#CBD5E1",
                    fg=TEXT,
                    width=2,
                    font_size=12,
                    bold=True,
                )
                add_btn.pack(side="left")
            else:
                remove_btn = self._make_button(
                    row,
                    "−",
                    lambda i=idx: self._remove_folder_row(i),
                    "#F1F5F9",
                    "#E2E8F0",
                    fg=TEXT_2,
                    width=2,
                    font_size=12,
                    bold=True,
                )
                remove_btn.pack(side="left")

    def _add_folder_row(self):
        self.folder_vars.append(tk.StringVar())
        self._render_folder_rows()

    def _remove_folder_row(self, index):
        if index <= 0 or index >= len(self.folder_vars):
            return
        self.folder_vars.pop(index)
        self._render_folder_rows()
        self._load_selected_folders()

    def _choose_folder_for_row(self, index):
        path = filedialog.askdirectory(title="选择同一款商品的图片文件夹")
        if not path:
            return
        self.folder_vars[index].set(path)
        self._load_selected_folders()

    def _collect_selected_folders(self):
        """按界面顺序返回有效且去重的文件夹路径。"""
        result = []
        seen = set()
        for var in self.folder_vars:
            raw = var.get().strip()
            if not raw:
                continue
            path = os.path.abspath(os.path.normpath(raw))
            key = os.path.normcase(path)
            if key in seen:
                continue
            if os.path.isdir(path):
                seen.add(key)
                result.append(path)
        return result

    def _load_selected_folders(self):
        folders = self._collect_selected_folders()
        if not folders:
            self.image_list.clear()
            self.refresh_canvas()
            self.update_summary()
            self.status_text.set("请选择至少一个有效文件夹")
            self.set_progress(0)
            return

        # 第一个文件夹始终是主目录/输出目录。
        self.folder_path.set(folders[0])

        img_extensions = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff", ".tif")
        files = []
        for folder_index, folder in enumerate(folders):
            folder_files = [
                os.path.join(folder, f)
                for f in os.listdir(folder)
                if f.lower().endswith(img_extensions)
            ]
            folder_files.sort(key=lambda p: natural_sort_key(os.path.basename(p)))
            for p in folder_files:
                files.append((p, folder_index))

        self._start_load(files)

    def select_images(self):
        filetypes = [("图片文件", "*.jpg *.jpeg *.png *.bmp *.webp *.tiff *.tif")]
        file_paths = filedialog.askopenfilenames(title="选择图片（可多选）", filetypes=filetypes)
        if not file_paths:
            return
        first_dir = os.path.dirname(file_paths[0])

        # 直接选图片仍保持原有单路径体验，同时清掉额外文件夹行，避免来源混淆。
        self.folder_path.set(first_dir)
        self.folder_vars = [self.folder_path]
        self._render_folder_rows()
        self._start_load([(p, 0) for p in file_paths])

    def select_folder(self):
        """兼容旧调用：等同于选择第 1 条路径。"""
        self._choose_folder_for_row(0)

    def _start_load(self, file_paths):
        if self._loading:
            messagebox.showinfo("提示", "正在加载图片，请稍候。")
            return
        if not file_paths:
            self.image_list.clear()
            self.refresh_canvas()
            self.status_text.set("没有找到可用图片")
            self.set_progress(0)
            return

        self._loading = True
        self.status_text.set(f"正在读取并分析 {len(file_paths)} 张图片...")
        self.set_progress(1)
        threading.Thread(target=self._load_worker, args=(file_paths,), daemon=True).start()

    def _load_worker(self, file_paths):
        prepared = []
        total = len(file_paths)
        for i, source in enumerate(file_paths, 1):
            # source 既支持旧格式的完整路径，也支持 (路径, 文件夹序号)。
            if isinstance(source, (tuple, list)):
                filepath = source[0]
                source_folder_index = int(source[1]) if len(source) > 1 else 0
            else:
                filepath = source
                source_folder_index = 0
            try:
                rgba = open_rgba(filepath)
                analysis_rgb = composite_for_analysis(rgba)
                info = analyze_image(analysis_rgb)

                thumb = rgba.copy()
                thumb.thumbnail((THUMB_SIZE, THUMB_SIZE), RESAMPLE_FILTER)

                prepared.append({
                    "path": filepath,
                    "name": os.path.basename(filepath),
                    "pil_thumb": thumb,
                    "photo": None,
                    "color_sig": info["color_sig"],
                    "color_name": info["color_name"],
                    "color_rgb": info["color_rgb"],
                    "front_score": info["front_score"],
                    "side": filename_side_hint(os.path.basename(filepath)) or "待确认",
                    "group_id": None,
                    "soft_pair": False,
                    "source_index": i - 1,
                    "source_folder_index": source_folder_index,
                    "source_folder": os.path.dirname(filepath),
                    "source_folder_name": os.path.basename(os.path.dirname(filepath)) or os.path.dirname(filepath),
                })
            except Exception as e:
                print(f"加载 {filepath} 失败：{e}")

            progress = (i / total) * 92.0
            self.root.after(0, lambda p=progress, c=i, t=total: self._loading_progress(p, c, t))

        self.root.after(0, lambda: self._finish_loading(prepared))

    def _loading_progress(self, progress, current, total):
        self.set_progress(progress)
        self.status_text.set(f"正在识别颜色与正反面... {current}/{total}")

    def _finish_loading(self, prepared):
        try:
            for item in prepared:
                item["photo"] = ImageTk.PhotoImage(item["pil_thumb"])
                item.pop("pil_thumb", None)
            self.image_list = prepared

            if self.auto_sort_var.get() and self.image_list:
                self.auto_group_sort(show_message=False)
            else:
                self.refresh_canvas()
                self.update_summary()

            self.set_progress(100)
            self.status_text.set(f"已加载 {len(self.image_list)} 张图片，可拖拽微调顺序")
            self.root.after(700, lambda: self.set_progress(0))
        finally:
            self._loading = False

    # =========================
    # 智能分组与排序
    # =========================
    def _build_color_groups(self):
        """
        第一步按较严格阈值做颜色聚类；第二步只对“双方都待确认”的单张组
        做近似颜色配对。这样不会大幅改变现有识别，同时能把正/背面因光线
        导致颜色略有差异的两张图先放到一起。
        """
        groups = []
        threshold = STRICT_COLOR_GROUP_THRESHOLD

        for item in self.image_list:
            item["soft_pair"] = False
            sig = item.get("color_sig", (0.0, 0.0, 1.0))
            best_idx = None
            best_dist = 999.0

            for gi, group in enumerate(groups):
                dist = color_distance(sig, group["rep_sig"])
                # 色名不同只做轻微惩罚；严格分组阶段仍保持原来的保守策略。
                if item.get("color_name") != group.get("color_name"):
                    dist += 0.08
                if dist < best_dist:
                    best_dist = dist
                    best_idx = gi

            if best_idx is None or best_dist > threshold:
                groups.append({
                    "items": [item],
                    "rep_sig": sig,
                    "color_name": item.get("color_name", "未知色"),
                    "first_index": item.get("source_index", len(groups)),
                    "soft_pair": False,
                })
            else:
                group = groups[best_idx]
                group["items"].append(item)
                group["first_index"] = min(
                    group.get("first_index", 10**9),
                    item.get("source_index", 10**9),
                )
                self._refresh_group_rep(group)

        groups = self._merge_nearby_pending_singletons(groups)
        groups.sort(key=lambda g: g.get("first_index", 10**9))
        return groups

    def _refresh_group_rep(self, group):
        items = group.get("items", [])
        if not items:
            return
        hs = [it["color_sig"][0] for it in items]
        ss = [it["color_sig"][1] for it in items]
        vv = [it["color_sig"][2] for it in items]
        weights = [max(0.1, sat) for sat in ss]
        group["rep_sig"] = (
            circular_hue_mean(hs, weights),
            sum(ss) / len(ss),
            sum(vv) / len(vv),
        )
        h, sat, val = group["rep_sig"]
        group["color_name"] = color_name_from_hsv(h, sat, val)

    def _merge_nearby_pending_singletons(self, groups):
        """
        兜底逻辑：如果两张图都没有正/背面明确提示，且严格颜色聚类把它们
        分成了两个单张组，但代表色非常接近，则把它们作为“疑似同色正反面”
        放在一起。两张都保持“待确认”，不强行猜正反面。
        """
        if len(groups) < 2:
            return groups

        candidates = []
        for gi, group in enumerate(groups):
            items = group.get("items", [])
            if len(items) != 1:
                continue
            item = items[0]
            if filename_side_hint(item.get("name", "")) is not None:
                continue
            if item.get("side", "待确认") != "待确认":
                continue
            candidates.append(gi)

        # 先收集所有可接受的配对，再按颜色距离从近到远贪心匹配。
        pair_options = []
        for pos, gi in enumerate(candidates):
            for gj in candidates[pos + 1:]:
                a = groups[gi]
                b = groups[gj]
                if colors_are_close_enough_for_soft_pair(a["rep_sig"], b["rep_sig"]):
                    d = color_distance(a["rep_sig"], b["rep_sig"])
                    pair_options.append((d, gi, gj))

        pair_options.sort(key=lambda x: x[0])
        used = set()
        merges = []
        for dist, gi, gj in pair_options:
            if gi in used or gj in used:
                continue
            used.add(gi)
            used.add(gj)
            merges.append((gi, gj, dist))

        if not merges:
            return groups

        absorbed = set()
        for gi, gj, _ in merges:
            if gi in absorbed or gj in absorbed:
                continue
            a = groups[gi]
            b = groups[gj]
            a["items"].extend(b["items"])
            a["first_index"] = min(a.get("first_index", 10**9), b.get("first_index", 10**9))
            a["soft_pair"] = True
            for item in a["items"]:
                item["soft_pair"] = True
                item["side"] = "待确认"
            self._refresh_group_rep(a)
            absorbed.add(gj)

        return [g for idx, g in enumerate(groups) if idx not in absorbed]

    def _assign_sides_in_group(self, items, soft_pair=False):
        """
        同色组内分配正/反面。
        - 文件名提示优先；
        - “近似颜色兜底配对”的两张图不强猜，统一待确认；
        - 普通两张同色图只有分数差足够明显时才自动判正/反面。
        """
        if not items:
            return

        hinted_front = [it for it in items if it.get("side") == "正面"]
        hinted_back = [it for it in items if it.get("side") == "背面"]
        unknown = [it for it in items if it.get("side") not in ("正面", "背面")]

        if len(items) == 1:
            if not hinted_front and not hinted_back:
                items[0]["side"] = "待确认"
            return

        # 颜色只是“接近”而不是严格同色时：用户更需要先把两张图放一起，
        # 再由人工点徽标确认，而不是让程序硬猜。
        if soft_pair and len(items) == 2 and not hinted_front and not hinted_back:
            for item in items:
                item["side"] = "待确认"
            return

        # 最常见场景：同色正背面各 1 张。
        if len(items) == 2:
            a, b = items
            if a.get("side") == "正面" and b.get("side") == "待确认":
                b["side"] = "背面"
                return
            if a.get("side") == "背面" and b.get("side") == "待确认":
                b["side"] = "正面"
                return
            if b.get("side") == "正面" and a.get("side") == "待确认":
                a["side"] = "背面"
                return
            if b.get("side") == "背面" and a.get("side") == "待确认":
                a["side"] = "正面"
                return
            if not hinted_front and not hinted_back:
                ordered = sorted(items, key=lambda x: x.get("front_score", 0.0), reverse=True)
                gap = abs(ordered[0].get("front_score", 0.0) - ordered[1].get("front_score", 0.0))
                if gap >= SIDE_PAIR_MIN_GAP:
                    ordered[0]["side"] = "正面"
                    ordered[1]["side"] = "背面"
                else:
                    # 两张看起来太像，保守显示待确认，避免错判后自动拆开。
                    ordered[0]["side"] = "待确认"
                    ordered[1]["side"] = "待确认"
                return

        # 多张同色：保持原有策略，文件名提示优先，其余按分数排序。
        unknown.sort(key=lambda x: x.get("front_score", 0.0), reverse=True)
        remain_front = max(0, math.ceil(len(items) / 2) - len(hinted_front))
        for idx, item in enumerate(unknown):
            item["side"] = "正面" if idx < remain_front else "背面"

    def auto_group_sort(self, show_message=True):
        if not self.image_list:
            if show_message:
                messagebox.showwarning("提示", "还没有加载图片。")
            return

        # “重新智能排序”视为重新判断：先清除非文件名明确指定的人工/旧识别状态，
        # 再进行颜色分组，这样近似颜色兜底配对每次都能稳定生效。
        for item in self.image_list:
            hint = filename_side_hint(item.get("name", ""))
            item["side"] = hint or "待确认"
            item["soft_pair"] = False

        groups = self._build_color_groups()
        sorted_items = []

        for gid, group in enumerate(groups, 1):
            items = group["items"]
            soft_pair = bool(group.get("soft_pair"))
            self._assign_sides_in_group(items, soft_pair=soft_pair)

            # 用组代表色统一色名，界面看起来更稳定
            h, s, v = group["rep_sig"]
            group_name = color_name_from_hsv(h, s, v)
            rr, gg, bb = colorsys.hsv_to_rgb(h, max(s, 0.12), v)
            group_rgb = (rr * 255, gg * 255, bb * 255)

            for item in items:
                item["group_id"] = gid
                item["color_name"] = group_name
                item["color_rgb"] = group_rgb
                item["soft_pair"] = soft_pair

            side_rank = {"正面": 0, "待确认": 1, "背面": 2}
            items.sort(
                key=lambda it: (
                    side_rank.get(it.get("side", "待确认"), 1),
                    -it.get("front_score", 0.0),
                    natural_sort_key(it.get("name", "")),
                )
            )
            sorted_items.extend(items)

        self.image_list = sorted_items
        self.refresh_canvas()
        self.update_summary()

        if show_message:
            soft_count = sum(1 for g in groups if g.get("soft_pair"))
            if soft_count:
                self.status_text.set(
                    f"智能排序完成：{len(groups)} 个颜色组 · {soft_count} 组近似颜色已放在一起并标记待确认"
                )
            else:
                self.status_text.set(f"智能排序完成：识别到 {len(groups)} 个颜色组，已按每色正面→背面排列")

    def _sort_using_existing_groups(self):
        """手动切换正反面后，仅按现有颜色组重新排，不重新跑识别。"""
        groups = []
        seen = {}
        for item in self.image_list:
            gid = item.get("group_id")
            if gid not in seen:
                seen[gid] = len(groups)
                groups.append([])
            groups[seen[gid]].append(item)

        side_rank = {"正面": 0, "待确认": 1, "背面": 2}
        result = []
        for items in groups:
            items.sort(key=lambda it: (side_rank.get(it.get("side"), 1), -it.get("front_score", 0.0)))
            result.extend(items)
        self.image_list = result

    # =========================
    # 画布与缩略图
    # =========================
    def refresh_canvas(self):
        self.canvas.delete("all")
        self.drop_preview_index = None
        self.drop_side = None
        self.drop_preview_rect = None

        total = len(self.image_list)
        if total == 0:
            cw = max(600, self.canvas.winfo_width())
            self.canvas.create_text(
                cw // 2,
                150,
                text="暂无图片",
                fill=TEXT_3,
                font=(FONT, 13, "bold"),
            )
            self.canvas.create_text(
                cw // 2,
                180,
                text="选择文件夹后会自动识别颜色与正反面",
                fill="#CBD5E1",
                font=(FONT, 9),
            )
            self.canvas.config(scrollregion=(0, 0, 0, 0))
            return

        width = max(1, self.canvas.winfo_width())
        self.cols = max(1, (width - CARD_GAP) // (CARD_W + CARD_GAP))
        rows = (total + self.cols - 1) // self.cols
        canvas_height = rows * (CARD_H + CARD_GAP) + CARD_GAP
        self.canvas.config(scrollregion=(0, 0, width, canvas_height))

        for idx, item in enumerate(self.image_list):
            x, y = self.get_item_position(idx)
            self.draw_thumbnail(idx, item, x, y)

    def get_item_position(self, idx):
        row = idx // self.cols
        col = idx % self.cols
        x = col * (CARD_W + CARD_GAP) + CARD_GAP
        y = row * (CARD_H + CARD_GAP) + CARD_GAP
        return x, y

    def draw_thumbnail(self, idx, item, x, y):
        # 卡片
        self.canvas.create_rectangle(
            x,
            y,
            x + CARD_W,
            y + CARD_H,
            fill=SURFACE,
            outline=BORDER,
            width=1,
            tags=(f"card_{idx}", f"item_{idx}"),
        )

        # 图片区域
        img_box_x = x + 10
        img_box_y = y + 10
        img_box_w = CARD_W - 20
        img_box_h = THUMB_SIZE
        self.canvas.create_rectangle(
            img_box_x,
            img_box_y,
            img_box_x + img_box_w,
            img_box_y + img_box_h,
            fill="#F8FAFC",
            outline="#EEF2F7",
            width=1,
            tags=f"item_{idx}",
        )

        photo = item["photo"]
        img_w, img_h = photo.width(), photo.height()
        img_x = img_box_x + (img_box_w - img_w) // 2
        img_y = img_box_y + (img_box_h - img_h) // 2
        self.canvas.create_image(
            img_x + img_w // 2,
            img_y + img_h // 2,
            image=photo,
            tags=(f"img_{idx}", f"item_{idx}"),
        )

        # 顺序角标
        self.canvas.create_oval(
            x + 16,
            y + 16,
            x + 42,
            y + 42,
            fill=HEADER,
            outline="",
            tags=f"item_{idx}",
        )
        self.canvas.create_text(
            x + 29,
            y + 29,
            text=str(idx + 1),
            fill="white",
            font=(FONT, 8, "bold"),
            tags=f"item_{idx}",
        )

        # 色块 + 色名
        color_hex = rgb_to_hex(item.get("color_rgb", (210, 210, 210)))
        base_y = y + 170
        self.canvas.create_oval(
            x + 12,
            base_y + 3,
            x + 22,
            base_y + 13,
            fill=color_hex,
            outline="#CBD5E1",
            width=1,
            tags=f"item_{idx}",
        )
        self.canvas.create_text(
            x + 28,
            base_y + 8,
            anchor="w",
            text=item.get("color_name", "未知色"),
            fill=TEXT_2,
            font=(FONT, 8),
            tags=f"item_{idx}",
        )

        # 正反面徽标
        side = item.get("side", "待确认")
        if side == "正面":
            badge_bg, badge_fg = "#DBEAFE", "#1D4ED8"
        elif side == "背面":
            badge_bg, badge_fg = "#ECFDF5", "#047857"
        else:
            badge_bg, badge_fg = "#FEF3C7", "#B45309"

        badge_x1 = x + CARD_W - 58
        badge_y1 = base_y
        badge_x2 = x + CARD_W - 10
        badge_y2 = base_y + 18
        self.canvas.create_rectangle(
            badge_x1,
            badge_y1,
            badge_x2,
            badge_y2,
            fill=badge_bg,
            outline="",
            tags=(f"item_{idx}", f"side_badge_{idx}"),
        )
        self.canvas.create_text(
            (badge_x1 + badge_x2) // 2,
            (badge_y1 + badge_y2) // 2,
            text=side,
            fill=badge_fg,
            font=(FONT, 8, "bold"),
            tags=(f"item_{idx}", f"side_badge_{idx}"),
        )

        # 文件名。多文件夹模式额外显示来源文件夹，避免同名文件在界面里混淆。
        name = item["name"]
        folder_count = len(self._collect_selected_folders())
        if folder_count > 1:
            source_no = item.get("source_folder_index", 0) + 1
            display_name = f"F{source_no} · {item.get('source_folder_name', '')} / {name}"
        else:
            display_name = name
        if len(display_name) > 25:
            display_name = display_name[:22] + "..."
        self.canvas.create_text(
            x + 12,
            y + 198,
            anchor="w",
            text=display_name,
            fill=TEXT_3,
            font=(FONT, 8),
            tags=f"item_{idx}",
        )

    # =========================
    # 拖拽排序
    # =========================
    def _get_badge_index(self, x, y):
        """只有点击正/反面徽标区域才触发手动切换，避免与拖拽冲突。"""
        for idx in range(len(self.image_list)):
            ix, iy = self.get_item_position(idx)
            base_y = iy + 170
            badge_x1 = ix + CARD_W - 58
            badge_y1 = base_y
            badge_x2 = ix + CARD_W - 10
            badge_y2 = base_y + 18
            # 轻微扩大点击热区，但不会覆盖到图片拖拽区。
            if badge_x1 - 3 <= x <= badge_x2 + 3 and badge_y1 - 3 <= y <= badge_y2 + 3:
                return idx
        return None

    def _toggle_side_badge(self, idx):
        item = self.image_list[idx]
        current = item.get("side", "待确认")
        # 待确认 -> 正面 -> 背面 -> 正面...
        item["side"] = "背面" if current == "正面" else "正面"
        item["soft_pair"] = False

        self._sort_using_existing_groups()
        self.refresh_canvas()
        self.update_summary()
        self.status_text.set(f"已手动标记：{item['name']} → {item['side']}")

    def on_mouse_press(self, event):
        if not self.image_list:
            return
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)

        # 徽标是独立按钮区：单击切换，且不进入拖拽状态。
        badge_idx = self._get_badge_index(x, y)
        if badge_idx is not None:
            self._toggle_side_badge(badge_idx)
            return "break"

        idx, _ = self.get_target_and_side(x, y)
        if idx is None:
            return

        self.drag_index = idx
        self.drag_photo = self.image_list[idx]["photo"]
        self.canvas.itemconfig(f"card_{idx}", outline=PRIMARY, width=2)
        self.canvas.itemconfig(f"img_{idx}", state="hidden")
        self.drag_item = self.canvas.create_image(x, y, image=self.drag_photo)
        self.canvas.tag_raise(self.drag_item)

    def on_mouse_drag(self, event):
        if self.drag_item is None or self.drag_index is None:
            return
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        self.canvas.coords(self.drag_item, x, y)

        target_idx, side = self.get_target_and_side(x, y)
        self.update_drop_preview(target_idx, side)

        canvas_h = self.canvas.winfo_height()
        if event.y > canvas_h - 45:
            self.canvas.yview_scroll(3, "units")
        elif event.y < 45:
            self.canvas.yview_scroll(-3, "units")

    def on_mouse_release(self, event):
        if self.drag_index is None:
            return

        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        target_idx, side = self.get_target_and_side(x, y)

        if self.drag_item is not None:
            self.canvas.delete(self.drag_item)
        self.drag_item = None
        self.drag_photo = None
        self.clear_drop_preview()

        if target_idx is not None and target_idx != self.drag_index:
            item = self.image_list.pop(self.drag_index)
            insert_pos = target_idx if side == "left" else target_idx + 1
            if self.drag_index < target_idx:
                insert_pos -= 1
            self.image_list.insert(insert_pos, item)
            self.status_text.set(f"顺序已手动调整，共 {len(self.image_list)} 张图片")

        self.refresh_canvas()
        self.drag_index = None

    def get_target_and_side(self, x, y):
        total = len(self.image_list)
        for idx in range(total):
            ix, iy = self.get_item_position(idx)
            card_left = ix
            card_right = ix + CARD_W
            card_top = iy
            card_bottom = iy + CARD_H
            if card_left <= x <= card_right and card_top <= y <= card_bottom:
                mid_x = (card_left + card_right) / 2
                return idx, ("left" if x < mid_x else "right")
        return None, None

    def update_drop_preview(self, target_idx, side):
        if target_idx == self.drop_preview_index and side == self.drop_side:
            return
        self.clear_drop_preview()
        if target_idx is None:
            return

        self.drop_preview_index = target_idx
        self.drop_side = side
        x, y = self.get_item_position(target_idx)
        if side == "left":
            px = x - 5
        else:
            px = x + CARD_W + 5

        self.drop_preview_rect = self.canvas.create_line(
            px,
            y - 2,
            px,
            y + CARD_H + 2,
            fill=PRIMARY,
            width=4,
        )
        self.canvas.tag_raise(self.drop_preview_rect)

    def clear_drop_preview(self):
        if self.drop_preview_rect is not None:
            self.canvas.delete(self.drop_preview_rect)
        self.drop_preview_rect = None
        self.drop_preview_index = None
        self.drop_side = None

    # =========================
    # 重命名
    # =========================
    def rename_by_order(self):
        total_img = len(self.image_list)
        if total_img < 1:
            messagebox.showwarning("提示", "没有图片可重命名！")
            return

        resp = messagebox.askyesno(
            "确认重命名",
            f"将按照当前界面顺序修改磁盘原始文件名。\n"
            f"共 {total_img} 个文件，格式示例：001_原文件名.jpg\n\n"
            f"建议先备份原图。是否继续？",
        )
        if not resp:
            return

        threading.Thread(target=self._do_rename_work, daemon=True).start()

    def _do_rename_work(self):
        """
        按当前全局顺序重命名，但每张图始终留在自己的原文件夹。
        多个文件夹里即使存在完全相同的文件名，也不会互相覆盖或被移动到一起。
        """
        try:
            total_img = len(self.image_list)
            temp_records = []

            # 第一步：在“各自原目录”内改为唯一临时名。
            # 临时名带全局序号，因此即便不同目录有同名文件也互不影响。
            for idx, item in enumerate(self.image_list):
                src = item["path"]
                dirname = os.path.dirname(src)
                base, ext = os.path.splitext(os.path.basename(src))
                clean_base = re.sub(r"^\d{3}_", "", base)

                temp_name = f".__joiner_tmp_{idx + 1:04d}__{clean_base}{ext}"
                temp_full = os.path.join(dirname, temp_name)
                suffix = 1
                while os.path.exists(temp_full):
                    temp_name = f".__joiner_tmp_{idx + 1:04d}_{suffix}__{clean_base}{ext}"
                    temp_full = os.path.join(dirname, temp_name)
                    suffix += 1

                shutil.move(src, temp_full)
                temp_records.append((item, temp_full, dirname, clean_base, ext))

            success = 0
            # 第二步：仍在原目录内落正式文件名。
            for order_idx, (item, temp_path, dirname, old_base, ext) in enumerate(temp_records):
                new_filename = f"{order_idx + 1:03d}_{old_base}{ext}"
                new_full = os.path.join(dirname, new_filename)

                # 极端情况下目录中已有同名目标，追加短序号，绝不覆盖已有文件。
                if os.path.exists(new_full):
                    stem = os.path.splitext(new_filename)[0]
                    n = 2
                    candidate = os.path.join(dirname, f"{stem}_{n}{ext}")
                    while os.path.exists(candidate):
                        n += 1
                        candidate = os.path.join(dirname, f"{stem}_{n}{ext}")
                    new_full = candidate

                shutil.move(temp_path, new_full)
                item["path"] = new_full
                item["name"] = os.path.basename(new_full)
                item["source_folder"] = dirname
                item["source_folder_name"] = os.path.basename(dirname) or dirname
                success += 1
                self.root.after(
                    0,
                    lambda s=success, t=total_img: self.status_text.set(f"正在重命名... {s}/{t}"),
                )

            self.root.after(0, lambda: self._finish_rename_multi(success))
        except Exception as err:
            traceback.print_exc()
            self.root.after(
                0,
                lambda: messagebox.showerror("重命名出错", f"部分文件重命名失败！\n{str(err)}"),
            )

    def _finish_rename_multi(self, success):
        messagebox.showinfo("完成", f"重命名完成！\n成功 {success} 个文件。\n多文件夹图片均保留在各自原目录。")
        # 重新从当前所有路径读取，确保界面与磁盘一致。
        self._load_selected_folders()

    # =========================
    # 拼接
    # =========================
    def start_join(self):
        if len(self.image_list) < 2:
            messagebox.showwarning("提示", "图片数量不足 2 张，无法拼接。")
            return
        folders = self._collect_selected_folders()
        folder = folders[0] if folders else self.folder_path.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("提示", "请先选择有效的第 1 个文件夹。")
            return

        if len(self.image_list) % 2 != 0:
            go = messagebox.askyesno(
                "存在单张图片",
                "当前图片数量为奇数，最后 1 张不会参与拼接。\n是否继续？",
            )
            if not go:
                return

        self.set_progress(1)
        threading.Thread(target=self.process_images, args=(folder,), daemon=True).start()

    def process_images(self, folder):
        output_dir = os.path.join(folder, "处理后")
        os.makedirs(output_dir, exist_ok=True)

        canvas_size = (800, 800)
        half_width = 400
        success_count = 0
        total_groups = len(self.image_list) // 2

        for group_index, i in enumerate(range(0, len(self.image_list) - 1, 2), 1):
            try:
                item1 = self.image_list[i]
                item2 = self.image_list[i + 1]

                img1 = open_rgba(item1["path"])
                img2 = open_rgba(item2["path"])

                canvas = Image.new("RGB", canvas_size, "white")

                img1_resized = self.resize_fit(img1, half_width, 800)
                x1 = (half_width - img1_resized.width) // 2
                y1 = (800 - img1_resized.height) // 2
                canvas.paste(img1_resized, (x1, y1), img1_resized)

                img2_resized = self.resize_fit(img2, half_width, 800)
                x2 = half_width + (half_width - img2_resized.width) // 2
                y2 = (800 - img2_resized.height) // 2
                canvas.paste(img2_resized, (x2, y2), img2_resized)

                # 保持原程序的输出命名规则，不改变现有工作习惯
                out_name = f"拼接_{i + 1}_{i + 2}.png"
                canvas.save(os.path.join(output_dir, out_name), "PNG")

                success_count += 1
                progress = (group_index / max(1, total_groups)) * 100.0
                self.root.after(0, lambda p=progress: self.set_progress(p))
                self.root.after(
                    0,
                    lambda c=success_count, t=total_groups: self.status_text.set(f"正在拼接... {c}/{t} 组"),
                )
            except Exception as e:
                print(f"处理第 {i + 1}-{i + 2} 张时出错：{e}")

        self.root.after(0, lambda: self.status_text.set(f"完成！共生成 {success_count} 张拼接图"))
        self.root.after(0, lambda: self.set_progress(100))
        self.root.after(
            0,
            lambda: messagebox.showinfo(
                "完成",
                f"拼接完成！\n共生成 {success_count} 张图片\n保存位置（第 1 个文件夹）：{output_dir}",
            ),
        )
        self.root.after(900, lambda: self.set_progress(0))

    def resize_fit(self, img, max_w, max_h):
        w, h = img.size
        ratio = min(max_w / w, max_h / h)
        return img.resize((max(1, int(w * ratio)), max(1, int(h * ratio))), RESAMPLE_FILTER)


def main():
    try:
        root = tk.Tk()
        ImageJoinerApp(root)
        root.mainloop()
    except Exception:
        traceback.print_exc()
        input("\n程序异常，按回车关闭窗口...")


if __name__ == "__main__":
    main()
