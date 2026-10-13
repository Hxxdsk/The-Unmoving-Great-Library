//go:build !windows

package main

import (
	"errors"
	"net/url"
	"os/exec"
)

func hideConsole(cmd *exec.Cmd) {}
func verifySignature(path string) error {
	return errors.New("Authenticode verification requires Windows")
}
func systemProxy() *url.URL { return nil }
func main()                 {}
