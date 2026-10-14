"""Tk 的后台任务只操作队列，所有窗口更新在 Tk 主线程执行。"""

import queue
import threading


class TkTask:
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False

    def start(
        self, function, success, error, finished=lambda: None, message=lambda text: None
    ):
        if self.busy:
            return False
        self.busy = True
        self.cancel.clear()

        def worker():
            try:
                result = function(
                    self.cancel, lambda text: self.queue.put(("message", text))
                )
                self.queue.put(("success", result))
            except Exception as exc:
                self.queue.put(("error", str(exc)))

        def poll():
            try:
                while True:
                    kind, value = self.queue.get_nowait()
                    if kind == "message":
                        message(value)
                    else:
                        self.busy = False
                        try:
                            (success if kind == "success" else error)(value)
                        finally:
                            finished()
                        return
            except queue.Empty:
                self.root.after(60, poll)

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(60, poll)
        return True
