//go:build windows

package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestUnsignedInstallerIsRejected(t *testing.T) {
	path := filepath.Join(t.TempDir(), "not-signed.exe")
	if err := os.WriteFile(path, []byte("MZ not a signed installer"), 0644); err != nil {
		t.Fatal(err)
	}
	if err := verifySignature(path); err == nil {
		t.Fatal("Accepted unsigned executable")
	}
}
