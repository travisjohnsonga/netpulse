//go:build linux

package logforward

import (
	"os"
	"path/filepath"
	"testing"
)

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

// readNew tails appended lines and re-reads from the start after rotation
// (inode change) or truncation (size shrink) — no lost/duplicated lines.
func TestReadNewTailAndRotation(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "test.log")
	if err := os.WriteFile(p, []byte("a\nb\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	st, _ := os.Stat(p)
	tl := &tail{source: "auth", offset: st.Size(), inode: inodeOf(st)} // seek to end

	// Nothing new yet.
	if got := readNew(p, tl); len(got) != 0 {
		t.Fatalf("expected no new lines, got %v", got)
	}

	// Append → only the new lines.
	f, _ := os.OpenFile(p, os.O_APPEND|os.O_WRONLY, 0o644)
	f.WriteString("c\nd\n")
	f.Close()
	if got := readNew(p, tl); !eq(got, []string{"c", "d"}) {
		t.Fatalf("append: got %v want [c d]", got)
	}

	// Rotate: replace the file with a new inode → re-read from the start.
	os.Remove(p)
	if err := os.WriteFile(p, []byte("e\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := readNew(p, tl); !eq(got, []string{"e"}) {
		t.Fatalf("rotation: got %v want [e]", got)
	}

	// Grow the file so the offset advances well past a future small file...
	f2, _ := os.OpenFile(p, os.O_APPEND|os.O_WRONLY, 0o644)
	f2.WriteString("g\nh\ni\n")
	f2.Close()
	_ = readNew(p, tl) // consume → offset now large

	// ...then truncate in place to a smaller size (copytruncate) → size < offset
	// is detected and we re-read from the start.
	os.WriteFile(p, []byte("f\n"), 0o644) // O_TRUNC keeps the inode, shrinks size
	if got := readNew(p, tl); !eq(got, []string{"f"}) {
		t.Fatalf("truncate: got %v want [f]", got)
	}
}

// Apply must refuse an out-of-allowlist additional path (defense in depth).
type nopShipper struct{}

func (nopShipper) SendLogs(string, []string) error { return nil }

func TestApplyRefusesBadPath(t *testing.T) {
	f := New(nopShipper{})
	defer f.Stop()
	f.Apply(false, []string{"/etc/shadow", "/var/log/ok.log"})
	f.mu.Lock()
	defer f.mu.Unlock()
	if _, bad := f.tails["/etc/shadow"]; bad {
		t.Fatal("/etc/shadow must NOT be tailed")
	}
	if _, ok := f.tails["/var/log/ok.log"]; !ok {
		t.Fatal("/var/log/ok.log should be tailed")
	}
}
