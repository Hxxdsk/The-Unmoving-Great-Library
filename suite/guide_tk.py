"""Tk desktop pet only; independent of task controls, settings and APIs."""

import tkinter as tk
import time
from PIL import ImageTk
from .guide_support import (
    PetAnimator,
    PositionTween,
    SIZE,
    FRAME_MS,
    clamp_position,
    nearest_edge,
    dock_position,
    shape_tk_pet,
)


class DesktopPet:
    def __init__(self, owner):
        self.root = owner.root
        self.engine = PetAnimator()
        self.snap = PositionTween()
        self.closed = False
        self.visible = False
        self.started = False
        self.suspended = False
        self.was_paused = False
        self.tick_id = None
        self.pet_x = self.pet_y = 0
        self.drag_start = None
        self.photo = None
        self.canvas = tk.Canvas(
            self.root,
            width=SIZE[0],
            height=SIZE[1],
            highlightthickness=0,
            bg=self.root.cget("bg"),
            takefocus=False,
            cursor="hand2",
        )
        self.pet_id = self.canvas.create_image(0, 0, anchor="nw")
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.release)
        self.canvas.bind("<Double-Button-1>", self.sleep)
        self.canvas.bind("<Button-3>", self.pause_animation)
        self.root.bind("<Configure>", self.resize, add="+")
        self.root.bind("<Unmap>", self.unmap, add="+")
        self.root.bind("<Map>", self.mapped, add="+")
        self.root.bind("<Destroy>", self.destroyed, add="+")
        self.start_id = self.root.after_idle(self.start)

    def start(self):
        self.start_id = None
        if self.closed:
            return
        if not self.started:
            self.started = True
            self.pet_x = self.root.winfo_width() - SIZE[0] - 18
            self.pet_y = self.root.winfo_height() - 320
        self.visible = bool(self.root.winfo_ismapped())
        self.arrange()
        self.tick()

    def area(self):
        return self.root.winfo_width(), self.root.winfo_height()

    def arrange(self, smooth=False):
        point = (self.pet_x, self.pet_y)
        target = (
            dock_position(point, self.area(), self.engine.side)
            if self.engine.mode == "dock"
            else clamp_position(point, self.area())
        )
        if smooth:
            self.snap.begin(point, target)
        else:
            self.snap.cancel()
            self.pet_x, self.pet_y = target
            self.place()

    def place(self):
        self.canvas.place(x=self.pet_x, y=self.pet_y, width=SIZE[0], height=SIZE[1])
        tk.Misc.lift(self.canvas)

    def resize(self, event):
        if event.widget is self.root and not self.closed:
            self.arrange()

    def stop_timer(self):
        if self.tick_id:
            self.root.after_cancel(self.tick_id)
            self.tick_id = None

    def unmap(self, event):
        if event.widget is self.root:
            self.visible = False
            if not self.suspended:
                self.suspended = True
                self.was_paused = self.engine.paused
                self.engine.set_paused(True)
            self.stop_timer()

    def mapped(self, event):
        if event.widget is self.root:
            self.visible = True
            if self.suspended:
                self.suspended = False
                self.engine.set_paused(self.was_paused)
            if self.started:
                self.tick()

    def destroyed(self, event):
        if event.widget is self.root:
            self.closed = True
            self.stop_timer()
            if self.start_id:
                self.root.after_cancel(self.start_id)
                self.start_id = None

    def press(self, event):
        self.snap.cancel()
        self.drag_start = (event.x_root, event.y_root, self.pet_x, self.pet_y)
        self.last_move = time.monotonic()
        self.last_x = event.x_root
        self.moved = False
        self.engine.pickup()
        self.tick()

    def drag(self, event):
        if not self.drag_start:
            return
        x, y, px, py = self.drag_start
        dx, dy = event.x_root - x, event.y_root - y
        self.moved |= abs(dx) + abs(dy) > 5
        now = time.monotonic()
        self.engine.drag(event.x_root - self.last_x, now - self.last_move)
        self.last_x = event.x_root
        self.last_move = now
        self.pet_x, self.pet_y = px + dx, py + dy
        self.arrange()

    def release(self, event):
        if not self.drag_start:
            return
        self.drag_start = None
        if self.moved:
            self.engine.release(nearest_edge((self.pet_x, self.pet_y), self.area()))
        else:
            self.engine.click()
        self.arrange(smooth=self.engine.mode == "dock")
        self.tick()

    def sleep(self, event):
        self.drag_start = None
        self.engine.toggle_sleep()
        self.arrange()
        self.tick()

    def pause_animation(self, event):
        self.engine.set_paused(not self.engine.paused)
        self.tick()

    def tick(self):
        started = time.monotonic()
        self.stop_timer()
        if self.closed or not self.visible:
            return
        moving = False
        if self.snap.started is not None and not self.engine.paused:
            point, moving = self.snap.sample()
            self.pet_x, self.pet_y = point
            self.place()
        frame = self.engine.frame()
        # Replace the only canvas image. No old sprite items remain underneath.
        photo = ImageTk.PhotoImage(frame.image, master=self.root)
        self.canvas.delete(self.pet_id)
        self.pet_id = self.canvas.create_image(0, 0, anchor="nw", image=photo)
        self.photo = photo
        shape_tk_pet(self.canvas, frame.image)
        if not self.engine.paused:
            budget = FRAME_MS if frame.active or moving else 50
            wait = max(1, round(budget - (time.monotonic() - started) * 1000))
            self.tick_id = self.root.after(wait, self.tick)


def install_tk_guide(owner, component=None):
    owner.patchouli_guide = DesktopPet(owner)
    return owner.patchouli_guide
