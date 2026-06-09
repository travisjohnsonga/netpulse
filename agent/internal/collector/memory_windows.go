//go:build windows

package collector

import (
	"unsafe"

	"golang.org/x/sys/windows"
)

type memoryStatusEx struct {
	dwLength                uint32
	dwMemoryLoad            uint32
	ullTotalPhys            uint64
	ullAvailPhys            uint64
	ullTotalPageFile        uint64
	ullAvailPageFile        uint64
	ullTotalVirtual         uint64
	ullAvailVirtual         uint64
	ullAvailExtendedVirtual uint64
}

func CollectMemory() (*MemoryStat, error) {
	kernel32 := windows.NewLazySystemDLL("kernel32.dll")
	proc := kernel32.NewProc("GlobalMemoryStatusEx")

	var m memoryStatusEx
	m.dwLength = uint32(unsafe.Sizeof(m))
	ret, _, err := proc.Call(uintptr(unsafe.Pointer(&m)))
	if ret == 0 {
		return nil, err
	}

	total := m.ullTotalPhys
	avail := m.ullAvailPhys
	used := total - avail
	var usagePct float64
	if total > 0 {
		usagePct = float64(used) / float64(total) * 100
	}
	swapTotal := m.ullTotalPageFile
	swapFree := m.ullAvailPageFile
	return &MemoryStat{
		TotalBytes:     total,
		UsedBytes:      used,
		FreeBytes:      avail,
		AvailableBytes: avail,
		UsagePct:       usagePct,
		SwapTotal:      swapTotal,
		SwapFree:       swapFree,
		SwapUsed:       swapTotal - swapFree,
	}, nil
}
