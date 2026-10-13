//go:build windows

package main

import (
	"fmt"
	"net/url"
	"os/exec"
	"strings"
	"syscall"
	"unsafe"
)

func hideConsole(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000}
}

type winTrustFile struct {
	Size         uint32
	Path         *uint16
	File         uintptr
	KnownSubject uintptr
}
type winTrustData struct {
	Size              uint32
	PolicyCallback    uintptr
	SIPClient         uintptr
	UIChoice          uint32
	RevocationChecks  uint32
	UnionChoice       uint32
	File              *winTrustFile
	StateAction       uint32
	StateData         uintptr
	URLReference      uintptr
	ProviderFlags     uint32
	UIContext         uint32
	SignatureSettings uintptr
}
type guid struct {
	A    uint32
	B, C uint16
	D    [8]byte
}

func verifySignature(path string) error {
	name, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		return err
	}
	file := winTrustFile{Path: name}
	file.Size = uint32(unsafe.Sizeof(file))
	data := winTrustData{UIChoice: 2, RevocationChecks: 1, UnionChoice: 1, File: &file, StateAction: 1, ProviderFlags: 0x80}
	data.Size = uint32(unsafe.Sizeof(data))
	action := guid{A: 0x00aac56b, B: 0xcd44, C: 0x11d0, D: [8]byte{0x8c, 0xc2, 0, 0xc0, 0x4f, 0xc2, 0x95, 0xee}}
	verify := syscall.NewLazyDLL("wintrust.dll").NewProc("WinVerifyTrust")
	result, _, _ := verify.Call(0, uintptr(unsafe.Pointer(&action)), uintptr(unsafe.Pointer(&data)))
	data.StateAction = 2
	verify.Call(0, uintptr(unsafe.Pointer(&action)), uintptr(unsafe.Pointer(&data)))
	if uint32(result) != 0 {
		return fmt.Errorf("Windows 信任验证错误 0x%08x", uint32(result))
	}
	return nil
}

func systemProxy() *url.URL {
	// 读取 Windows 手动 HTTPS 代理；环境变量代理由 http.ProxyFromEnvironment 优先处理。
	type proxyConfig struct {
		AutoDetect             int32
		AutoURL, Proxy, Bypass *uint16
	}
	var config proxyConfig
	get := syscall.NewLazyDLL("winhttp.dll").NewProc("WinHttpGetIEProxyConfigForCurrentUser")
	free := syscall.NewLazyDLL("kernel32.dll").NewProc("GlobalFree")
	result, _, _ := get.Call(uintptr(unsafe.Pointer(&config)))
	if result == 0 {
		return nil
	}
	defer func() {
		for _, ptr := range []*uint16{config.AutoURL, config.Proxy, config.Bypass} {
			if ptr != nil {
				free.Call(uintptr(unsafe.Pointer(ptr)))
			}
		}
	}()
	if config.Proxy == nil {
		return nil
	}
	name := utf16PointerString(config.Proxy)
	for _, part := range strings.Split(name, ";") {
		part = strings.TrimSpace(part)
		if strings.HasPrefix(part, "https=") {
			name = strings.TrimPrefix(part, "https=")
			break
		}
		if !strings.Contains(part, "=") {
			name = part
		}
	}
	if strings.Contains(name, "=") {
		return nil
	}
	if !strings.Contains(name, "://") {
		name = "http://" + name
	}
	parsed, err := url.Parse(name)
	if err != nil {
		return nil
	}
	return parsed
}

func utf16PointerString(ptr *uint16) string {
	if ptr == nil {
		return ""
	}
	var values []uint16
	for n := uintptr(0); n < 32768; n++ {
		value := *(*uint16)(unsafe.Pointer(uintptr(unsafe.Pointer(ptr)) + n*2))
		if value == 0 {
			break
		}
		values = append(values, value)
	}
	return syscall.UTF16ToString(values)
}
