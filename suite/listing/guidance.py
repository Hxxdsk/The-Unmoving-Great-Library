"""User-guided browser controls. Browser calls remain in the task thread."""

from __future__ import annotations

import secrets
import re
import threading
from pathlib import Path
from urllib.parse import urlparse

from .models import ListingError, Cancelled
from .workflow import resolve
from .page_ai import FORBIDDEN

SCRIPT = Path(__file__).with_name("human.js").read_text(encoding="utf-8")


class UserGuide:
    def __init__(self, cancel, notify=lambda *_: None, finished=lambda: None):
        self.cancel, self.notify, self.finished = cancel, notify, finished
        self.resume, self.skip = threading.Event(), threading.Event()
        self.active = None
        self.events = []
        self.bound = set()
        self.binding = "__glHuman_" + secrets.token_hex(8)

    def event(self, source, data):
        if (
            not isinstance(data, dict)
            or data.get("token") != self.active
            or source.get("frame") != source["page"].main_frame
        ):
            return
        url = urlparse(data.get("url", ""))
        if (
            url.hostname != "fxg.jinritemai.com"
            or re.search(r"/login|/passport|/verify|/captcha", url.path, re.I)
            or data.get("kind")
            not in (
                "hover",
                "click",
                "blocked",
            )
        ):
            return
        if isinstance(data.get("target"), dict) and len(self.events) < 48:
            self.events.append({"kind": data["kind"], "target": data["target"]})

    def install(self, page, mode):
        url = urlparse(page.url)
        if url.hostname != "fxg.jinritemai.com" or re.search(
            r"/login|/passport|/verify|/captcha", url.path, re.I
        ):
            return
        if page not in self.bound:
            page.expose_binding(self.binding, self.event)
            self.bound.add(page)
        page.evaluate(
            SCRIPT, {"token": self.active, "mode": mode, "binding": self.binding}
        )

    def run(
        self,
        page,
        goal,
        mode="pick",
        condition=None,
        observe=None,
        root=None,
        allow_save=False,
    ):
        self.events, self.active = [], secrets.token_hex(16)
        self.resume.clear()
        self.skip.clear()
        current, installed = page, []
        self.notify(goal, mode)
        try:
            while True:
                if self.cancel.is_set():
                    raise Cancelled("已停止用户指导")
                if self.skip.is_set():
                    raise ListingError("用户跳过了当前款的指导步骤")
                if observe:
                    current = observe() or current
                if current not in installed or not current.evaluate(
                    "() => { window.__glHumanLease = Date.now() + 3000; return Boolean(window.__glHumanStop); }"
                ):
                    self.install(current, mode)
                    if current not in installed:
                        installed.append(current)
                if mode == "pick" and any(e["kind"] == "click" for e in self.events):
                    last = next(
                        e for e in reversed(self.events) if e["kind"] == "click"
                    )
                    target = resolve(current, last["target"], root, allow_save)
                    if target is None:
                        self.events.clear()
                        self.notify("所选控件已变化，请重新点击：" + goal, mode)
                    else:
                        # The click was intercepted; the caller performs it exactly once.
                        return target, self.recipe(target_only=True)
                if mode == "record" and condition and condition():
                    return None, self.recipe()
                if self.resume.is_set():
                    self.resume.clear()
                    self.notify("当前页面还未到达所需步骤，请继续操作：" + goal, mode)
                current.wait_for_timeout(100)
        finally:
            self.active = None
            for item in installed:
                try:
                    if not item.is_closed():
                        item.evaluate(SCRIPT, {"stop": True})
                except Exception:
                    pass
            self.finished()

    def recipe(self, target_only=False):
        events = self.events
        if target_only and any(e["kind"] == "click" for e in events):
            end = max(i for i, e in enumerate(events) if e["kind"] == "click")
            events = events[: end + 1]
        actions = [
            dict(e)
            for e in events
            if e["kind"] in ("hover", "click")
            and not FORBIDDEN.search(re.sub(r"\s+", "", e["target"].get("text", "")))
            and (target_only or "保存草稿" not in e["target"].get("text", ""))
        ]
        # Keep only recent meaningful hovers plus clicks; no coordinates or input data.
        if target_only and actions:
            actions[-1]["kind"] = "target"
        unique = []
        for action in actions[-24:]:
            if not unique or unique[-1] != action:
                unique.append(action)
        return unique
