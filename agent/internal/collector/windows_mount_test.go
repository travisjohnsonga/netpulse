package collector

import "testing"

// After the mount-string cleanup the Windows collector emits "C:"/"D:" (no
// trailing backslash). normalizeMount/FilterDisks must still match an operator's
// exclude entry, and a "C:" entry must equal the emitted "C:".
func TestFilterDisks_NoSlashWindowsForm(t *testing.T) {
	disks := []DiskStat{{Mount: "C:"}, {Mount: "D:"}}
	got := FilterDisks(disks, nil, []string{"D:"})
	if len(got) != 1 || got[0].Mount != "C:" {
		t.Fatalf("exclude D: should leave only C:, got %v", mounts(got))
	}
	// Operator variants still match the clean emitted form.
	for _, entry := range []string{"D:", `D:\`, "d:"} {
		if g := FilterDisks(disks, nil, []string{entry}); len(g) != 1 || g[0].Mount != "C:" {
			t.Fatalf("exclude %q should drop emitted D:, got %v", entry, mounts(g))
		}
	}
	if normalizeMount("C:") != "C:" {
		t.Fatalf("normalizeMount(\"C:\") = %q, want C:", normalizeMount("C:"))
	}
}
