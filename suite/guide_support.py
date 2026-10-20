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
FRAME_MS = 20

# Body atlases are segmented by connected sprite outlines. Peeking artwork
# has explicit bounds to exclude the following row.
ART = {
    "idle": ("pet-idle.png", 0, 4, 4),
    "half": ("pet-idle.png", 1, 4, 4),
    "blink-half": ("pet-idle.png", 2, 4, 4),
    "blink": ("pet-idle.png", 3, 4, 4),
    "blink-hold": ("pet-idle.png", 4, 4, 4),
    "open-half": ("pet-idle.png", 5, 4, 4),
    "open": ("pet-idle.png", 6, 4, 4),
    "neutral": ("pet-idle.png", 7, 4, 4),
    "wave-1": ("pet-idle.png", 8, 4, 4),
    "wave-2": ("pet-idle.png", 9, 4, 4),
    "wave-3": ("pet-idle.png", 10, 4, 4),
    "wave-4": ("pet-idle.png", 11, 4, 4),
    "wave-5": ("pet-idle.png", 12, 4, 4),
    "wave-6": ("pet-idle.png", 13, 4, 4),
    "wave-7": ("pet-idle.png", 14, 4, 4),
    "wave-8": ("pet-idle.png", 15, 4, 4),
    "pickup-1": ("pet-lift.png", 0, 4, 4),
    "pickup-2": ("pet-lift.png", 1, 4, 4),
    "pickup-3": ("pet-lift.png", 2, 4, 4),
    "pickup-4": ("pet-lift.png", 3, 4, 4),
    "hang-left": ("pet-lift.png", 4, 4, 4),
    "hang-center": ("pet-lift.png", 5, 4, 4),
    "hang-right": ("pet-lift.png", 6, 4, 4),
    "hang-return": ("pet-lift.png", 7, 4, 4),
    "land-1": ("pet-lift.png", 8, 4, 4),
    "land-2": ("pet-lift.png", 9, 4, 4),
    "land-3": ("pet-lift.png", 10, 4, 4),
    "land-4": ("pet-lift.png", 11, 4, 4),
    "land-5": ("pet-lift.png", 12, 4, 4),
    "land-6": ("pet-lift.png", 13, 4, 4),
    "land-7": ("pet-lift.png", 14, 4, 4),
    "land-8": ("pet-lift.png", 15, 4, 4),
}
PEEK = {
    "peek": (0, 0.035, 0.50, 0.438),
    "peek-blink": (0.50, 0.035, 1, 0.438),
    "side": (0, 0.46, 0.50, 0.99),
    "side-blink": (0.50, 0.46, 1, 0.99),
}


# Pixel-connected sprites are extracted as individual objects, rather than
# assuming an AI-drawn sheet has perfectly equal cells. Tiny disconnected
# atlas debris is excluded from the rendered sprite, not cached as another pose.
def _sprite_objects(source, expected):
    import re
    from PIL import ImageDraw, ImageChops

    alpha = source.getchannel("A")
    pixels = alpha.tobytes()
    width, height = source.size
    parents = []
    spans = []
    previous = []

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    for y in range(height):
        current = []
        for match in re.finditer(rb"[\x30-\xff]+", pixels[y * width : (y + 1) * width]):
            left, right = match.span()
            index = len(parents)
            parents.append(index)
            for pl, pr, pi in previous:
                if pr < left:
                    continue
                if pl > right:
                    break
                a, b = root(index), root(pi)
                if a != b:
                    parents[a] = b
            spans.append((left, right, y, index))
            current.append((left, right, index))
        previous = current
    groups = {}
    for left, right, y, index in spans:
        key = root(index)
        group = groups.setdefault(
            key, {"area": 0, "spans": [], "box": [left, y, right, y + 1]}
        )
        group["area"] += right - left
        group["spans"].append((left, right, y))
        box = group["box"]
        box[0] = min(box[0], left)
        box[1] = min(box[1], y)
        box[2] = max(box[2], right)
        box[3] = y + 1
    objects = sorted(groups.values(), key=lambda group: group["area"], reverse=True)[
        :expected
    ]
    if (
        len(objects) != expected
        or min(group["area"] for group in objects) < width * height * 0.004
    ):
        raise ValueError("桌宠图集缺少完整动作帧")
    objects.sort(key=lambda group: (group["box"][1] + group["box"][3]) / 2)
    columns = 4
    ordered = []
    for row in range(expected // columns):
        ordered.extend(
            sorted(
                objects[row * columns : (row + 1) * columns],
                key=lambda group: (group["box"][0] + group["box"][2]) / 2,
            )
        )
    frames = []
    for group in ordered:
        left, top, right, bottom = group["box"]
        left = max(0, left - 2)
        top = max(0, top - 2)
        right = min(width, right + 2)
        bottom = min(height, bottom + 2)
        image = source.crop((left, top, right, bottom))
        mask = Image.new("L", image.size)
        draw = ImageDraw.Draw(mask)
        for start, end, y in group["spans"]:
            draw.rectangle(
                (start - left - 1, y - top - 1, end - left, y - top + 1), fill=255
            )
        image.putalpha(ImageChops.multiply(image.getchannel("A"), mask))
        frames.append(image)
    return frames


@lru_cache(maxsize=3)
def _atlas_frames(filename):
    with Image.open(ASSETS / filename) as file:
        source = file.convert("RGBA")
        if filename == "guide-peek.png":
            return {
                name: _small_peek(source, name, rect) for name, rect in PEEK.items()
            }
        objects = _sprite_objects(source, 16)
        # One scale for the whole atlas: no individual thumbnail fitting, and
        # therefore no frame-to-frame head-size jumps.
        factor = min(
            106 / max(im.width for im in objects),
            114 / max(im.height for im in objects),
        )
        frames = {}
        for name, (atlas, index, _, _) in ART.items():
            if atlas != filename:
                continue
            image = objects[index]
            image = image.resize(
                (
                    max(1, round(image.width * factor)),
                    max(1, round(image.height * factor)),
                ),
                Image.Resampling.LANCZOS,
            )
            canvas = Image.new("RGBA", SIZE)
            canvas.alpha_composite(
                image, ((SIZE[0] - image.width) // 2, SIZE[1] - 10 - image.height)
            )
            frames[name] = canvas
        return frames


def _small_peek(source, name, rect):
    w, h = source.size
    image = source.crop(
        tuple(round(v * (w if i % 2 == 0 else h)) for i, v in enumerate(rect))
    )
    box = image.getbbox()
    if box:
        image = image.crop(box)
    image.thumbnail(
        (100, 76) if name.startswith("peek") else (78, 108), Image.Resampling.LANCZOS
    )
    canvas = Image.new("RGBA", SIZE)
    canvas.alpha_composite(
        image, ((SIZE[0] - image.width) // 2, SIZE[1] - 10 - image.height)
    )
    return canvas


@lru_cache(maxsize=1)
def _fallback_sprite():
    canvas = Image.new("RGBA", SIZE)
    try:
        with Image.open(ASSETS / "guide-patchouli.png") as file:
            image = file.convert("RGBA")
            image.thumbnail((104, 114), Image.Resampling.LANCZOS)
            canvas.alpha_composite(
                image, ((SIZE[0] - image.width) // 2, SIZE[1] - 10 - image.height)
            )
    except OSError:
        pass
    return canvas


@lru_cache(maxsize=40)
def sprite(name="idle"):
    """One padded frame with explicit atlas bounds and no neighboring sprites."""
    flip = name.startswith("right")
    key = ("side-blink" if name.endswith("blink") else "side") if flip else name
    filename = "guide-peek.png" if key in PEEK else ART[key][0]
    try:
        image = _atlas_frames(filename)[key]
    except (OSError, ValueError, KeyError):
        # An incomplete asset update must not stop a product-processing tool.
        image = _fallback_sprite()
    return ImageOps.mirror(image) if flip else image


@dataclass(frozen=True)
class MotionFrame:
    image: Image.Image
    pose: str
    active: bool


# Explicit in-betweens, with a neutral start and end. No previously rendered
# image participates in another frame: there is always exactly one silhouette.
IDLE_FRAMES = (
    "idle",
    "half",
    "blink-half",
    "blink",
    "blink-hold",
    "open-half",
    "open",
    "idle",
)
WAVE_FRAMES = (
    "wave-1",
    "wave-2",
    "wave-3",
    "wave-4",
    "wave-5",
    "wave-4",
    "wave-5",
    "wave-4",
    "wave-7",
    "wave-8",
)
PICKUP_FRAMES = ("pickup-1", "pickup-2", "pickup-3", "pickup-4")
HANG_FRAMES = ("hang-left", "hang-center", "hang-right", "hang-return")
LAND_FRAMES = (
    "land-1",
    "land-2",
    "land-3",
    "land-4",
    "land-5",
    "land-6",
    "land-7",
    "land-8",
)


def ease(value):
    value = max(0, min(1, value))
    return value * value * (3 - 2 * value)


def _sample(sequence, elapsed, step):
    return sequence[min(len(sequence) - 1, max(0, int(elapsed / step)))]


class PetAnimator:
    """Canonical single-sprite sampling. Rendering history cannot cause trails."""

    def __init__(self, now=None):
        self.mode = "idle"
        self.started = self.last_touch = time.monotonic() if now is None else now
        self.side = None
        self.entry_side = None
        self.velocity = 0.0
        self.velocity_at = self.started
        self.entry_transform = (0.0, 0.0, 1.0, 1.0)
        self.previous_pose = "idle"
        self.last_image = sprite("idle")
        self.paused = False
        self.paused_at = 0.0

    def _advance(self, now):
        durations = {
            "wave": len(WAVE_FRAMES) * 0.075,
            "land": len(LAND_FRAMES) * 0.065,
            "wake": 0.35,
        }
        duration = durations.get(self.mode)
        if duration is not None and now >= self.started + duration:
            self.started += duration
            self.mode = "idle"
            self.entry_side = None
            self.entry_transform = (0.0, 0.0, 1.0, 1.0)
        if self.mode == "idle" and now - self.last_touch >= 50:
            self.mode = "sleep"
            self.started = self.last_touch + 50

    def _target_motion(self, now):
        elapsed = max(0, now - self.started)
        angle = dy = 0.0
        sx = sy = 1.0
        if self.mode == "drag":
            velocity = self.velocity * math.exp(-5 * max(0, now - self.velocity_at))
            angle = -velocity * 6 + math.sin(elapsed * 5) * 1.1
            dy = -2 * ease(elapsed / 0.25) + math.sin(elapsed * 5) * 0.55
        elif self.mode == "land":
            progress = min(1, elapsed / (len(LAND_FRAMES) * 0.065))
            impact = math.sin(math.pi * progress) ** 2
            sy = 1 - 0.035 * impact
            sx = 1 + 0.018 * impact
        elif self.mode == "wave":
            dy = -math.sin(min(1, elapsed / (len(WAVE_FRAMES) * 0.075)) * math.pi) * 0.9
        elif self.mode == "dock":
            dy = math.sin(elapsed * 1.5) * 0.35
        else:
            dy = math.sin(now * 1.8) * 0.45
            sy = 1 + math.sin(now * 1.8) * 0.004
        target = (angle, dy, sx, sy)
        transition = ease(elapsed / 0.16)
        return tuple(
            self.entry_transform[i] + (target[i] - self.entry_transform[i]) * transition
            for i in range(4)
        )

    def set_mode(self, mode, now=None, side=None):
        now = time.monotonic() if now is None else now
        self._advance(now)
        previous = self.mode
        old_side = self.side if previous == "dock" else None
        transform = self._target_motion(now)
        self.mode = mode
        self.started = now
        self.last_touch = now
        self.entry_side = old_side if mode in ("drag", "wake", "wave") else None
        self.side = side if mode == "dock" else None
        self.entry_transform = transform
        self.velocity = 0.0
        self.velocity_at = now

    def pickup(self, now=None):
        self.set_paused(False, now)
        self.set_mode("drag", now)

    def drag(self, dx, dt, now=None):
        now = time.monotonic() if now is None else now
        current = self.velocity * math.exp(-5 * max(0, now - self.velocity_at))
        self.velocity = (
            current * 0.65 + max(-1, min(1, dx / max(0.008, dt) / 700)) * 0.35
        )
        self.velocity_at = now

    def release(self, side=None, now=None):
        self.set_mode("dock" if side else "land", now, side)

    def click(self, now=None):
        self.set_paused(False, now)
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
            duration = now - self.paused_at
            self.started += duration
            self.last_touch += duration
            self.velocity_at += duration
        self.paused = paused

    @staticmethod
    def peek_pose(side, closed=False):
        name = "right" if side == "right" else "side" if side == "left" else "peek"
        return name + "-blink" if closed else name

    def _pose(self, now):
        elapsed = max(0, now - self.started)
        if self.mode == "drag":
            if elapsed < 0.24:
                return _sample(PICKUP_FRAMES, elapsed, 0.06)
            return HANG_FRAMES[int((elapsed - 0.24) / 0.085) % len(HANG_FRAMES)]
        if self.mode == "dock":
            return self.peek_pose(self.side, elapsed % 5 > 4.84)
        if self.mode == "wave":
            return _sample(WAVE_FRAMES, elapsed, 0.075)
        if self.mode == "land":
            return _sample(LAND_FRAMES, elapsed, 0.065)
        if self.mode == "wake":
            return _sample(("blink-hold", "open-half", "open", "idle"), elapsed, 0.0875)
        if self.mode == "sleep":
            return _sample(IDLE_FRAMES[:5], elapsed, 0.065)
        blink = elapsed % 5.2
        return _sample(IDLE_FRAMES, blink - 4.8, 0.05) if blink >= 4.8 else "idle"

    def _edge_entry(self, pose, elapsed):
        """Tuck one pose away before revealing the next; never overlay faces."""
        side = self.side if self.mode == "dock" else self.entry_side
        if not side or elapsed >= 0.30:
            return pose, 0, 0
        if elapsed < 0.13:
            pose = "land-2" if self.mode == "dock" else self.peek_pose(side)
            travel = ease(elapsed / 0.13)
        else:
            travel = 1 - ease((elapsed - 0.13) / 0.17)
        if side == "left":
            return pose, -SIZE[0] * travel, 0
        if side == "right":
            return pose, SIZE[0] * travel, 0
        if side == "top":
            return pose, 0, -SIZE[1] * travel
        return pose, 0, SIZE[1] * travel

    def frame(self, now=None):
        now = time.monotonic() if now is None else now
        if self.paused:
            return MotionFrame(self.last_image, self.previous_pose, False)
        self._advance(now)
        elapsed = max(0, now - self.started)
        pose = self._pose(now)
        pose, shift_x, shift_y = self._edge_entry(pose, elapsed)
        angle, dy, sx, sy = self._target_motion(now)
        base = sprite(pose)
        # One subpixel affine sample, rather than rounding a resize/move to
        # whole pixels. Idle breathing and pendulum motion remain continuous.
        cx, cy = SIZE[0] / 2, 24 if self.mode == "drag" else SIZE[1] - 12
        radians = math.radians(angle)
        cosine, sine = math.cos(radians), math.sin(radians)
        a, b = cosine / sx, -sine / sx
        d, e = sine / sy, cosine / sy
        c = cx - a * (cx + shift_x) - b * (cy + dy + shift_y)
        f = cy - d * (cx + shift_x) - e * (cy + dy + shift_y)
        image = (
            base.convert("RGBa")
            .transform(
                SIZE,
                Image.Transform.AFFINE,
                (a, b, c, d, e, f),
                Image.Resampling.BICUBIC,
                fillcolor=(0, 0, 0, 0),
            )
            .convert("RGBA")
        )
        self.last_image = image
        self.previous_pose = pose
        active = (
            self.mode in ("drag", "wave", "land", "wake")
            or elapsed < 0.35
            or self.mode == "idle"
            and elapsed % 5.2 >= 4.8
        )
        return MotionFrame(image, pose, active)


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
    user.GetParent.argtypes = [wintypes.HWND]
    user.GetParent.restype = wintypes.HWND
    user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.MapWindowPoints.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_void_p,
        wintypes.UINT,
    ]
    user.InvalidateRect.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.RECT),
        wintypes.BOOL,
    ]
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
        hwnd = canvas.winfo_id()
        if user.SetWindowRgn(hwnd, region, True):
            region = None
            # Invalidate the underlying parent too: pixels outside a shrinking
            # native region otherwise may survive until the next window paint.
            parent = user.GetParent(hwnd)
            rect = wintypes.RECT()
            if parent and user.GetClientRect(hwnd, ctypes.byref(rect)):
                user.MapWindowPoints(hwnd, parent, ctypes.byref(rect), 2)
                user.InvalidateRect(parent, ctypes.byref(rect), False)
    finally:
        if region:
            gdi.DeleteObject(region)
