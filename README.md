# 大图书馆 · 商品资料工具箱

四个中文桌面工具统一入口、配色与字体，各工具在**独立进程**运行。可以同时打开多个入口；关闭首页后已经打开的工具仍继续运行。

![工具箱首页](docs/工具箱首页.png)

| 入口 | 功能 |
| --- | --- |
| 商品图片颜色配对 | Gemini 识色、人工连接，按商品编码复制原图；同颜色多尺码共用一张图 |
| 商品编码匹配工具 | 聚水潭款式与抖店 PID 关联，按颜色和尺码导出商家编码 |
| 送审资料生成器 | 根据主数据匹配颜色、尺码、分类与价格，整理面料并生成送审资料 |
| 送审拼接图 | 多文件夹加载、颜色与正反面排序、拖动调整、重命名和拼接 |

## Windows 使用

1. 下载仓库 `releases/大图书馆工具箱-Windows.zip`，**完整解压**。
2. 双击 **启动工具箱.exe**，或双击 `start.bat`。EXE 需与源码、资源放在一起。
3. 启动器自动检测环境，缺少依赖时内置下载并显示安装日志。完成后点击 **进入工具箱**。
4. 在首页选择入口，各工具独立工作。

**支持已有 Python 3.14，不设置版本上限，不固定第三方依赖版本。**是否可用以实际能力检测为准，pip 选择与解释器兼容的包。已在 Python 3.14.8 下验证；不能保证每个历史或未来的 Python 都有对应依赖，因此不以版本号替代能力检查。

原生 EXE 本身不依赖 Python、Qt、Go 或 .NET。提供 **Windows 10/11 x64** 可执行文件与完整源码。EXE 已交叉编译并检查 Windows GUI 格式；当前验证机器是 Linux，尚未在真实 Windows 电脑运行安装流程。

## 环境检测与内置下载

检查源码语法、Tk 和 Qt 窗口、PNG/JPEG 读写、Excel 读写、pandas 处理、旧 XLS 支持及四个入口。检测进程正常退出、全部项目通过后才显示就绪。

- 优先使用已有且可运行的环境，通过时无需安装。
- 缺少依赖时，在 `%LOCALAPPDATA%/GreatLibraryToolbox/envs/` 准备专用虚拟环境，不向现有系统 Python 安装第三方包。
- 没有可用 Python 时，从官方 Windows 下载页选择最新稳定版 x64 安装包，通过官方 HTTPS 下载并验证 **Windows Authenticode 签名及信任链**，再安装到工具箱目录。
- 依赖通过官方 PyPI 安装，不关闭 TLS 或签名验证。
- 失败显示具体日志，可重新检测；失败不会被标记为就绪。
- 再次启动会验证已有环境；环境正常可以离线进入首页，Gemini 识别仍需联网。

依赖：`PySide6`、`openpyxl`、`Pillow`、`pandas`、`xlrd`。Tk 随 Python 提供，不用 pip 安装。使用官方完整 Qt 包，由官方安装流程提供模块初始化与插件。

安装器支持环境变量代理及 Windows 手动代理。下载需要 `www.python.org`、`pypi.org`、`files.pythonhosted.org`；Gemini 需要 `generativelanguage.googleapis.com`。Windows 信任链检查还可能访问证书发布机构的验证服务。

## 原有工具流程

**商品图片颜色配对：**详见 [颜色配对说明](docs/颜色配对工具.md)。选择含商品资料表格与供应商款号文件夹的目录；每个文件夹一张名为 `颜色` 的图片及商品原图。输入 Key 检测模型并恢复上次选择，Key 仅存在本次内存中。AI 比较主体颜色，不按文件序号猜测；名称不同或不确定时由用户选择。结果写入 `_商品编码图片/新批次/款号/`，保留原图。

**商品编码匹配：**选择聚水潭和抖店表格 → 关联款式与 PID → 配对颜色 → 结合尺码导出商家编码。保留共享 PID、未配对颜色跳过和歧义检查。

**送审资料生成：**输入列为 `款号、款式、尺码、颜色、上下装、面料、成本`。主数据必须有 `新品类层级编码及标费档次、尺码表、颜色` 工作表。原默认主数据路径 `D:\GZ\预输入.xlsx` 保留，可改选。用户确认缺失颜色后，会追加到所选主数据并重新生成输出，保留原业务行为。

**送审拼接图：**加载文件夹、自动排序，必要时拖动或调整正反面，最后重命名或拼接。原图重命名是此入口原有功能，点击相应操作才执行。保留 800×800 画布与原命名，输出到第一个文件夹的 `处理后` 目录。

三个上传脚本逐字节保存在 `original_scripts/`；入口层统一主题，不改写业务函数。四个 GUI、变量、工作线程与日志独立。

## 目录与日志

```text
启动工具箱.exe         # 原生启动与环境安装
suite_entry.py         # 首页和四个独立入口
runtime_probe.py       # 实际能力检查
requirements.txt       # 不固定版本的依赖
suite/                # 首页、入口、主题
color_tool/           # Gemini 图片配对
original_scripts/     # 三个原始脚本
assets/               # 统一图标
launcher/             # 原生 EXE 的 Go 源码
tests/                # 功能与界面测试
docs/                 # 说明、界面预览和验证记录
releases/             # 完整分发 ZIP
```

Windows 日志在 `%LOCALAPPDATA%/GreatLibraryToolbox/logs/`。安装器写 `setup.log` / `toolbox.log`；独立入口写 `color.log`、`matcher.log`、`generator.log`、`joiner.log`。首页可打开日志目录。

## 源码开发与验证

```bash
python -m venv .venv
# Windows 使用 .venv\Scripts\python
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python suite_entry.py

# 单独启动入口：color / matcher / generator / joiner
.venv/bin/python suite_entry.py --tool color

.venv/bin/python -m pip install pytest
DISPLAY=:99 QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/python runtime_probe.py
```

Linux 的 Tk 工具需要图形显示，云环境可用 Xvfb。无显示时 `runtime_probe.py --no-gui` 仅检查 Tcl，Tk 界面测试会明确跳过，不能算作窗口验证。

## 重建 Windows EXE

终端用户不需要 Go。开发者安装 Go 后双击 `build_windows.bat`，或交叉编译：

```bash
cd launcher
go test ./...
GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags '-H=windowsgui -s -w' -o ../启动工具箱.exe .
```

已提供图标和 manifest 的 `resources_windows_amd64.syso`。修改图标可用 `github.com/akavel/rsrc` 再生成资源。manifest 采用普通用户权限。

验证见 [验证记录](docs/验证记录.md)。真实 Gemini 效果需自己的 Key 和原图，模拟测试不证明真实识别准确率。
