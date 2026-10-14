//go:build windows

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"syscall"
	"time"
	"unsafe"
)

var user32 = syscall.NewLazyDLL("user32.dll")
var gdi32 = syscall.NewLazyDLL("gdi32.dll")
var kernel32 = syscall.NewLazyDLL("kernel32.dll")
var createWindow = user32.NewProc("CreateWindowExW")
var sendMessage = user32.NewProc("SendMessageW")
var drawText = user32.NewProc("DrawTextW")
var fillRect = user32.NewProc("FillRect")
var setTextColor = gdi32.NewProc("SetTextColor")
var setBkMode = gdi32.NewProc("SetBkMode")
var selectObject = gdi32.NewProc("SelectObject")
var deleteObject = gdi32.NewProc("DeleteObject")
var enableWindow = user32.NewProc("EnableWindow")
var invalidate = user32.NewProc("InvalidateRect")

type rect struct{ Left, Top, Right, Bottom int32 }
type point struct{ X, Y int32 }
type message struct {
	Window         uintptr
	Message        uint32
	WParam, LParam uintptr
	Time           uint32
	Point          point
	Private        uint32
}
type windowClass struct {
	Style                              uint32
	Procedure                          uintptr
	ClassExtra, WindowExtra            int32
	Instance, Icon, Cursor, Background uintptr
	Menu, Name                         *uint16
}
type paintInfo struct {
	DC              uintptr
	Erase           int32
	Rect            rect
	Restore, Update int32
	Reserved        [32]byte
}
type drawItem struct {
	Type, ID, ItemID, Action, State uint32
	Window, DC                      uintptr
	Rect                            rect
	Data                            uintptr
}

type setupState struct {
	mu        sync.Mutex
	busy      bool
	python    string
	step      int
	percent   int
	status    string
	lines     []string
	serial    int
	cancel    context.CancelFunc
	installer *installer
	reveal    bool
	shown     bool
	autoEnter bool
}

var state = setupState{status: "正在准备环境检测…"}
var hwnd, logWindow, startButton, cancelButton, font, titleFont, headingFont, backgroundBrush, whiteBrush uintptr
var lastLogSerial int
var appIcon uintptr

func wide(text string) *uint16 { p, _ := syscall.UTF16PtrFromString(text); return p }
func color(hex string) uintptr {
	var r, g, b uint32
	fmt.Sscanf(hex, "#%02x%02x%02x", &r, &g, &b)
	return uintptr(r | (g << 8) | (b << 16))
}
func brush(hex string) uintptr {
	h, _, _ := gdi32.NewProc("CreateSolidBrush").Call(color(hex))
	return h
}
func newFont(height int, weight int) uintptr {
	h, _, _ := gdi32.NewProc("CreateFontW").Call(uintptr(int32(-height)), 0, 0, 0, uintptr(weight), 0, 0, 0, 1, 0, 0, 5, 0, uintptr(unsafe.Pointer(wide("Microsoft YaHei UI"))))
	return h
}
func text(dc uintptr, value string, bounds rect, fontHandle uintptr, hex string, flags uintptr) {
	old, _, _ := selectObject.Call(dc, fontHandle)
	setTextColor.Call(dc, color(hex))
	setBkMode.Call(dc, 1)
	drawText.Call(dc, uintptr(unsafe.Pointer(wide(value))), ^uintptr(0), uintptr(unsafe.Pointer(&bounds)), flags|0x800)
	selectObject.Call(dc, old)
}
func rounded(dc uintptr, bounds rect, hex string, radius int32) {
	b := brush(hex)
	old, _, _ := selectObject.Call(dc, b)
	pen, _, _ := gdi32.NewProc("GetStockObject").Call(8)
	oldPen, _, _ := selectObject.Call(dc, pen)
	gdi32.NewProc("RoundRect").Call(dc, uintptr(bounds.Left), uintptr(bounds.Top), uintptr(bounds.Right), uintptr(bounds.Bottom), uintptr(radius), uintptr(radius))
	selectObject.Call(dc, old)
	selectObject.Call(dc, oldPen)
	deleteObject.Call(b)
}
func newControl(class, title string, style uintptr, x, y, w, h int, id int) uintptr {
	instance, _, _ := kernel32.NewProc("GetModuleHandleW").Call(0)
	control, _, _ := createWindow.Call(0, uintptr(unsafe.Pointer(wide(class))), uintptr(unsafe.Pointer(wide(title))), style|0x50000000,
		uintptr(x), uintptr(y), uintptr(w), uintptr(h), hwnd, uintptr(id), instance, 0)
	sendMessage.Call(control, 0x30, font, 1)
	return control
}

func updateLog(line string) {
	state.mu.Lock()
	defer state.mu.Unlock()
	line = safeLog(line)
	state.lines = append(state.lines, time.Now().Format("15:04:05")+"  "+line)
	if len(state.lines) > 500 {
		state.lines = state.lines[len(state.lines)-500:]
	}
	state.serial++
	if state.installer != nil {
		path := filepath.Join(state.installer.cache, "logs")
		os.MkdirAll(path, 0755)
		if file, err := os.OpenFile(filepath.Join(path, "setup.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644); err == nil {
			fmt.Fprintln(file, line)
			file.Close()
		}
	}
}

func beginSetup() {
	state.mu.Lock()
	if state.busy {
		state.mu.Unlock()
		return
	}
	ctx, cancel := context.WithCancel(context.Background())
	state.busy = true
	state.python = ""
	state.cancel = cancel
	state.mu.Unlock()
	go func() {
		python, err := state.installer.prepare(ctx)
		state.mu.Lock()
		state.busy = false
		state.cancel = nil
		if err != nil {
			state.reveal = true
			if ctx.Err() != nil {
				state.status = "已取消，可重新检测。"
			} else {
				state.status = "准备未完成，请查看日志并重试。"
			}
		} else {
			state.python = python
			state.autoEnter = true
		}
		state.mu.Unlock()
		if err != nil {
			updateLog("未完成：" + err.Error())
		} else {
			updateLog("环境就绪，正在进入工具箱…")
		}
	}()
}

func drawMain(dc uintptr) {
	state.mu.Lock()
	stage, pct, status := state.step, state.percent, state.status
	state.mu.Unlock()
	bounds := rect{0, 0, 940, 730}
	fillRect.Call(dc, uintptr(unsafe.Pointer(&bounds)), backgroundBrush)
	rounded(dc, rect{24, 22, 920, 118}, "#302341", 18)
	if appIcon != 0 {
		user32.NewProc("DrawIconEx").Call(dc, 43, 34, appIcon, 72, 72, 0, 0, 3)
	}
	text(dc, "大图书馆 · 商品资料工具箱", rect{134, 38, 885, 75}, titleFont, "#ffffff", 0)
	text(dc, "环境检测  →  补齐缺失运行库  →  打开工具箱", rect{135, 82, 880, 108}, font, "#e5d8f3", 0)
	text(dc, "准备运行环境", rect{28, 139, 902, 170}, headingFont, "#302341", 0)
	labels := []string{"01  发现 Python", "02  能力检测", "03  下载与修复", "04  进入工具箱"}
	for n, name := range labels {
		left := int32(24 + n*228)
		bg, fg := "#ffffff", "#6d7a8f"
		if n == stage {
			bg, fg = "#eae2f5", "#8365bb"
		} else if n < stage {
			fg = "#168b78"
		}
		rounded(dc, rect{left, 188, left + 212, 239}, bg, 14)
		text(dc, name, rect{left + 14, 199, left + 206, 232}, font, fg, 0)
	}
	rounded(dc, rect{24, 254, 920, 340}, "#ffffff", 14)
	text(dc, status, rect{43, 268, 899, 302}, font, "#302341", 0x10)
	rounded(dc, rect{43, 315, 900, 322}, "#e6ebf4", 6)
	if pct > 0 {
		rounded(dc, rect{43, 315, 43 + int32(857*pct/100), 322}, "#8365bb", 6)
	}
	text(dc, "处理日志", rect{26, 354, 891, 381}, headingFont, "#302341", 0)
}

func drawButton(item *drawItem) {
	state.mu.Lock()
	busy, ready := state.busy, state.python != ""
	state.mu.Unlock()
	caption, bg, fg := "重新检测 / 修复", "#8365bb", "#ffffff"
	if item.ID == 100 {
		if ready {
			caption = "进入工具箱  →"
		} else if busy {
			caption, bg, fg = "正在准备…", "#e6eaf3", "#9aa6bc"
		}
	} else {
		caption, bg, fg = "取消", "#ffffff", "#302341"
		if !busy {
			caption = "关闭"
		}
	}
	if item.State&1 != 0 {
		bg, fg = "#6e51a3", "#ffffff"
	}
	rounded(item.DC, item.Rect, bg, 12)
	text(item.DC, caption, item.Rect, font, fg, 0x1|0x4|0x20)
}

func windowProc(window uintptr, event uint32, wParam, lParam uintptr) uintptr {
	switch event {
	case 0x1: // WM_CREATE
		hwnd = window
		logWindow = newControl("EDIT", "", 0x00200000|0x4|0x40|0x800, 26, 384, 893, 194, 103)
		sendMessage.Call(logWindow, 0xcf, 1, 0)
		startButton = newControl("BUTTON", "开始检测", 0xb|0x10000, 690, 589, 227, 42, 100)
		cancelButton = newControl("BUTTON", "取消", 0xb|0x10000, 550, 589, 122, 42, 101)
		user32.NewProc("SetTimer").Call(window, 1, 200, 0)
		beginSetup()
		return 0
	case 0xf: // WM_PAINT
		var info paintInfo
		dc, _, _ := user32.NewProc("BeginPaint").Call(window, uintptr(unsafe.Pointer(&info)))
		drawMain(dc)
		user32.NewProc("EndPaint").Call(window, uintptr(unsafe.Pointer(&info)))
		return 0
	case 0x2b: // WM_DRAWITEM
		// Windows 提供原生结构地址；复制到 Go 结构后使用，不把整数地址转为 Go 指针。
		var item drawItem
		var bytesRead uintptr
		size := unsafe.Sizeof(item)
		ok, _, _ := kernel32.NewProc("ReadProcessMemory").Call(^uintptr(0), lParam,
			uintptr(unsafe.Pointer(&item)), size, uintptr(unsafe.Pointer(&bytesRead)))
		if ok != 0 && bytesRead == size {
			drawButton(&item)
			return 1
		}
		return 0
	case 0x138: // WM_CTLCOLORSTATIC (readonly edit)
		setTextColor.Call(wParam, color("#302341"))
		gdi32.NewProc("SetBkColor").Call(wParam, color("#ffffff"))
		return whiteBrush
	case 0x113: // WM_TIMER
		state.mu.Lock()
		busy, serial, logs := state.busy, state.serial, strings.Join(state.lines, "\r\n")
		reveal := state.reveal && !state.shown
		if reveal {
			state.shown = true
		}
		launchNow := state.autoEnter && !busy && state.python != ""
		python := state.python
		if launchNow {
			state.autoEnter = false
		}
		state.mu.Unlock()
		if reveal {
			user32.NewProc("ShowWindow").Call(window, 1)
			user32.NewProc("UpdateWindow").Call(window)
		}
		if serial != lastLogSerial {
			user32.NewProc("SetWindowTextW").Call(logWindow, uintptr(unsafe.Pointer(wide(logs))))
			units, _ := syscall.UTF16FromString(logs)
			end := uintptr(len(units) - 1)
			sendMessage.Call(logWindow, 0xb1, end, end)
			sendMessage.Call(logWindow, 0xb7, 0, 0)
			lastLogSerial = serial
		}
		enabled := uintptr(1)
		if busy {
			enabled = 0
		}
		enableWindow.Call(startButton, enabled)
		invalidate.Call(window, 0, 0)
		invalidate.Call(startButton, 0, 0)
		invalidate.Call(cancelButton, 0, 0)
		if launchNow {
			if err := state.installer.launch(python); err != nil {
				updateLog("启动失败：" + err.Error())
				state.mu.Lock()
				state.reveal = true
				state.mu.Unlock()
			} else {
				user32.NewProc("DestroyWindow").Call(window)
			}
		}
		return 0
	case 0x111: // WM_COMMAND
		switch wParam & 0xffff {
		case 100:
			state.mu.Lock()
			python := state.python
			state.mu.Unlock()
			if python == "" {
				beginSetup()
			} else {
				if err := state.installer.launch(python); err != nil {
					updateLog("启动失败：" + err.Error())
				} else {
					user32.NewProc("DestroyWindow").Call(window)
				}
			}
		case 101:
			state.mu.Lock()
			busy, cancel := state.busy, state.cancel
			state.mu.Unlock()
			if busy && cancel != nil {
				cancel()
				updateLog("正在取消，等待当前操作结束…")
			} else {
				user32.NewProc("DestroyWindow").Call(window)
			}
		}
		return 0
	case 0x10: // WM_CLOSE
		state.mu.Lock()
		busy, cancel := state.busy, state.cancel
		state.mu.Unlock()
		if busy && cancel != nil {
			cancel()
			updateLog("正在结束准备工作，请稍后再关闭。")
			return 0
		}
		user32.NewProc("DestroyWindow").Call(window)
		return 0
	case 0x2:
		user32.NewProc("PostQuitMessage").Call(0)
		return 0
	}
	result, _, _ := user32.NewProc("DefWindowProcW").Call(window, uintptr(event), wParam, lParam)
	return result
}

func main() {
	runtime.LockOSThread()
	mutex, _, mutexErr := kernel32.NewProc("CreateMutexW").Call(0, 0, uintptr(unsafe.Pointer(wide(`Local\GreatLibraryToolboxSetup`))))
	if mutexErr == syscall.Errno(183) {
		user32.NewProc("MessageBoxW").Call(0, uintptr(unsafe.Pointer(wide("工具箱正在启动，请稍候。"))), uintptr(unsafe.Pointer(wide("大图书馆工具箱"))), 0)
		return
	}
	defer kernel32.NewProc("CloseHandle").Call(mutex)
	file, err := os.Executable()
	if err != nil {
		return
	}
	cache, err := os.UserCacheDir()
	if err != nil {
		return
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	system := systemProxy()
	transport.Proxy = func(request *http.Request) (*url.URL, error) {
		proxy, err := http.ProxyFromEnvironment(request)
		if proxy != nil || err != nil {
			return proxy, err
		}
		return system, nil
	}
	client := &http.Client{Transport: transport, Timeout: 5 * time.Minute, CheckRedirect: func(request *http.Request, via []*http.Request) error {
		if request.URL.Scheme != "https" || request.URL.Hostname() != "www.python.org" {
			return fmt.Errorf("拒绝转向非官方 HTTPS 下载源")
		}
		if len(via) > 5 {
			return fmt.Errorf("下载重定向过多")
		}
		return nil
	}}
	state.installer = &installer{root: filepath.Dir(file), cache: filepath.Join(cache, "GreatLibraryToolbox"), client: client, log: updateLog,
		step: func(step int, status string, pct int) {
			state.mu.Lock()
			state.step = step
			state.status = status
			state.percent = pct
			state.mu.Unlock()
		}, needsSetup: func() { state.mu.Lock(); state.reveal = true; state.mu.Unlock() }}
	// CI 用原生 Windows 二进制调用相同检测流程，不创建安装器窗口。
	if len(os.Args) == 3 && os.Args[1] == "--check-only" {
		python, checkErr := state.installer.prepare(context.Background())
		result := map[string]any{"ready": checkErr == nil, "python": python}
		if checkErr != nil {
			result["error"] = checkErr.Error()
		}
		data, _ := json.MarshalIndent(result, "", "  ")
		writeErr := os.WriteFile(os.Args[2], data, 0644)
		if checkErr != nil || writeErr != nil {
			os.Exit(1)
		}
		return
	}
	font = newFont(16, 400)
	titleFont = newFont(28, 700)
	headingFont = newFont(18, 600)
	backgroundBrush = brush("#f5f2fa")
	whiteBrush = brush("#ffffff")
	defer func() {
		for _, handle := range []uintptr{font, titleFont, headingFont, backgroundBrush, whiteBrush} {
			deleteObject.Call(handle)
		}
	}()
	instance, _, _ := kernel32.NewProc("GetModuleHandleW").Call(0)
	cursor, _, _ := user32.NewProc("LoadCursorW").Call(0, 32512)
	icon, _, _ := syscall.NewLazyDLL("shell32.dll").NewProc("ExtractIconW").Call(instance, uintptr(unsafe.Pointer(wide(file))), 0)
	if icon > 1 {
		defer user32.NewProc("DestroyIcon").Call(icon)
	} else {
		icon = 0
	}
	appIcon = icon
	largeIcon, _, _ := user32.NewProc("LoadImageW").Call(instance, 1, 1, 72, 72, 0)
	if largeIcon != 0 {
		appIcon = largeIcon
		defer user32.NewProc("DestroyIcon").Call(largeIcon)
	}
	class := windowClass{Style: 3, Procedure: syscall.NewCallback(windowProc), Instance: instance, Icon: icon, Cursor: cursor, Background: backgroundBrush, Name: wide("GreatLibrarySetupWindow")}
	if result, _, _ := user32.NewProc("RegisterClassW").Call(uintptr(unsafe.Pointer(&class))); result == 0 {
		return
	}
	style := uintptr(0x00cf0000 &^ 0x00040000 &^ 0x00010000)
	window, _, _ := createWindow.Call(0, uintptr(unsafe.Pointer(class.Name)), uintptr(unsafe.Pointer(wide("大图书馆工具箱 · 启动与环境准备"))), style, 120, 80, 960, 730, 0, 0, instance, 0)
	if window == 0 {
		return
	}
	// 先隐藏检测；只有确认需要安装或遇到错误才显示准备界面。
	var msg message
	for {
		result, _, _ := user32.NewProc("GetMessageW").Call(uintptr(unsafe.Pointer(&msg)), 0, 0, 0)
		if int32(result) <= 0 {
			break
		}
		user32.NewProc("TranslateMessage").Call(uintptr(unsafe.Pointer(&msg)))
		user32.NewProc("DispatchMessageW").Call(uintptr(unsafe.Pointer(&msg)))
	}
}
