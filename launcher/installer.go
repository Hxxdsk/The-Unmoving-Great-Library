package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

type checkItem struct {
	Name   string `json:"name"`
	OK     bool   `json:"ok"`
	Detail string `json:"detail"`
}
type report struct {
	Ready   bool        `json:"ready"`
	Checks  []checkItem `json:"checks"`
	Python  string      `json:"python"`
	Version string      `json:"version"`
}
type interpreter struct {
	Executable string `json:"executable"`
	Version    []int  `json:"version"`
}
type installer struct {
	root   string
	cache  string
	client *http.Client
	log    func(string)
	step   func(int, string, int)
}

func safeLog(value string) string {
	pattern := regexp.MustCompile(`(?i)(https?://)[^/\s@]+@`)
	return pattern.ReplaceAllString(value, "$1[认证已隐藏]@")
}

func (i *installer) run(ctx context.Context, executable string, args ...string) ([]byte, error) {
	cmd := exec.CommandContext(ctx, executable, args...)
	cmd.Dir = i.root
	cmd.Env = append(os.Environ(), "PYTHONUTF8=1", "PYTHONIOENCODING=utf-8")
	hideConsole(cmd)
	output, err := cmd.CombinedOutput()
	if err != nil {
		detail := string(output)
		if len(detail) > 5000 {
			detail = detail[len(detail)-5000:]
		}
		return output, fmt.Errorf("%w\n%s", err, safeLog(detail))
	}
	return output, nil
}

func (i *installer) stream(ctx context.Context, executable string, args ...string) error {
	cmd := exec.CommandContext(ctx, executable, args...)
	cmd.Dir = i.root
	cmd.Env = append(os.Environ(), "PYTHONUTF8=1", "PYTHONIOENCODING=utf-8")
	hideConsole(cmd)
	writer := &logWriter{log: i.log}
	cmd.Stdout, cmd.Stderr = writer, writer
	err := cmd.Run()
	writer.flush()
	return err
}

type logWriter struct {
	pending string
	log     func(string)
}

func (w *logWriter) Write(data []byte) (int, error) {
	w.pending += string(data)
	for {
		at := strings.IndexAny(w.pending, "\r\n")
		if at < 0 {
			break
		}
		line := strings.TrimSpace(w.pending[:at])
		w.pending = w.pending[at+1:]
		if line != "" {
			w.log(safeLog(line))
		}
	}
	return len(data), nil
}
func (w *logWriter) flush() {
	if strings.TrimSpace(w.pending) != "" {
		w.log(safeLog(strings.TrimSpace(w.pending)))
	}
	w.pending = ""
}

func (i *installer) inspect(ctx context.Context, executable string, prefix ...string) (interpreter, error) {
	code := "import json,sys,tkinter,venv;from pathlib import Path;assert hasattr(Path,'is_relative_to');print(json.dumps({'executable':sys.executable,'version':list(sys.version_info[:3])}))"
	args := append(append([]string{}, prefix...), "-c", code)
	checkCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	data, err := i.run(checkCtx, executable, args...)
	var result interpreter
	if err == nil {
		err = json.Unmarshal(data, &result)
	}
	return result, err
}

func (i *installer) candidates(ctx context.Context) []interpreter {
	var found []interpreter
	seen := map[string]bool{}
	add := func(executable string, args ...string) {
		if strings.Contains(strings.ToLower(executable), `\windowsapps\`) {
			return
		}
		info, err := i.inspect(ctx, executable, args...)
		if err == nil && info.Executable != "" && !seen[strings.ToLower(info.Executable)] {
			seen[strings.ToLower(info.Executable)] = true
			found = append(found, info)
		}
	}
	cached, _ := filepath.Glob(filepath.Join(i.cache, "envs", "*", "Scripts", "python.exe"))
	for _, executable := range cached {
		add(executable)
	}
	if py, err := exec.LookPath("py"); err == nil {
		add(py, "-3")
		listCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
		data, _ := i.run(listCtx, py, "-0p")
		cancel()
		pattern := regexp.MustCompile(`(?im)[a-z]:\\[^\r\n]+?python(?:w)?\.exe`)
		for _, name := range pattern.FindAllString(string(data), -1) {
			add(strings.TrimSpace(name))
		}
	}
	for _, name := range []string{"python", "python3"} {
		if executable, err := exec.LookPath(name); err == nil {
			add(executable)
		}
	}
	managed, _ := filepath.Glob(filepath.Join(i.cache, "python", "*", "python.exe"))
	for _, executable := range managed {
		add(executable)
	}
	// 专用可用环境优先，其余按实际 Python 版本排序，不设置版本上限。
	sort.SliceStable(found, func(a, b int) bool {
		ac := strings.HasPrefix(strings.ToLower(found[a].Executable), strings.ToLower(filepath.Join(i.cache, "envs")))
		bc := strings.HasPrefix(strings.ToLower(found[b].Executable), strings.ToLower(filepath.Join(i.cache, "envs")))
		if ac != bc {
			return ac
		}
		return compareVersions(found[a].Version, found[b].Version) > 0
	})
	return found
}

func compareVersions(a, b []int) int {
	for n := 0; n < len(a) && n < len(b); n++ {
		if a[n] > b[n] {
			return 1
		}
		if a[n] < b[n] {
			return -1
		}
	}
	return 0
}

func (i *installer) health(ctx context.Context, python string) (report, error) {
	checkCtx, cancel := context.WithTimeout(ctx, 120*time.Second)
	defer cancel()
	data, commandErr := i.run(checkCtx, python, filepath.Join(i.root, "runtime_probe.py"))
	result, err := parseHealthOutput(data, commandErr)
	if err != nil {
		return result, err
	}
	for _, item := range result.Checks {
		if item.OK {
			i.log("✓ " + item.Name + " · " + item.Detail)
		} else {
			i.log("待修复 · " + item.Name + "：" + item.Detail)
		}
	}
	return result, nil
}

func parseHealthOutput(data []byte, commandErr error) (report, error) {
	var result report
	lines := strings.Split(strings.TrimSpace(string(data)), "\n")
	parsed := false
	for n := len(lines) - 1; n >= 0; n-- {
		line := strings.TrimSpace(lines[n])
		if strings.HasPrefix(line, "{") && json.Unmarshal([]byte(line), &result) == nil && result.Python != "" {
			parsed = true
			break
		}
	}
	if !parsed {
		if commandErr != nil {
			return result, commandErr
		}
		return result, errors.New("无法解析环境检测结果。")
	}
	if commandErr != nil && result.Ready {
		return result, fmt.Errorf("检测进程未正常退出，不能标记环境就绪：%w", commandErr)
	}
	return result, nil
}

func (i *installer) prepare(ctx context.Context) (string, error) {
	for _, name := range []string{"runtime_probe.py", "suite_entry.py", "requirements.txt",
		"suite/hub.py", "suite/legacy.py", "color_tool/ui.py", "assets/toolbox.ico",
		"original_scripts/商品编码匹配工具.py", "original_scripts/送审资料生成器.py", "original_scripts/送审拼接图.py"} {
		if _, err := os.Stat(filepath.Join(i.root, name)); err != nil {
			return "", fmt.Errorf("缺少 %s。请先完整解压压缩包，EXE 需与源码放在一起。", name)
		}
	}
	i.step(0, "正在发现已有 Python 和工具箱专用环境…", 8)
	choices := i.candidates(ctx)
	if err := ctx.Err(); err != nil {
		return "", err
	}
	var selected interpreter
	for index, choice := range choices {
		i.log("检查 Python：" + choice.Executable)
		i.step(1, "正在实际测试界面、图片和 Excel 读写…", 20)
		status, err := i.health(ctx, choice.Executable)
		if err == nil && status.Ready {
			i.log("环境可用，无需下载或修改已有 Python。")
			i.step(3, "四个入口均已通过检查，可以进入工具箱。", 100)
			return choice.Executable, nil
		}
		if index == 0 {
			selected = choice
		}
		if ctx.Err() != nil {
			return "", ctx.Err()
		}
	}
	if selected.Executable == "" {
		i.step(0, "没有发现可用 Python，正在下载官方运行环境…", 15)
		var err error
		selected, err = i.downloadPython(ctx)
		if err != nil {
			return "", err
		}
	}
	i.step(2, "为工具箱准备专用环境，不修改系统 Python 包…", 38)
	hash := sha256.Sum256([]byte(selected.Executable + fmt.Sprint(selected.Version)))
	directory := filepath.Join(i.cache, "envs", hex.EncodeToString(hash[:6]))
	if err := os.MkdirAll(directory, 0755); err != nil {
		return "", err
	}
	if err := i.stream(ctx, selected.Executable, "-m", "venv", directory); err != nil {
		return "", fmt.Errorf("创建专用环境失败：%w", err)
	}
	python := filepath.Join(directory, "Scripts", "python.exe")
	i.log("专用环境：" + directory)
	if err := i.stream(ctx, python, "-m", "ensurepip", "--upgrade"); err != nil {
		return "", fmt.Errorf("准备 pip 失败：%w", err)
	}
	i.step(2, "正在下载缺失依赖，下载与安装日志显示在下方…", 50)
	if err := i.stream(ctx, python, "-m", "pip", "--isolated", "install", "--disable-pip-version-check", "--upgrade", "--index-url", "https://pypi.org/simple", "pip"); err != nil {
		return "", fmt.Errorf("更新 pip 失败：%w。检查网络后可重新检测。", err)
	}
	if err := i.stream(ctx, python, "-m", "pip", "--isolated", "install", "--disable-pip-version-check", "--upgrade", "--index-url", "https://pypi.org/simple", "-r", filepath.Join(i.root, "requirements.txt")); err != nil {
		return "", fmt.Errorf("依赖安装失败：%w。详情见日志；不会把失败的环境标记为就绪。", err)
	}
	i.step(2, "依赖已安装，正在重新验证四个程序…", 90)
	status, err := i.health(ctx, python)
	if err != nil {
		return "", err
	}
	if !status.Ready {
		return "", errors.New("安装后仍有能力检查失败，请查看日志中的具体项目并重新检测。")
	}
	i.step(3, "四个入口均已通过检查，可以进入工具箱。", 100)
	return python, nil
}

func (i *installer) download(ctx context.Context, address, target string) error {
	parsed, err := url.Parse(address)
	if err != nil || parsed.Scheme != "https" || parsed.Hostname() != "www.python.org" {
		return errors.New("拒绝非 Python 官方 HTTPS 下载地址。")
	}
	request, err := http.NewRequestWithContext(ctx, "GET", address, nil)
	if err != nil {
		return err
	}
	request.Header.Set("User-Agent", "GreatLibraryToolbox/1.0")
	response, err := i.client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return fmt.Errorf("下载 HTTP %d", response.StatusCode)
	}
	if response.ContentLength > 200*1024*1024 {
		return errors.New("安装包超出预期大小。")
	}
	file, err := os.Create(target + ".partial")
	if err != nil {
		return err
	}
	defer os.Remove(target + ".partial")
	var done int64
	buffer := make([]byte, 128*1024)
	last := time.Now()
	for {
		count, readErr := response.Body.Read(buffer)
		if count > 0 {
			if _, err = file.Write(buffer[:count]); err != nil {
				file.Close()
				return err
			}
			done += int64(count)
			if done > 200*1024*1024 {
				file.Close()
				return errors.New("下载大小超过限制。")
			}
			if time.Since(last) > time.Second {
				i.step(0, fmt.Sprintf("下载官方 Python：%.1f MB", float64(done)/(1024*1024)), 18)
				last = time.Now()
			}
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			file.Close()
			return readErr
		}
	}
	if err = file.Close(); err != nil {
		return err
	}
	if response.ContentLength >= 0 && done != response.ContentLength {
		return errors.New("安装包下载不完整。")
	}
	return os.Rename(target+".partial", target)
}

func officialInstaller(body string) (string, []int, error) {
	pattern := regexp.MustCompile(`https://www\.python\.org/ftp/python/(3\.[0-9]+\.[0-9]+)/python-([0-9.]+)-amd64\.exe`)
	var best string
	var version []int
	for _, match := range pattern.FindAllStringSubmatch(body, -1) {
		if match[1] != match[2] {
			continue
		}
		parts := strings.Split(match[1], ".")
		var values []int
		for _, p := range parts {
			v, _ := strconv.Atoi(p)
			values = append(values, v)
		}
		if best == "" || compareVersions(values, version) > 0 {
			best = match[0]
			version = values
		}
	}
	if best == "" {
		return "", nil, errors.New("未能从 Python 官方页面发现稳定版 Windows 安装包。请联网后重试。")
	}
	return best, version, nil
}

func (i *installer) downloadPython(ctx context.Context) (interpreter, error) {
	var zero interpreter
	request, err := http.NewRequestWithContext(ctx, "GET", "https://www.python.org/downloads/windows/", nil)
	if err != nil {
		return zero, err
	}
	response, err := i.client.Do(request)
	if err != nil {
		return zero, err
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return zero, fmt.Errorf("读取官网 HTTP %d", response.StatusCode)
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, 4*1024*1024))
	if err != nil {
		return zero, err
	}
	address, version, err := officialInstaller(string(data))
	if err != nil {
		return zero, err
	}
	versionText := fmt.Sprintf("%d.%d.%d", version[0], version[1], version[2])
	downloads := filepath.Join(i.cache, "downloads")
	if err = os.MkdirAll(downloads, 0755); err != nil {
		return zero, err
	}
	file := filepath.Join(downloads, "python-"+versionText+"-amd64.exe")
	i.log("下载来源：" + address)
	if err = i.download(ctx, address, file); err != nil {
		return zero, err
	}
	i.log("正在检查 Windows Authenticode 签名及信任链…")
	if err = verifySignature(file); err != nil {
		os.Remove(file)
		return zero, fmt.Errorf("Python 安装包签名验证未通过，文件已删除：%w", err)
	}
	directory := filepath.Join(i.cache, "python", versionText)
	i.log("签名验证通过，安装到工具箱专用目录。")
	i.step(0, "正在安装官方 Python（包含 Tk 和 pip）…", 28)
	if err = i.stream(ctx, file, "/quiet", "InstallAllUsers=0", "TargetDir="+directory, "Include_launcher=0",
		"Include_pip=1", "Include_tcltk=1", "Include_test=0", "Shortcuts=0", "PrependPath=0", "AssociateFiles=0"); err != nil {
		var exit *exec.ExitError
		if !errors.As(err, &exit) || exit.ExitCode() != 3010 {
			return zero, fmt.Errorf("Python 安装失败：%w", err)
		}
	}
	return i.inspect(ctx, filepath.Join(directory, "python.exe"))
}

func (i *installer) launch(python string) error {
	logs := filepath.Join(i.cache, "logs")
	if err := os.MkdirAll(logs, 0755); err != nil {
		return err
	}
	file, err := os.OpenFile(filepath.Join(logs, "toolbox.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		return err
	}
	defer file.Close()
	cmd := exec.Command(python, filepath.Join(i.root, "suite_entry.py"))
	cmd.Dir = i.root
	cmd.Env = append(os.Environ(), "PYTHONUTF8=1", "PYTHONIOENCODING=utf-8")
	cmd.Stdout, cmd.Stderr = file, file
	hideConsole(cmd)
	if err = cmd.Start(); err != nil {
		return err
	}
	return cmd.Process.Release()
}
