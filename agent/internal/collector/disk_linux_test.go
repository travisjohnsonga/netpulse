//go:build linux

package collector

import "testing"

func TestSkipFSTypes(t *testing.T) {
	// Optical/loop/pseudo filesystems are skipped...
	for _, fs := range []string{"iso9660", "udf", "squashfs", "bpf", "tmpfs", "proc", "sysfs"} {
		if !skipFSTypes[fs] {
			t.Errorf("expected %q to be skipped", fs)
		}
	}
	// ...real disk filesystems are kept.
	for _, fs := range []string{"ext4", "xfs", "btrfs", "ntfs", "vfat"} {
		if skipFSTypes[fs] {
			t.Errorf("expected %q to be collected (not skipped)", fs)
		}
	}
}
