package collector

import "testing"

// Windows: skip removable (USB) + optical (DVD/ISO), keep fixed + network.
func TestSkipWindowsDriveType(t *testing.T) {
	skip := map[uint32]bool{
		driveRemovable: true,  // 2 — USB
		driveCDROM:     true,  // 5 — DVD / mounted ISO
		driveFixed:     false, // 3 — real disk, keep
		driveRemote:    false, // 4 — network drive, keep by default
		0:              false, // DRIVE_UNKNOWN, keep (don't over-skip)
		1:              false, // DRIVE_NO_ROOT_DIR
		6:              false, // DRIVE_RAMDISK
	}
	for dt, want := range skip {
		if got := skipWindowsDriveType(dt); got != want {
			t.Errorf("skipWindowsDriveType(%d) = %v, want %v", dt, got, want)
		}
	}
}

// FilterDisks (manual exclude) still works on whatever the collector emits after
// the auto-skip — exclude D: drops the emitted "D:\".
func TestAutoSkipThenManualExcludeOrder(t *testing.T) {
	// After auto-skip only fixed drives remain; the operator can still exclude one.
	fixed := []DiskStat{{Mount: `C:\`}, {Mount: `E:\`}}
	got := FilterDisks(fixed, nil, []string{"E:"})
	if len(got) != 1 || got[0].Mount != `C:\` {
		t.Fatalf("manual exclude should still apply after auto-skip, got %v", mounts(got))
	}
}
