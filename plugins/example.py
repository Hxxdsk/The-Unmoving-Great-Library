"""可直接运行的统一主题示例；默认不注册，不影响四个现有工具。"""
import sys
from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget
from color_tool.ui import STYLESHEET, label
from suite.branding import mascot


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    window = QMainWindow()
    window.setWindowTitle("大图书馆 · 扩展功能示例")
    window.resize(600, 400)
    body = QWidget()
    layout = QVBoxLayout(body)
    layout.addWidget(mascot(80))
    layout.addWidget(label("在这里实现新功能", "title"))
    layout.addStretch()
    window.setCentralWidget(body)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
