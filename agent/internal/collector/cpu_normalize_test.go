package collector

import "testing"

// The Windows CPU collector must emit the same shape as Linux: the aggregate row
// keyed "cpu" (so the chart/Overview pick it up) and per-core rows passed
// through. WMI's "_Total" aggregate must become "cpu"; numeric per-core ids stay.
func TestNormalizeCPUCore(t *testing.T) {
	cases := map[string]string{
		"_Total": "cpu", // Windows aggregate → cross-platform aggregate key
		"cpu":    "cpu", // Linux aggregate (already correct)
		"0":      "0",   // Windows per-core
		"7":      "7",
		"cpu0":   "cpu0", // Linux per-core
	}
	for in, want := range cases {
		if got := normalizeCPUCore(in); got != want {
			t.Errorf("normalizeCPUCore(%q) = %q, want %q", in, got, want)
		}
	}
	if normalizeCPUCore("_Total") != AggregateCPUCore {
		t.Errorf("the _Total mapping must equal AggregateCPUCore (%q)", AggregateCPUCore)
	}
}
