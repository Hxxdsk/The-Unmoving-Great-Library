"""Offline desktop-pet sprites and motion; independent of all business workflows."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import math
import sys
import time
from PIL import Image, ImageOps

ASSETS = Path(__file__).resolve().parent.parent / "assets"
SIZE = (128, 144)
FRAME_MS = 33

# Atlas cells have generous gutters. Peeking artwork has explicit bounds because
# the old equal-height grid included fragments from the next row.
ART = {
    "idle": ("pet-idle.png", 0, 4, 3),
    "half": ("pet-idle.png", 1, 4, 3),
    "blink": ("pet-idle.png", 2, 4, 3),
    "open": ("pet-idle.png", 3, 4, 3),
    "greet": ("pet-idle.png", 4, 4, 3),
    "wave-left": ("pet-idle.png", 5, 4, 3),
    "wave-right": ("pet-idle.png", 6, 4, 3),
    "lower-hand": ("pet-idle.png", 7, 4, 3),
    "drowsy": ("pet-idle.png", 8, 4, 3),
    "sleep": ("pet-idle.png", 9, 4, 3),
    "wake": ("pet-idle.png", 10, 4, 3),
    "stretch": ("pet-idle.png", 11, 4, 3),
    "lift-start": ("pet-lift.png", 0, 4, 2),
    "lift": ("pet-lift.png", 1, 4, 2),
    "lift-left": ("pet-lift.png", 2, 4, 2),
    "lift-right": ("pet-lift.png", 3, 4, 2),
    "lift-blink": ("pet-lift.png", 4, 4, 2),
    "lower": ("pet-lift.png", 5, 4, 2),
    "land": ("pet-lift.png", 6, 4, 2),
    "settle": ("pet-lift.png", 7, 4, 2),
}
PEEK = {
    "peek": (0, 0.035, 0.50, 0.438),
    "peek-blink": (0.50, 0.035, 1, 0.438),
    "side": (0, 0.46, 0.50, 0.99),
    "side-blink": (0.50, 0.46, 1, 0.99),
}


def _small_frame(source, name, rect, filename):
    w, h = source.size
    image = source.crop(
        tuple(round(v * (w if i % 2 == 0 else h)) for i, v in enumerate(rect))
    )
    if name not in PEEK:
        bottom_gutter = 7 if filename == "pet-lift.png" else 2
        image = image.crop((0, 0, image.width, max(1, image.height - bottom_gutter)))
    box = image.getbbox()
    if box:
        image = image.crop(box)
    maximum = (104, 112)
    if name in PEEK:
        maximum = (100, 76) if name.startswith("peek") else (78, 108)
    elif name == "sleep":
        maximum = (104, 86)
    elif name.startswith("lift") or name == "lower":
        maximum = (102, 118)
    image.thumbnail(maximum, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", SIZE)
    canvas.alpha_composite(
        image, ((SIZE[0] - image.width) // 2, SIZE[1] - 10 - image.height)
    )
    return canvas


@lru_cache(maxsize=3)
def _atlas_frames(filename):
    """Decode each atlas once and retain only the small extracted frames."""
    path = ASSETS / filename
    with Image.open(path if path.exists() else ASSETS / "guide-patchouli.png") as file:
        source = file.convert("RGBA")
        frames = {}
        entries = (
            PEEK.items()
            if filename == "guide-peek.png"
            else (
                (
                    name,
                    (
                        (index % cols) / cols,
                        (index // cols) / rows,
                        (index % cols + 1) / cols,
                        (index // cols + 1) / rows,
                    ),
                )
                for name, (f, index, cols, rows) in ART.items()
                if f == filename
            )
        )
        for name, rect in entries:
            frames[name] = _small_frame(
                source, name, rect if path.exists() else (0, 0, 1, 1), filename
            )
        return frames


@lru_cache(maxsize=32)
def sprite(name="idle"):
    """One padded frame with explicit atlas bounds and no neighboring sprites."""
    flip = name.startswith("right")
    key = ("side-blink" if name.endswith("blink") else "side") if flip else name
    filename = "guide-peek.png" if key in PEEK else ART[key][0]
    image = _atlas_frames(filename)[key]
    return ImageOps.mirror(image) if flip else image


def head_anchor(pose):
    # Align faces during head-only/full-body transitions instead of fading two
    # heads at different heights (the body emerges while being picked up).
    if pose.startswith("peek"):
        return (64, 110)
    if pose.startswith(("side", "right")):
        return (64, 78)
    if pose == "sleep":
        return (64, 99)
    if pose == "wake":
        return (64, 88)
    if pose.startswith("lift") or pose == "lower":
        return (64, 76)
    return (64, 69)


def translated(image, dx, dy):
    canvas = Image.new("RGBA", SIZE)
    canvas.alpha_composite(image, (round(dx), round(dy)))
    return canvas


@dataclass(frozen=True)
class MotionFrame:
    image: Image.Image
    pose: str
    active: bool


class PetAnimator:
    """Time-based motion, shared by Qt and Tk; no task state or network access."""

    def __init__(self, now=None):
        self.mode = "idle"
        self.started = self.last_touch = time.monotonic() if now is None else now
        self.side = None
        self.velocity = 0.0
        self.previous_pose = "idle"
        self.source = sprite("idle")
        self.target = self.source
        self.changed = self.started - 1
        self.last_image = self.source
        self.display_head = self.source_head = self.target_head = head_anchor("idle")
        self.paused = False
        self.paused_at = 0.0
        self.last_render_at = self.started

    def set_mode(self, mode, now=None, side=None):
        now = time.monotonic() if now is None else now
        self.mode = mode
        self.started = now
        self.last_touch = now
        self.side = side if mode == "dock" else None
        self.velocity = 0.0
        self.last_render_at = now

    def pickup(self, now=None):
        self.paused = False
        self.set_mode("drag", now)

    def drag(self, dx, dt):
        self.velocity = (
            self.velocity * 0.72 + max(-1, min(1, dx / max(0.008, dt) / 700)) * 0.28
        )

    def release(self, side=None, now=None):
        self.set_mode("dock" if side else "land", now, side)

    def click(self, now=None):
        self.paused = False
        self.set_mode("wake" if self.mode in ("sleep", "dock") else "wave", now)

    def toggle_sleep(self, now=None):
        self.set_mode("wake" if self.mode == "sleep" else "sleep", now)

    def set_paused(self, paused, now=None):
        now = time.monotonic() if now is None else now
        if paused == self.paused:
            return
        if paused:
            self.paused_at = now
        else:
            delay = now - self.paused_at
            self.started += delay
            self.changed += delay
            self.last_touch += delay
        self.paused = paused

    def _pose(self, now):
        elapsed = now - self.started
        if self.mode == "drag":
            if elapsed < 0.16:
                return "lift-start"
            cycle = int((elapsed - 0.16) / 0.19) % 6
            return ("lift", "lift-left", "lift", "lift-right", "lift", "lift-blink")[
                cycle
            ]
        if self.mode == "dock":
            key = (
                "right"
                if self.side == "right"
                else "side" if self.side == "left" else "peek"
            )
            return key + "-blink" if elapsed % 5 > 4.78 else key
        sequences = {
            "wave": (
                ("greet", 0.16),
                ("wave-left", 0.18),
                ("wave-right", 0.18),
                ("wave-left", 0.18),
                ("wave-right", 0.18),
                ("lower-hand", 0.17),
            ),
            "land": (("lower", 0.13), ("land", 0.15), ("settle", 0.16)),
            "wake": (("wake", 0.20), ("stretch", 0.25), ("settle", 0.16)),
        }
        if self.mode in sequences:
            remaining = elapsed
            for pose, duration in sequences[self.mode]:
                if remaining < duration:
                    return pose
                remaining -= duration
            self.mode = "idle"
            self.started = now
            elapsed = 0
        if self.mode == "sleep":
            return "drowsy" if elapsed < 0.24 else "sleep"
        if now - self.last_touch > 50:
            self.set_mode("sleep", now)
            return "drowsy"
        blink = elapsed % 5.2
        if blink > 4.92:
            return ("half", "blink", "open", "idle")[min(3, int((blink - 4.92) / 0.07))]
        return "idle"

    def frame(self, now=None):
        now = time.monotonic() if now is None else now
        if self.paused:
            return MotionFrame(self.last_image, self.previous_pose, False)
        self.velocity *= math.exp(-4 * max(0, min(0.1, now - self.last_render_at)))
        self.last_render_at = now
        pose = self._pose(now)
        if pose != self.previous_pose:
            # Blend from the actually displayed frame, including unfinished
            # transitions. All frames are composited onto a fresh clear canvas.
            self.source = self.last_image.copy()
            self.source_head = self.display_head
            self.target_head = head_anchor(pose)
            self.target = sprite(pose)
            self.previous_pose = pose
            self.changed = now
        amount = min(1, max(0, (now - self.changed) / 0.10))
        smooth = amount * amount * (3 - 2 * amount)
        anchor = tuple(
            self.source_head[i] + (self.target_head[i] - self.source_head[i]) * smooth
            for i in (0, 1)
        )
        if amount < 1:
            old = translated(
                self.source,
                anchor[0] - self.source_head[0],
                anchor[1] - self.source_head[1],
            )
            new = translated(
                self.target,
                anchor[0] - self.target_head[0],
                anchor[1] - self.target_head[1],
            )
            base = Image.blend(
                old.convert("RGBa"), new.convert("RGBa"), smooth
            ).convert("RGBA")
        else:
            base = self.target
            anchor = self.target_head
        elapsed = now - self.started
        angle = 0.0
        dy = 0.0
        sx = sy = 1.0
        if self.mode == "drag":
            angle = math.sin(elapsed * 7) * 2.2 - self.velocity * 8
            dy = -3 + math.sin(elapsed * 8) * 1.4
        elif self.mode == "land":
            bounce = max(0, 1 - elapsed / 0.46)
            sy = 1 - 0.08 * math.sin(elapsed / 0.46 * math.pi) * bounce
            sx = 1 + 0.04 * math.sin(elapsed / 0.46 * math.pi) * bounce
        elif self.mode == "wave":
            dy = -abs(math.sin(elapsed * 7)) * 2.5
        elif self.mode == "dock":
            dy = math.sin(elapsed * 1.6) * 0.6
        else:
            sy = 1 + math.sin(now * 2) * 0.008
            dy = math.sin(now * 2) * 0.7
        # The head/hat is the suspension point while picked up.
        center = (SIZE[0] / 2, 24 if self.mode == "drag" else SIZE[1] - 12)
        scaled = base.resize(
            (max(1, round(SIZE[0] * sx)), max(1, round(SIZE[1] * sy))),
            Image.Resampling.BICUBIC,
        )
        transformed = scaled.rotate(
            angle, Image.Resampling.BICUBIC, expand=False, center=center
        )
        canvas = Image.new("RGBA", SIZE)
        canvas.alpha_composite(
            transformed,
            (
                round((SIZE[0] - transformed.width) / 2),
                round(SIZE[1] - transformed.height + dy),
            ),
        )
        self.last_image = canvas
        self.display_head = (anchor[0], anchor[1] + dy)
        return MotionFrame(
            canvas, pose, self.mode in ("drag", "wave", "land", "wake") or amount < 1
        )


class PositionTween:
    """Short eased edge snapping, interruptible by the next mouse press."""

    def __init__(self):
        self.source = self.target = (0, 0)
        self.started = None

    def begin(self, source, target, now=None):
        self.source, self.target = source, target
        self.started = time.monotonic() if now is None else now

    def cancel(self):
        self.started = None

    def sample(self, now=None):
        if self.started is None:
            return self.target, False
        now = time.monotonic() if now is None else now
        fraction = max(0, min(1, (now - self.started) / 0.18))
        ease = 1 - (1 - fraction) ** 3
        point = tuple(
            round(self.source[i] + (self.target[i] - self.source[i]) * ease)
            for i in (0, 1)
        )
        active = fraction < 1
        if not active:
            self.started = None
        return point, active


def clamp_position(point, area):
    return tuple(max(0, min(max(0, area[i] - SIZE[i]), point[i])) for i in (0, 1))


def nearest_edge(point, area, threshold=26):
    x, y = point
    w, h = area
    distances = {
        "left": x,
        "right": w - SIZE[0] - x,
        "top": y,
        "bottom": h - SIZE[1] - y,
    }
    side = min(distances, key=distances.get)
    return side if distances[side] < threshold else None


def dock_position(point, area, side):
    x, y = clamp_position(point, area)
    w, h = area
    if side == "left":
        x = -46
    elif side == "right":
        x = w - SIZE[0] + 46
    elif side == "top":
        y = -64
    elif side == "bottom":
        y = h - SIZE[1] + 10
    return x, y


def shape_tk_pet(canvas, image):
    """Exact Windows child-window mask; only the current frame is visible/hittable."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    gdi.CreateRectRgn.argtypes = [ctypes.c_int] * 4
    gdi.CreateRectRgn.restype = wintypes.HANDLE
    gdi.CombineRgn.argtypes = [wintypes.HANDLE] * 3 + [ctypes.c_int]
    gdi.DeleteObject.argtypes = [wintypes.HANDLE]
    user.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
    alpha = image.getchannel("A").point([0] * 13 + [255] * 243)
    spans = []
    for y in range(image.height):
        box = alpha.crop((0, y, image.width, y + 1)).getbbox()
        if not box:
            continue
        left, right = box[0], box[2]
        if (
            spans
            and spans[-1][0] == left
            and spans[-1][2] == right
            and spans[-1][3] == y
        ):
            old = spans.pop()
            spans.append((left, old[1], right, y + 1))
        else:
            spans.append((left, y, right, y + 1))
    region = gdi.CreateRectRgn(0, 0, 0, 0)
    if not region:
        return
    try:
        for span in spans:
            part = gdi.CreateRectRgn(*span)
            if part:
                gdi.CombineRgn(region, region, part, 2)
                gdi.DeleteObject(part)
        if user.SetWindowRgn(canvas.winfo_id(), region, True):
            region = None
    finally:
        if region:
            gdi.DeleteObject(region)
