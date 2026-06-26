//go:build linux

package collector

import (
	"bufio"
	"os"
	"strings"
	"syscall"
)

// Virtual/pseudo filesystems + removable/optical media we never report on by
// default. iso9660/udf are optical (DVD/ISO), squashfs is snap loop mounts, bpf
// is /sys/fs/bpf (it leaked through before). The manual exclude_mounts filter
// (Stage B) runs AFTER this, on the remaining real drives.
var skipFSTypes = map[string]bool{
	"tmpfs": true, "devtmpfs": true, "sysfs": true, "proc": true,
	"devpts": true, "cgroup": true, "cgroup2": true, "pstore": true,
	"debugfs": true, "hugetlbfs": true, "mqueue": true, "overlay": true,
	"securityfs": true, "tracefs": true, "configfs": true, "fusectl": true,
	"bpf": true, "iso9660": true, "udf": true, "squashfs": true,
}

type mountEntry struct {
	device string
	point  string
	fstype string
}

func CollectDisk() ([]DiskStat, error) {
	mounts, err := readMounts()
	if err != nil {
		return nil, err
	}
	var stats []DiskStat
	seen := make(map[string]bool)
	for _, m := range mounts {
		if skipFSTypes[m.fstype] || seen[m.device] {
			continue
		}
		seen[m.device] = true

		var st syscall.Statfs_t
		if err := syscall.Statfs(m.point, &st); err != nil {
			continue
		}
		bsize := uint64(st.Bsize)
		total := st.Blocks * bsize
		free := st.Bfree * bsize
		used := total - free
		var usagePct float64
		if total > 0 {
			usagePct = float64(used) / float64(total) * 100
		}
		stats = append(stats, DiskStat{
			Mount: m.point, Device: m.device, FSType: m.fstype,
			TotalBytes: total, UsedBytes: used, FreeBytes: free, UsagePct: usagePct,
		})
	}
	return stats, nil
}

func readMounts() ([]mountEntry, error) {
	f, err := os.Open("/proc/mounts")
	if err != nil {
		return nil, err
	}
	defer f.Close()

	var mounts []mountEntry
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) < 3 {
			continue
		}
		mounts = append(mounts, mountEntry{device: fields[0], point: fields[1], fstype: fields[2]})
	}
	return mounts, scanner.Err()
}
