//go:build linux

package collector

import (
	"os"
	"os/exec"
	"strings"
)

// CollectOSInfo reads the distro identity from /etc/os-release (falling back to
// /usr/lib/os-release) and the kernel from `uname -r`. Best-effort: an absent
// os-release yields {Name: "Linux"} via parseOSRelease("").
func CollectOSInfo() OSInfo {
	info := parseOSRelease(readOSRelease())
	if k := kernelRelease(); k != "" {
		info.Kernel = k
	}
	return info
}

func readOSRelease() string {
	for _, p := range []string{"/etc/os-release", "/usr/lib/os-release"} {
		if b, err := os.ReadFile(p); err == nil {
			return string(b)
		}
	}
	return ""
}

func kernelRelease() string {
	out, err := exec.Command("uname", "-r").Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}
