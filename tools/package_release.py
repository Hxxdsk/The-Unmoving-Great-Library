"""生成源码 + 原生 EXE 的完整分发 ZIP，不包含 Git 元数据或用户数据。"""

import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIRECTORIES = ("suite", "color_tool", "launcher", "original_scripts", "assets", "docs", "tests", "tools", ".github")
ROOT_FILES = ("README.md", "requirements.txt", "app.py", "suite_entry.py", "runtime_probe.py",
              "start.bat", "build_windows.bat", "启动工具箱.exe", ".gitignore")


def main():
    output = ROOT / "releases" / "大图书馆工具箱-Windows.zip"
    output.parent.mkdir(exist_ok=True)
    files = [ROOT / name for name in ROOT_FILES]
    for directory in DIRECTORIES:
        files.extend(path for path in (ROOT / directory).rglob("*") if path.is_file()
                     and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".log"})
    hashes = {}
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(files):
            relative = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            if path.suffix == ".bat":
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            archive.writestr("大图书馆工具箱/" + relative, data)
            hashes[relative] = hashlib.sha256(data).hexdigest()
        archive.writestr("大图书馆工具箱/SHA256SUMS.json", json.dumps(hashes, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        for relative, expected in hashes.items():
            assert hashlib.sha256(archive.read("大图书馆工具箱/" + relative)).hexdigest() == expected
    print(f"{output}\n{len(hashes)} 个文件，{output.stat().st_size:,} 字节，CRC 与 SHA256 检查通过。")


if __name__ == "__main__":
    main()
