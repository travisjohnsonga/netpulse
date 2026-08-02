package collector

import "testing"

func mounts(stats []DiskStat) []string {
	out := make([]string, len(stats))
	for i, s := range stats {
		out[i] = s.Mount
	}
	return out
}

func eq(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// Windows collector emits drive roots like "C:\" / "D:\"; an operator types "D:".
var winDisks = []DiskStat{{Mount: `C:\`}, {Mount: `D:\`}}
var linuxDisks = []DiskStat{{Mount: "/"}, {Mount: "/var"}, {Mount: "/home"}}

func TestFilterDisks_EmptyMonitorsAll(t *testing.T) {
	// THE regression guard: empty include AND empty exclude = ALL, not nothing.
	if got := FilterDisks(winDisks, nil, nil); !eq(mounts(got), []string{`C:\`, `D:\`}) {
		t.Fatalf("empty filter should keep all, got %v", mounts(got))
	}
	if got := FilterDisks(linuxDisks, []string{}, []string{}); len(got) != 3 {
		t.Fatalf("empty filter should keep all 3 linux mounts, got %v", mounts(got))
	}
}

func TestFilterDisks_ExcludeWindowsDriveNormalization(t *testing.T) {
	// "D:" must match the collector's "D:\" — the original recovery-partition bug.
	for _, entry := range []string{"D:", `D:\`, "d:"} {
		got := FilterDisks(winDisks, nil, []string{entry})
		if !eq(mounts(got), []string{`C:\`}) {
			t.Fatalf("exclude %q should drop D:\\, got %v", entry, mounts(got))
		}
	}
}

func TestFilterDisks_IncludeOnly(t *testing.T) {
	got := FilterDisks(winDisks, []string{"C:"}, nil)
	if !eq(mounts(got), []string{`C:\`}) {
		t.Fatalf("include-only C: should keep only C:\\, got %v", mounts(got))
	}
}

func TestFilterDisks_ExcludeWins(t *testing.T) {
	got := FilterDisks(winDisks, []string{"C:", "D:"}, []string{"D:"})
	if !eq(mounts(got), []string{`C:\`}) {
		t.Fatalf("exclude should take precedence over include, got %v", mounts(got))
	}
}

func TestFilterDisks_LinuxTrailingSlash(t *testing.T) {
	// Both "/var" and "/var/" must match the emitted "/var"; "/" preserved.
	for _, entry := range []string{"/var", "/var/"} {
		got := FilterDisks(linuxDisks, nil, []string{entry})
		if !eq(mounts(got), []string{"/", "/home"}) {
			t.Fatalf("exclude %q should drop /var only, got %v", entry, mounts(got))
		}
	}
}

func TestFilterDisks_MatchOnlyPreservesReportedMount(t *testing.T) {
	// Normalization is for MATCHING only — the surviving disk keeps its real
	// emitted Mount string ("C:\"), not the normalized form ("C:").
	got := FilterDisks(winDisks, nil, []string{"d:"})
	if len(got) != 1 || got[0].Mount != `C:\` {
		t.Fatalf("reported Mount must stay %q, got %v", `C:\`, mounts(got))
	}
}
