//go:build windows

package collector

import (
	"syscall"
	"unsafe"

	"golang.org/x/sys/windows"
)

func CollectDisk() ([]DiskStat, error) {
	kernel32 := windows.NewLazySystemDLL("kernel32.dll")
	getDiskFreeSpaceEx := kernel32.NewProc("GetDiskFreeSpaceExW")
	getLogicalDrives := kernel32.NewProc("GetLogicalDrives")

	ret, _, _ := getLogicalDrives.Call()
	drives := uint32(ret)

	var stats []DiskStat
	for i := 0; i < 26; i++ {
		if drives&(1<<uint(i)) == 0 {
			continue
		}
		// root ("C:\") is required by the Win32 calls; the EMITTED mount is "C:"
		// (no trailing backslash). A trailing "\" in an InfluxDB tag value escapes
		// the line-protocol delimiter on write, mangling "C:\" → "C: " (trailing
		// space). Emitting "C:" stores cleanly and matches what operators type in
		// exclude_mounts. (normalizeMount strips trailing slashes anyway, so
		// include/exclude matching is unaffected.)
		root := string(rune('A'+i)) + `:\`
		mount := string(rune('A'+i)) + ":"
		drivePtr, err := syscall.UTF16PtrFromString(root)
		if err != nil {
			continue
		}
		var freeAvail, totalBytes, totalFree uint64
		r, _, _ := getDiskFreeSpaceEx.Call(
			uintptr(unsafe.Pointer(drivePtr)),
			uintptr(unsafe.Pointer(&freeAvail)),
			uintptr(unsafe.Pointer(&totalBytes)),
			uintptr(unsafe.Pointer(&totalFree)),
		)
		if r == 0 || totalBytes == 0 {
			continue
		}
		used := totalBytes - totalFree
		stats = append(stats, DiskStat{
			Mount: mount, Device: mount,
			TotalBytes: totalBytes, UsedBytes: used, FreeBytes: totalFree,
			UsagePct: float64(used) / float64(totalBytes) * 100,
		})
	}
	return stats, nil
}
