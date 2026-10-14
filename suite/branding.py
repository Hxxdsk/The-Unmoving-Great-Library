from pathlib import Path
import os

ASSETS = Path(__file__).resolve().parent.parent / "assets"


def identify_component(component):
    if os.name == "nt":
        import ctypes
        function = ctypes.WinDLL("shell32").SetCurrentProcessExplicitAppUserModelID
        function.argtypes = [ctypes.c_wchar_p]
        function.restype = ctypes.c_long
        function("GreatLibrary.Toolbox." + component)


def mascot(size=72, component="hub"):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QLabel
    widget = QLabel()
    path = ASSETS / ("logo-" + component + ".png")
    if not path.exists():
        path = ASSETS / "patchouli.png"
    widget.setPixmap(QPixmap(str(path)).scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
    widget.setFixedSize(size, size)
    return widget
