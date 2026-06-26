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
	getDriveType := kernel32.NewProc("GetDriveTypeW")

	ret, _, _ := getLogicalDrives.Call()
	drives := uint32(ret)

	var stats []DiskStat
	for i := 0; i < 26; i++ {
		if drives&(1<<uint(i)) == 0 {
			continue
		}
		drive := string(rune('A'+i)) + `:\`
		drivePtr, err := syscall.UTF16PtrFromString(drive)
		if err != nil {
			continue
		}
		// Auto-skip removable/optical media (USB, DVD, mounted ISO) by default —
		// a full read-only disc reports 100% forever and is pure noise. Fixed and
		// network drives are kept; the manual exclude_mounts filter runs after.
		dt, _, _ := getDriveType.Call(uintptr(unsafe.Pointer(drivePtr)))
		if skipWindowsDriveType(uint32(dt)) {
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
			Mount: drive, Device: drive,
			TotalBytes: totalBytes, UsedBytes: used, FreeBytes: totalFree,
			UsagePct: float64(used) / float64(totalBytes) * 100,
		})
	}
	return stats, nil
}
