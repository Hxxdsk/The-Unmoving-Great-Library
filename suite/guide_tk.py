"""Tk counterpart of the reserved guide shelf. Timers stop when tucked away."""

import tkinter as tk
from pathlib import Path
from PIL import Image, ImageTk

from .guide_content import GuideSession, read_state


class TkGuideShelf(tk.Frame):
    def __init__(self, owner, component):
        root = owner.root
        super().__init__(root, bg="#f4eff9", height=86)
        self.owner, self.session = owner, GuideSession(component)
        self.component = component
        self.state, self.note = {}, None
        self.tick_id = self.bounce_id = None
        self.drag_start = None
        self.pet_x = 12
        self.compact, self.force_expanded = False, False
        self.pack_propagate(False)
        existing = [c for c in root.winfo_children() if c is not self]
        self.pack(side="bottom", fill="x", before=existing[0] if existing else None)
        self.canvas = tk.Canvas(
            self, bg="#f4eff9", highlightthickness=0, height=86, takefocus=False
        )
        self.canvas.pack(fill="both", expand=True)
        path = Path(__file__).resolve().parent.parent / "assets/guide-patchouli.png"
        with Image.open(
            path if path.exists() else path.with_name("patchouli.png")
        ) as image:
            image = image.convert("RGBA")
            image.thumbnail((76, 76), Image.Resampling.LANCZOS)
            self.body = ImageTk.PhotoImage(image, master=root)
            self.head = ImageTk.PhotoImage(
                image.crop((0, 18, image.width, min(52, image.height))), master=root
            )
        self.pet_id = self.canvas.create_image(
            self.pet_x, 4, image=self.body, anchor="nw", tags="pet"
        )
        self.bubble = tk.Frame(
            self.canvas,
            bg="#fffafd",
            highlightbackground="#e1d7ed",
            highlightthickness=1,
        )
        header = tk.Frame(self.bubble, bg="#fffafd")
        header.pack(fill="x", padx=9, pady=(3, 0))
        self.heading = tk.Label(
            header,
            bg="#fffafd",
            fg="#76549b",
            font=("Microsoft YaHei UI", 9, "bold"),
            anchor="w",
        )
        self.heading.pack(side="left", fill="x", expand=True)
        for title, command in [
            ("‹", lambda: self.turn(-1)),
            ("翻一页 ›", lambda: self.turn(1)),
            ("趴一会儿", lambda: self.set_quiet(True)),
        ]:
            tk.Button(
                header,
                text=title,
                command=command,
                bg="#fffafd",
                fg="#76549b",
                relief="flat",
                bd=0,
                takefocus=False,
                font=("Microsoft YaHei UI", 9),
            ).pack(side="left", padx=3)
        self.message = tk.Label(
            self.bubble,
            bg="#fffafd",
            fg="#554365",
            font=("Microsoft YaHei UI", 9),
            justify="left",
            anchor="w",
        )
        self.message.pack(fill="both", expand=True, padx=9, pady=(0, 3))
        self.bubble_id = self.canvas.create_window(
            98, 3, window=self.bubble, anchor="nw", width=700, height=78
        )
        self.canvas.tag_bind("pet", "<ButtonPress-1>", self.press)
        self.canvas.tag_bind("pet", "<B1-Motion>", self.drag)
        self.canvas.tag_bind("pet", "<ButtonRelease-1>", self.release)
        self.canvas.bind("<Configure>", lambda _: self.arrange())
        self.bind("<Destroy>", self.destroyed, add="+")
        self.bind("<Unmap>", lambda _: self.pause(), add="+")
        self.bind(
            "<Map>",
            lambda _: (
                self.refresh() if not self.tick_id and not self.session.muted else None
            ),
            add="+",
        )
        root.bind("<Configure>", self.resized, add="+")
        root.bind(
            "<Unmap>",
            lambda event: self.pause() if event.widget is root else None,
            add="+",
        )
        root.bind(
            "<Map>",
            lambda event: (
                self.refresh()
                if event.widget is root and not self.tick_id and not self.session.muted
                else None
            ),
            add="+",
        )
        self.refresh()

    def refresh(self):
        self.tick_id = None
        if not self.winfo_exists() or self.session.muted:
            return
        self.state = read_state(self.owner, self.component)
        self.show_note(self.session.choose(self.state))
        self.tick_id = self.after(1800, self.refresh)

    def show_note(self, note):
        if self.note != note:
            self.note = note
            self.heading.configure(text="帕秋莉 · " + note.title)
            self.message.configure(text=note.text)
        self.arrange()

    def turn(self, delta):
        if self.compact:
            self.force_expanded = True
            self.compact = False
            self.configure(height=86)
            self.canvas.configure(height=86)
            self.message.pack(fill="both", expand=True, padx=9, pady=(0, 3))
            note = self.session.choose(self.state)
        else:
            note = self.session.turn(self.state, delta)
        self.show_note(note)
        self.bounce(0)

    def bounce(self, index):
        if self.bounce_id:
            self.after_cancel(self.bounce_id)
            self.bounce_id = None
        if self.session.muted:
            return
        offsets = [0, -2, -4, -5, -4, -2, 0, 1, 0]
        self.canvas.coords(self.pet_id, self.pet_x, 4 + offsets[index])
        if index + 1 < len(offsets):
            self.bounce_id = self.after(35, lambda: self.bounce(index + 1))

    def press(self, event):
        self.drag_start = event.x_root
        self.start_x = self.pet_x
        self.moved = False

    def drag(self, event):
        if self.drag_start is not None:
            dx = event.x_root - self.drag_start
            self.moved |= abs(dx) > 6
            self.pet_x = max(0, min(self.winfo_width() - 80, self.start_x + dx))
            self.arrange()

    def release(self, event):
        if self.drag_start is None:
            return
        self.drag_start = None
        if self.moved:
            if self.pet_x < 28 or self.pet_x > self.winfo_width() - 108:
                self.set_quiet(True)
        elif self.session.muted:
            self.set_quiet(False)
        else:
            self.turn(1)

    def set_quiet(self, quiet):
        self.session.muted = quiet
        height = 32 if quiet else 44 if self.compact else 86
        self.configure(height=height)
        self.canvas.configure(height=height)
        self.canvas.itemconfigure(self.pet_id, image=self.head if quiet else self.body)
        if quiet:
            for attr in ("tick_id", "bounce_id"):
                identifier = getattr(self, attr)
                if identifier:
                    self.after_cancel(identifier)
                    setattr(self, attr, None)
            self.pet_x = (
                0 if self.pet_x < self.winfo_width() / 2 else self.winfo_width() - 80
            )
        else:
            self.refresh()
        self.arrange()

    def arrange(self):
        width = self.winfo_width()
        if width < 120:
            return
        self.pet_x = max(0, min(width - 80, self.pet_x))
        self.canvas.coords(self.pet_id, self.pet_x, 0 if self.session.muted else 4)
        bubble_width = min(820, width - 106)
        x = (
            self.pet_x + 86
            if self.pet_x + 86 + bubble_width < width
            else self.pet_x - bubble_width - 6
        )
        x = max(4, min(width - bubble_width - 4, x))
        overlaps = x < self.pet_x + 80 and x + bubble_width > self.pet_x
        self.canvas.itemconfigure(
            self.bubble_id,
            state="hidden" if self.session.muted or overlaps else "normal",
            width=bubble_width,
            height=36 if self.compact else 78,
        )
        self.canvas.coords(self.bubble_id, x, 3)
        self.message.configure(wraplength=max(100, bubble_width - 22))

    def pause(self):
        for attr in ("tick_id", "bounce_id"):
            identifier = getattr(self, attr)
            if identifier:
                try:
                    self.after_cancel(identifier)
                except tk.TclError:
                    pass
                setattr(self, attr, None)

    def resized(self, event):
        if event.widget is not self.owner.root or not self.winfo_exists():
            return
        compact = event.height < 820 and not self.force_expanded
        if compact != self.compact:
            self.compact = compact
            if compact:
                self.message.pack_forget()
            else:
                self.message.pack(fill="both", expand=True, padx=9, pady=(0, 3))
            if not self.session.muted:
                height = 44 if compact else 86
                self.configure(height=height)
                self.canvas.configure(height=height)
            self.arrange()

    def destroyed(self, event):
        if event.widget is self:
            self.pause()


def install_tk_guide(owner, component):
    owner.patchouli_guide = TkGuideShelf(owner, component)
    return owner.patchouli_guide
