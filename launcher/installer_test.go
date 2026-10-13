package main

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"testing"
)

func TestHealthReportIgnoresWarningsButRejectsAbnormalExit(t *testing.T) {
	output := []byte("Qt warning\n{\"ready\":true,\"python\":\"python.exe\",\"checks\":[]}\n")
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
