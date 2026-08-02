//go:build !linux && !windows

package collector

import "runtime"

// CollectOSInfo fallback for platforms without a specific implementation
// (darwin, *bsd). The server/UI fall back to os_family anyway.
func CollectOSInfo() OSInfo {
	return OSInfo{Name: runtime.GOOS}
}
