from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QMainWindow, QPushButton, QVBoxLayout, QWidget, QScrollArea)

from color_tool.ui import Job, STYLESHEET, card, label
from .registry import ROOT, TOOLS, get_tool
from .branding import mascot


class Hub(QMainWindow):
    def __init__(self, logs: Path | None = None, validated_report: dict | None = None):
        super().__init__()
        self.setWindowTitle("大图书馆 · 商品资料工具箱")
        self.setWindowIcon(QIcon(str(ROOT / "assets" / "toolbox.ico")))
        self.resize(1100, 880)
        self.setMinimumSize(950, 790)
        self.processes = {}
        self.log_files = {}
        self.buttons = {}
        self.tool_status = {}
        self.job = None
        from .shared_ai import PoolManager, PoolBroker

        self.ai_manager = PoolManager()
        self.ai_broker = PoolBroker(self.ai_manager)
        self._closing = False
        base = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation))
        self.logs = logs or (base / "GreatLibraryToolbox" / "logs")
        self.logs.mkdir(parents=True, exist_ok=True)
        self._build()
        from .guide_qt import install_qt_guide
        install_qt_guide(self, "hub", lambda _: self.configure_ai())
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_children)
        self.timer.start(750)
        from runtime_probe import validate_report

        if validated_report and validate_report(validated_report):
            self.environment_checked(validated_report)
        else:
            QTimer.singleShot(0, self.check_environment)

    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(24, 22, 24, 20)
        outer.setSpacing(18)
        header = QFrame()
        header.setObjectName("suiteHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(25, 20, 25, 20)
        titles = QVBoxLayout()
        titles.addWidget(label("大图书馆 · 商品资料工具箱", "suiteTitle"))
        titles.addWidget(label("帕秋莉的小工房，今天一起整理哪一摞？", "suiteSubtitle"))
        row.addLayout(titles)
        row.addStretch()
        row.addWidget(mascot(78))
        outer.addWidget(header)
        ai_card, ai_layout = card()
        ai_row = QHBoxLayout()
        ai_row.addWidget(label("通用 AI", "section"))
        self.ai_status = label("", "hint")
        ai_row.addWidget(self.ai_status, 1)
        configure = QPushButton("API / 模型 · 管理")
        self.configure = configure
        configure.clicked.connect(self.configure_ai)
        ai_row.addWidget(configure)
        ai_layout.addLayout(ai_row)
        outer.addWidget(ai_card)
        self.refresh_ai()
        lead = QHBoxLayout()
        lead.addWidget(label("工具", "section"))
        lead.addStretch()
        outer.addLayout(lead)
        grid = QGridLayout()
        grid.setSpacing(16)
        for index, tool in enumerate(TOOLS):
            frame, layout = card()
            frame.setMinimumHeight(202)
            heading = QHBoxLayout()
            heading.addWidget(mascot(58, tool.key))
            heading.addStretch()
            status = label("待检测", "hint")
            self.tool_status[tool.key] = status
            heading.addWidget(status)
            layout.addLayout(heading)
            layout.addWidget(label(tool.title, "toolTitle"))
            methods = {"color": "读取表格 → 配对颜色 → 输出图片", "matcher": "导入表格 → 关联 PID → 配色导出", "generator": "选择资料与主数据 → 生成 → 核对", "joiner": "加载图片 → 确认正反面 → 拼接", "listing": "选择目录 → 自动填写 → 保存草稿"}
            intro = label(tool.description, "hint")
            intro.setWordWrap(True)
            layout.addWidget(intro)
            layout.addStretch()
            button = QPushButton("打开工具  →")
            button.setObjectName("primary")
            button.setEnabled(False)
            button.clicked.connect(lambda _, key=tool.key: self.launch(key))
            self.buttons[tool.key] = button
            layout.addWidget(button)
            grid.addWidget(frame, index // 2, index % 2)
        if len(TOOLS) > 4:
            tools_widget = QWidget()
            tools_widget.setLayout(grid)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(tools_widget)
            outer.addWidget(scroll, 1)
        else:
            outer.addLayout(grid, 1)
        environment, env = card()
        line = QHBoxLayout()
        line.addWidget(label("运行环境", "section"))
        line.addStretch()
        self.check_button = QPushButton("重新检测")
        self.check_button.clicked.connect(self.check_environment)
        line.addWidget(self.check_button)
        logs = QPushButton("打开运行日志")
        logs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.logs))))
        line.addWidget(logs)
        env.addLayout(line)
        self.environment_status = label("正在检查工具的界面、表格和图片处理能力…", "hint")
        self.environment_status.setWordWrap(True)
        env.addWidget(self.environment_status)
        outer.addWidget(environment)
        self.footer = label("", "hint")
        outer.addWidget(self.footer)

    def check_environment(self):
        if self.job:
            return
        self.check_button.setEnabled(False)
        self.environment_status.setText("正在检查实际读写能力…")

        def work(*_):
            import json
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            args = [sys.executable, str(ROOT / "runtime_probe.py")]
            # 源码的无显示云实例仅检查 Tcl；Windows 始终实际创建 Tk 窗口。
            if os.name != "nt" and not os.environ.get("DISPLAY"):
                args.append("--no-gui")
            result = subprocess.run(args, cwd=ROOT, capture_output=True, timeout=120, creationflags=flags)
            if not result.stdout:
                raise RuntimeError(result.stderr.decode("utf-8", errors="replace")[-1500:] or "环境检测进程未返回结果。")
            report = json.loads(result.stdout.decode("utf-8"))
            if result.returncode != 0 and report.get("ready"):
                raise RuntimeError(f"检测进程未正常退出（{result.returncode}），不能标记环境就绪。")
            return report

        self.job = Job(work, self)
        self.job.result.connect(self.environment_checked)
        self.job.failed.connect(self.environment_failed)
        self.job.finished.connect(self.check_finished)
        self.job.start()

    def environment_checked(self, result):
        failures = [check for check in result["checks"] if not check["ok"]]
        if failures:
            self.environment_failed("；".join(item["name"] + "：" + item["detail"] for item in failures))
            return
        self.environment_status.setText(f"Python {result['version']}  ·  环境就绪")
        for key, button in self.buttons.items():
            button.setEnabled(True)
            self.tool_status[key].setText("运行中" if key in self.processes else "环境就绪")

    def environment_failed(self, message):
        self.environment_status.setText("检测未通过：" + message + "\n请重新运行“启动工具箱.exe”，安装器会检查并修复专用环境。")
        for key, button in self.buttons.items():
            button.setEnabled(False)
            self.tool_status[key].setText("环境待修复")

    def check_finished(self):
        job, self.job = self.job, None
        if job:
            job.deleteLater()
        self.check_button.setEnabled(True)

    def launch(self, key):
        running = self.processes.get(key)
        if running and running.poll() is None:
            self.footer.setText(get_tool(key).title + " 已打开，可从任务栏切换到它。")
            return
        path = self.logs / (key + ".log")
        stream = path.open("ab")
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
        try:
            environment = os.environ.copy()
            environment.update(self.ai_broker.environment())
            process = subprocess.Popen(
                [sys.executable, str(ROOT / "suite_entry.py"), "--tool", key],
                cwd=ROOT,
                stdout=stream,
                stderr=stream,
                creationflags=flags,
                env=environment,
                start_new_session=os.name != "nt",
            )
        finally:
            stream.close()
        self.processes[key] = process
        self.tool_status[key].setText("运行中 · 独立窗口")
        self.buttons[key].setText("已打开 · 查看任务栏")
        self.footer.setText(get_tool(key).title + " 已启动。其他入口仍可正常使用。")

    def poll_children(self):
        for key, process in list(self.processes.items()):
            code = process.poll()
            if code is None:
                continue
            del self.processes[key]
            self.buttons[key].setText("打开工具  →")
            self.tool_status[key].setText("环境就绪" if code == 0 else "已退出 · 查看日志")
            if code:
                self.footer.setText(get_tool(key).title + f" 异常退出（{code}），详情位于 {self.logs / (key + '.log')}")
        if self._closing and not self.processes:
            self.close()

    def configure_ai(self):
        from .ai_dialog import AISettingsDialog

        dialog = AISettingsDialog(self.ai_manager, self)
        dialog.exec()
        dialog.deleteLater()
        self.refresh_ai()

    def refresh_ai(self):
        data = self.ai_manager.snapshot()
        count = sum(data["model"] in entry["models"] for entry in data["entries"])
        self._guide_ai_ready = bool(data["model"] and count)
        if hasattr(self, "patchouli_guide"):
            self.patchouli_guide.refresh()
        self.ai_status.setText(
            f"{data['model'].removeprefix('models/')} · {count} 条 API 轮询"
            if count
            else "新增 API → 检测 → 选择模型"
        )

    def closeEvent(self, event):
        if self.job and self.job.isRunning():
            self.footer.setText("正在完成环境检测，请稍后关闭首页。")
            event.ignore()
        else:
            if any(process.poll() is None for process in self.processes.values()):
                self._closing = True
                self.hide()
                event.ignore()
            else:
                self.ai_broker.close()
                event.accept()


SUITE_STYLESHEET = STYLESHEET + """
QFrame#suiteHeader { background: #302341; border-radius: 12px; }
QLabel#suiteTitle { color: white; font-size: 26px; font-weight: 700; }
QLabel#suiteSubtitle { color: #c4cee2; }
QLabel#toolTitle { color: #302341; font-size: 20px; font-weight: 600; }
"""


def main(validated_runtime: str | None = None):
    from .branding import identify_component
    identify_component("hub")
    app = QApplication(sys.argv)
    app.setOrganizationName("GreatLibrary")
    app.setApplicationName("ProductToolbox")
    app.setStyle("Fusion")
    app.setStyleSheet(SUITE_STYLESHEET)
    report = None
    if validated_runtime:
        try:
            import base64, json
            report = json.loads(base64.b64decode(validated_runtime))
        except (ValueError, TypeError):
            pass
    hub = Hub(validated_report=report)
    hub.show()
    raise SystemExit(app.exec())
