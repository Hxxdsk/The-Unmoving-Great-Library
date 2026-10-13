package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestHealthReportIgnoresWarningsButRejectsAbnormalExit(t *testing.T) {
	output := []byte("Qt warning\n{\"ready\":true,\"python\":\"python.exe\",\"checks\":[{\"ok\":true}]}\n")
	if result, err := parseHealthOutput(output, nil); err != nil || !result.Ready {
		t.Fatal(result, err)
	}
	if _, err := parseHealthOutput(output, errors.New("exit 139")); err == nil {
		t.Fatal("Accepted abnormal exit")
	}
	failed := []byte("{\"ready\":false,\"python\":\"python.exe\",\"checks\":[]}\n")
	if _, err := parseHealthOutput(failed, errors.New("exit 1")); err != nil {
		t.Fatal("Normal failed check should be diagnosable", err)
	}
}

func TestOnlyMissingLibrariesAreRequested(t *testing.T) {
	status := report{Checks: []checkItem{
		{OK: true, Package: "PySide6"}, {OK: true, Package: "Pillow"},
		{OK: false, Package: "pandas"}, {OK: false, Package: "pandas"},
		{OK: false, Package: "openpyxl", InstalledVersion: "3.1.5"},
		{OK: false, Name: "Python syntax"},
	}}
	packages := missingPackages(status)
	if strings.Join(packages, ",") != "openpyxl==3.1.5,pandas" {
		t.Fatal(packages)
	}
}

func TestEmptyOrContradictoryChecksCannotBeReady(t *testing.T) {
	for _, output := range []string{
		`{"ready":true,"python":"python.exe","checks":[]}`,
		`{"ready":true,"python":"python.exe","checks":[{"ok":false}]}`,
	} {
		if _, err := parseHealthOutput([]byte(output), nil); err == nil {
			t.Fatal("Invalid ready report", output)
		}
	}
}

func TestOfficialInstallerUsesNewestStableAndExactVersion(t *testing.T) {
	body := `<a href="https://www.python.org/ftp/python/3.14.0/python-3.14.0-amd64.exe">A</a>
<a href="https://www.python.org/ftp/python/3.14.8/python-3.14.8-amd64.exe">B</a>
<a href="https://www.python.org/ftp/python/3.15.0/python-3.15.0rc1-amd64.exe">C</a>
<a href="https://www.python.org/ftp/python/3.99.0/python-3.14.8-amd64.exe">D</a>
<a href="http://evil.invalid/ftp/python/3.99.0/python-3.99.0-amd64.exe">E</a>`
	address, version, err := officialInstaller(body)
	if err != nil || !strings.Contains(address, "3.14.8") || version[2] != 8 {
		t.Fatalf("Wrong installer: %s %v %v", address, version, err)
	}
}
func TestNoOfficialInstallerFails(t *testing.T) {
	if _, _, err := officialInstaller("No official executable"); err == nil {
		t.Fatal("Expected failure")
	}
}
func TestLogRedactsProxyCredentialsAndPreservesLines(t *testing.T) {
	var lines []string
	writer := logWriter{log: func(line string) { lines = append(lines, line) }}
	writer.Write([]byte("下载中\nhttps://alice:secret@proxy.invalid/a\r"))
	writer.Write([]byte("\ncomplete"))
	writer.flush()
	if len(lines) != 3 || strings.Contains(strings.Join(lines, ""), "secret") {
		t.Fatal(lines)
	}
}
func TestDownloadRejectsInsecureAndNonOfficialHosts(t *testing.T) {
	i := installer{client: &http.Client{}}
	for _, address := range []string{"http://www.python.org/x", "https://evil.invalid/x", "file:///tmp/a"} {
		if err := i.download(context.Background(), address, t.TempDir()+"/file"); err == nil {
			t.Fatal("Allowed", address)
		}
	}
}
func TestPrepareChecksFullExtractionBeforeMutatingCache(t *testing.T) {
	i := installer{root: t.TempDir(), cache: t.TempDir()}
	if _, err := i.prepare(context.Background()); err == nil || !strings.Contains(err.Error(), "完整解压") {
		t.Fatal(err)
	}
}

func TestHealthyCachedRuntimeSkipsSetupAndDownloads(t *testing.T) {
	python, err := exec.LookPath("python")
	if err != nil {
		python, err = exec.LookPath("python3")
	}
	if err != nil {
		t.Skip("Python is needed for the subprocess fixture")
	}
	root, cache := t.TempDir(), t.TempDir()
	for _, name := range []string{"runtime_probe.py", "suite_entry.py", "requirements.txt", "suite/hub.py", "suite/legacy.py", "color_tool/ui.py", "assets/toolbox.ico", "original_scripts/商品编码匹配工具.py", "original_scripts/送审资料生成器.py", "original_scripts/送审拼接图.py"} {
		path := filepath.Join(root, name)
		if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(""), 0644); err != nil {
			t.Fatal(err)
		}
	}
	probe := "import json,sys,time;print(json.dumps({'ready':True,'python':sys.executable,'checks':[{'name':'fixture','ok':True}],'checked_at':time.time(),'source_hash':'fixture'}))"
	if err := os.WriteFile(filepath.Join(root, "runtime_probe.py"), []byte(probe), 0644); err != nil {
		t.Fatal(err)
	}
	data, _ := json.Marshal(map[string]string{"python": python})
	if err := os.WriteFile(filepath.Join(cache, "last_runtime.json"), data, 0644); err != nil {
		t.Fatal(err)
	}
	revealed := false
	i := installer{root: root, cache: cache, log: func(string) {}, step: func(int, string, int) {}, needsSetup: func() { revealed = true }}
	selected, err := i.prepare(context.Background())
	if err != nil || selected != python || revealed || !i.validated.Ready {
		t.Fatal(selected, err, revealed)
	}
	if _, err := os.Stat(filepath.Join(cache, "envs")); !os.IsNotExist(err) {
		t.Fatal("Healthy runtime created an installation environment", err)
	}
}

func TestOverlayReusesExistingVirtualEnvironmentLibraries(t *testing.T) {
	python, err := exec.LookPath("python")
	if err != nil {
		python, err = exec.LookPath("python3")
	}
	if err != nil {
		t.Skip("Python is needed for the subprocess fixture")
	}
	root := t.TempDir()
	source, target := filepath.Join(root, "existing"), filepath.Join(root, "overlay")
	i := installer{root: root}
	for _, directory := range []string{source, target} {
		if _, err := i.run(context.Background(), python, "-m", "venv", "--without-pip", directory); err != nil {
			t.Fatal(err)
		}
	}
	venvPython := func(directory string) string {
		if runtime.GOOS == "windows" {
			return filepath.Join(directory, "Scripts", "python.exe")
		}
		return filepath.Join(directory, "bin", "python")
	}
	seed := "import sysconfig;from pathlib import Path;Path(sysconfig.get_path('purelib'),'fixture_existing_lib.py').write_text('value=42')"
	if _, err := i.run(context.Background(), venvPython(source), "-c", seed); err != nil {
		t.Fatal(err)
	}
	if err := i.inheritPackages(context.Background(), venvPython(source), venvPython(target)); err != nil {
		t.Fatal(err)
	}
	if _, err := i.run(context.Background(), venvPython(target), "-c", "import fixture_existing_lib;assert fixture_existing_lib.value==42"); err != nil {
		t.Fatal(err)
	}
}
