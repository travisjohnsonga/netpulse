//go:build linux

package logforward

import (
	"bufio"
	"log"
	"os"
	"sync"
	"syscall"
	"time"
)

// Shipper ships a batch of raw lines for a source. *transport.Client satisfies it
// (SendLogs) — an interface here avoids a logforward→transport import cycle.
type Shipper interface {
	SendLogs(source string, lines []string) error
}

// pollInterval — how often we read new lines. Cheap: stat + read-to-EOF.
const pollInterval = 5 * time.Second

// maxLinesPerPoll caps a single ship batch (rotation/backfill bursts).
const maxLinesPerPoll = 1000

// securityProfilePaths returns the (source → path) set for the curated profile,
// picking the first existing file per distro (Debian auth.log vs RHEL secure,
// syslog vs messages).
func securityProfilePaths() map[string]string {
	pick := func(cands ...string) string {
		for _, c := range cands {
			if fi, err := os.Stat(c); err == nil && !fi.IsDir() {
				return c
			}
		}
		return ""
	}
	out := map[string]string{}
	if p := pick("/var/log/auth.log", "/var/log/secure"); p != "" {
		out[p] = "auth"
	}
	if p := pick("/var/log/syslog", "/var/log/messages"); p != "" {
		out[p] = "service"
	}
	if p := pick("/var/log/kern.log"); p != "" {
		out[p] = "kernel"
	}
	return out
}

type tail struct {
	source string
	offset int64
	inode  uint64
}

// Forwarder tails a reconciled set of files and ships new lines. Apply() sets the
// active set from config; a single goroutine polls. Outbound-only.
type Forwarder struct {
	shipper Shipper
	mu      sync.Mutex
	tails   map[string]*tail // path → state
	stop    chan struct{}
	started bool
}

func New(shipper Shipper) *Forwarder {
	return &Forwarder{shipper: shipper, tails: map[string]*tail{}, stop: make(chan struct{})}
}

// Apply reconciles the active path set: the security profile (when enabled) plus
// allowlisted additional paths. Paths failing the allowlist are refused + logged.
func (f *Forwarder) Apply(securityProfile bool, additionalPaths []string) {
	want := map[string]string{} // path → source
	if securityProfile {
		for p, src := range securityProfilePaths() {
			want[p] = src
		}
	}
	for _, p := range additionalPaths {
		if IsAllowedLogPath(p) {
			want[p] = "custom"
		} else {
			log.Printf("logforward: refusing out-of-allowlist path %q", p)
		}
	}

	f.mu.Lock()
	defer f.mu.Unlock()
	// Drop paths no longer wanted.
	for p := range f.tails {
		if _, ok := want[p]; !ok {
			delete(f.tails, p)
		}
	}
	// Add new paths, seeking to END (don't backfill the whole historical file).
	for p, src := range want {
		if _, ok := f.tails[p]; ok {
			continue
		}
		t := &tail{source: src}
		if fi, err := os.Stat(p); err == nil {
			t.offset = fi.Size()
			t.inode = inodeOf(fi)
		}
		f.tails[p] = t
	}
	if !f.started && len(f.tails) > 0 {
		f.started = true
		go f.run()
	}
}

func (f *Forwarder) Stop() {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.started {
		close(f.stop)
		f.started = false
	}
}

func (f *Forwarder) run() {
	ticker := time.NewTicker(pollInterval)
	defer ticker.Stop()
	for {
		select {
		case <-f.stop:
			return
		case <-ticker.C:
			f.poll()
		}
	}
}

func (f *Forwarder) poll() {
	// Snapshot the active set so we don't hold the lock during file IO / shipping.
	f.mu.Lock()
	paths := make([]string, 0, len(f.tails))
	for p := range f.tails {
		paths = append(paths, p)
	}
	f.mu.Unlock()

	for _, p := range paths {
		f.mu.Lock()
		t := f.tails[p]
		f.mu.Unlock()
		if t == nil {
			continue
		}
		lines := readNew(p, t)
		if len(lines) > 0 {
			if err := f.shipper.SendLogs(t.source, lines); err != nil {
				log.Printf("logforward: ship %s (%s): %v", p, t.source, err)
			}
		}
	}
}

// readNew reads lines appended since t.offset, handling rotation: if the inode
// changed or the file shrank (truncate/rotate), it re-reads from the start.
func readNew(path string, t *tail) []string {
	fi, err := os.Stat(path)
	if err != nil {
		return nil // file gone (mid-rotation); pick it up next poll
	}
	ino := inodeOf(fi)
	if ino != t.inode || fi.Size() < t.offset {
		t.offset = 0 // rotated/truncated → start over
		t.inode = ino
	}
	if fi.Size() == t.offset {
		return nil // nothing new
	}
	fh, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer fh.Close()
	if _, err := fh.Seek(t.offset, 0); err != nil {
		return nil
	}
	var lines []string
	sc := bufio.NewScanner(fh)
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024) // tolerate long lines
	read := int64(0)
	for sc.Scan() {
		line := sc.Text()
		read += int64(len(line)) + 1
		if line != "" {
			lines = append(lines, line)
		}
		if len(lines) >= maxLinesPerPoll {
			break
		}
	}
	t.offset += read
	return lines
}

func inodeOf(fi os.FileInfo) uint64 {
	if st, ok := fi.Sys().(*syscall.Stat_t); ok {
		return st.Ino
	}
	return 0
}
