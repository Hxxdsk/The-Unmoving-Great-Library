"""Floating Tk desktop companion; never packed into the business layout."""

import time
import tkinter as tk
from PIL import ImageTk, ImageOps
from .guide_content import GuideSession, read_state
from .guide_support import (
    sprite,
    candidates,
    bubble_position,
    ChatRequest,
    TUTORIAL_BUTTONS,
    shape_tk_pet,
)


class TkGuideShelf:
    def __init__(self, owner, component):
        self.owner = owner
        self.root = owner.root
        self.component = component
        self.session = GuideSession(component)
        self.state = {}
        self.note = None
        self.tick_id = self.bounce_id = self.chat_id = self.bob_id = None
        self.dock = None
        self.ticks = 0
        self.chat = ChatRequest()
        self.drag_start = None
        self.pose = "idle"
        self.pose_until = 0
        self.last_touch = time.monotonic()
        self.pet_x = self.pet_y = 0
        self.visible = True
        self.closed = False
        self.images = {
            name: ImageTk.PhotoImage(sprite(name), master=self.root)
            for name in [
                "idle",
                "blink",
                "wave",
                "read",
                "think",
                "sleep",
                "peek",
                "peek-blink",
                "side",
                "side-blink",
            ]
        }
        self.images["right"] = ImageTk.PhotoImage(
            ImageOps.mirror(sprite("side")), master=self.root
        )
        self.images["right-blink"] = ImageTk.PhotoImage(
            ImageOps.mirror(sprite("side-blink")), master=self.root
        )
        bg = self.root.cget("bg")
        self.canvas = tk.Canvas(
            self.root,
            width=112,
            height=112,
            highlightthickness=0,
            bg=bg,
            takefocus=False,
            cursor="hand2",
        )
        self.pet_id = self.canvas.create_image(
            56, 112, image=self.images["idle"], anchor="s"
        )
        self.bubble = tk.Frame(
            self.root, bg="#fffafd", highlightbackground="#bda4d8", highlightthickness=1
        )
        row = tk.Frame(self.bubble, bg="#fffafd")
        row.pack(fill="x", padx=8, pady=(5, 0))
        self.heading = tk.Label(row, bg="#fffafd", fg="#76549b", anchor="w")
        self.heading.pack(side="left", expand=True, fill="x")
        for title, command in [
            ("‹", lambda: self.turn(-1)),
            ("›", lambda: self.turn(1)),
            ("×", self.collapse),
        ]:
            tk.Button(
                row,
                text=title,
                command=command,
                takefocus=False,
                bd=0,
                bg="#fffafd",
                fg="#76549b",
            ).pack(side="left")
        self.message = tk.Label(
            self.bubble,
            bg="#fffafd",
            fg="#554365",
            wraplength=304,
            justify="left",
            anchor="w",
        )
        self.message.pack(fill="x", padx=10, pady=6)
        row = tk.Frame(self.bubble, bg="#fffafd")
        row.pack(fill="x", padx=8, pady=(0, 7))
        self.question = tk.Entry(row)
        self.question.pack(side="left", expand=True, fill="x")
        self.question.bind("<Return>", lambda _: self.ask_ai())
        self.ask = tk.Button(row, text="问帕秋莉", command=self.ask_ai, takefocus=False)
        self.ask.pack(side="right")
        self.borders = [
            tk.Frame(self.root, bg="#b48bdd", takefocus=False) for _ in range(4)
        ]
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.release)
        self.canvas.bind("<Double-Button-1>", lambda _: self.collapse())
        self.root.bind("<Configure>", self.resized, add="+")
        self.root.bind("<Unmap>", self.unmap, add="+")
        self.root.bind("<Map>", self.mapped, add="+")
        self.root.bind("<Destroy>", self.destroyed, add="+")
        self.root.after_idle(self.start)

    def start(self):
        if self.closed:
            return
        self.pet_x = max(0, self.root.winfo_width() - 126)
        self.pet_y = max(0, self.root.winfo_height() - 300)
        self.arrange()
        self.refresh()
        self.animate()

    def pause(self):
        for name in ("tick_id", "bounce_id", "chat_id", "bob_id"):
            identifier = getattr(self, name)
            if identifier:
                try:
                    self.root.after_cancel(identifier)
                except tk.TclError:
                    pass
                setattr(self, name, None)

    def unmap(self, event):
        if event.widget is self.root:
            self.visible = False
            self.pause()

    def mapped(self, event):
        if event.widget is self.root:
            self.visible = True
            if not self.tick_id:
                self.refresh()
            if not self.bounce_id:
                self.animate()
            if self.chat.busy and not self.chat_id:
                self.poll_chat()

    def destroyed(self, event):
        if event.widget is self.root:
            self.closed = True
            self.pause()
            self.chat.stop()

    def refresh(self):
        if self.tick_id:
            self.root.after_cancel(self.tick_id)
        self.tick_id = None
        if self.closed or not self.visible or self.session.muted:
            return
        self.state = read_state(self.owner, self.component)
        note = self.session.choose(self.state)
        if note != self.note:
            self.show_note(note)
            self.interact("think" if note.key.startswith("setup") else "wave")
        self.arrange()
        self.tick_id = self.root.after(1400, self.refresh)

    def show_note(self, note):
        self.note = note
        self.heading.configure(text="帕秋莉 · " + note.title)
        self.message.configure(text=note.text)
        self.bubble.place(x=4, y=4, width=330)
        self.arrange()

    def turn(self, delta):
        self.show_note(self.session.turn(self.state, delta))
        self.interact("read")

    def collapse(self):
        self.bubble.place_forget()
        for border in self.borders:
            border.place_forget()

    def target(self):
        labels = TUTORIAL_BUTTONS.get((self.component, self.note.key), ())
        stack = list(self.root.winfo_children())
        while labels and stack:
            widget = stack.pop()
            if widget in (self.bubble, self.canvas):
                continue
            try:
                text = str(widget.cget("text"))
                if (
                    widget.winfo_ismapped()
                    and any(label in text for label in labels)
                    and widget.winfo_class() in ("Button", "TButton")
                ):
                    return widget
            except tk.TclError:
                pass
            stack.extend(widget.winfo_children())
        for item in candidates(self.owner, self.component, self.note.key):
            if isinstance(item, tk.Widget) and item.winfo_ismapped():
                return item
            # Resolve Tk variables to their live Entry instead of guessing by text.
            if isinstance(item, tk.Variable):
                stack = list(self.root.winfo_children())
                while stack:
                    widget = stack.pop()
                    if widget is self.bubble:
                        continue
                    try:
                        if (
                            str(widget.cget("textvariable")) == str(item)
                            and widget.winfo_ismapped()
                        ):
                            return widget
                    except tk.TclError:
                        pass
                    stack.extend(widget.winfo_children())
        return None

    def arrange(self):
        if self.closed:
            return
        w, h = self.root.winfo_width(), self.root.winfo_height()
        self.pet_x = max(0, min(w - 112, self.pet_x))
        self.pet_y = max(0, min(h - 112, self.pet_y))
        if self.session.muted:
            if self.dock == "left":
                self.pet_x = -48
            elif self.dock == "right":
                self.pet_x = w - 64
            elif self.dock == "top":
                self.pet_y = -38
            else:
                self.pet_y = h - 72
        self.canvas.place(x=self.pet_x, y=self.pet_y, width=112, height=112)
        tk.Misc.lift(self.canvas)
        if not self.note or self.session.muted or not self.bubble.winfo_manager():
            return
        target = self.target()
        rect = None
        if target:
            rect = (
                target.winfo_rootx() - self.root.winfo_rootx(),
                target.winfo_rooty() - self.root.winfo_rooty(),
                target.winfo_width(),
                target.winfo_height(),
            )
        width = min(330, max(210, w - 20))
        self.message.configure(wraplength=width - 22)
        height = self.bubble.winfo_reqheight()
        x, y = bubble_position((w, h), rect, (width, height), (self.pet_x, self.pet_y))
        self.bubble.place(x=x, y=y, width=width)
        self.bubble.lift()
        tk.Misc.lift(self.canvas)
        for border in self.borders:
            border.place_forget()
        if rect:
            x, y, rw, rh = rect
            for border, geometry in zip(
                self.borders,
                [
                    (x, y, rw, 2),
                    (x, y + rh - 2, rw, 2),
                    (x, y, 2, rh),
                    (x + rw - 2, y, 2, rh),
                ],
            ):
                border.place(
                    x=geometry[0], y=geometry[1], width=geometry[2], height=geometry[3]
                )
                border.lift()

    def resized(self, event):
        if event.widget is self.root:
            self.arrange()

    def press(self, event):
        self.drag_start = (event.x_root, event.y_root, self.pet_x, self.pet_y)
        self.moved = False
        self.interact("wave")

    def drag(self, event):
        if not self.drag_start:
            return
        x, y, px, py = self.drag_start
        dx, dy = event.x_root - x, event.y_root - y
        self.moved |= abs(dx) + abs(dy) > 6
        self.pet_x = px + dx
        self.pet_y = py + dy
        self.collapse()
        self.arrange()

    def release(self, event):
        if not self.drag_start:
            return
        self.drag_start = None
        if self.moved:
            w, h = self.root.winfo_width(), self.root.winfo_height()
            edges = {
                "left": self.pet_x,
                "right": w - 112 - self.pet_x,
                "top": self.pet_y,
                "bottom": h - 112 - self.pet_y,
            }
            self.dock = min(edges, key=edges.get) if min(edges.values()) < 30 else None
            self.set_quiet(bool(self.dock))
        elif self.session.muted:
            self.set_quiet(False)
        elif self.bubble.winfo_manager():
            self.collapse()
        else:
            self.show_note(self.session.choose(self.state))
            self.interact("wave")

    def set_quiet(self, quiet):
        self.session.muted = quiet
        if self.bob_id:
            self.root.after_cancel(self.bob_id)
            self.bob_id = None
        self.canvas.coords(self.pet_id, 56, 112)
        if quiet:
            self.dock = self.dock or "right"
            self.collapse()
            if self.tick_id:
                self.root.after_cancel(self.tick_id)
                self.tick_id = None
        else:
            self.dock = None
            self.pet_x = max(40, min(self.root.winfo_width() - 152, self.pet_x))
            self.pet_y = max(40, min(self.root.winfo_height() - 152, self.pet_y))
            self.refresh()
            self.show_note(self.session.choose(self.state))
        self.arrange()

    def interact(self, pose):
        self.last_touch = time.monotonic()
        self.pose = pose
        self.pose_until = time.monotonic() + 1.2
        if not self.session.muted and self.drag_start is None:
            self.bob(0)

    def bob(self, index):
        if self.bob_id:
            self.root.after_cancel(self.bob_id)
            self.bob_id = None
        offsets = [0, -2, -4, -6, -4, -2, 0]
        self.canvas.coords(self.pet_id, 56, 112 + offsets[index])
        if index + 1 < len(offsets) and self.visible and not self.session.muted:
            self.bob_id = self.root.after(40, lambda: self.bob(index + 1))

    def animate(self):
        if self.bounce_id:
            self.root.after_cancel(self.bounce_id)
        self.bounce_id = None
        if self.closed or not self.visible:
            return
        self.ticks += 1
        blink = self.ticks % 44 in (0, 1)
        if self.session.muted:
            name = (
                "right"
                if self.dock == "right"
                else "side" if self.dock == "left" else "peek"
            )
            if blink:
                name += "-blink"
        elif time.monotonic() < self.pose_until:
            name = self.pose
        elif self.state.get("busy"):
            name = "read" if self.ticks % 40 < 34 else "think"
        elif time.monotonic() - self.last_touch > 45:
            name = "sleep"
        else:
            name = "blink" if blink else "idle"
        self.canvas.itemconfigure(self.pet_id, image=self.images[name])
        if getattr(self, "_shape_name", None) != name:
            shape_tk_pet(self.canvas, name)
            self._shape_name = name
        self.bounce_id = self.root.after(120, self.animate)

    def ask_ai(self):
        if self.note and self.chat.start(
            self.question.get(), self.component, self.note, self.state
        ):
            self.asked_key = self.note.key
            self.ask.configure(state="disabled")
            self.message.configure(text="唔，让我翻翻魔法书…")
            self.interact("read")
            self.poll_chat()

    def poll_chat(self):
        self.chat_id = None
        if self.closed:
            return
        result = self.chat.poll()
        if result is not None:
            self.message.configure(
                text=result if self.note.key == self.asked_key else self.note.text
            )
            self.ask.configure(state="normal")
            self.question.delete(0, "end")
            self.interact("wave")
            self.arrange()
        elif self.chat.busy and self.visible:
            self.chat_id = self.root.after(120, self.poll_chat)


def install_tk_guide(owner, component):
    owner.patchouli_guide = TkGuideShelf(owner, component)
    return owner.patchouli_guide
