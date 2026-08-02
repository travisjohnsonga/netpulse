package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSaveAtomicRoundTrip(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.json")

	cfg := &Config{ServerURL: "https://srv", AgentID: "a1"}
	cfg.Collection.Interval = 45
	cfg.Disk.ExcludeMounts = []string{"D:"}
	if err := Save(path, cfg); err != nil {
		t.Fatalf("Save: %v", err)
	}

	got, err := Load(path)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if got.Collection.Interval != 45 || len(got.Disk.ExcludeMounts) != 1 || got.Disk.ExcludeMounts[0] != "D:" {
		t.Fatalf("round-trip mismatch: %+v", got)
	}

	// Atomic write must leave no stray temp files behind in the dir.
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), ".config-") {
			t.Fatalf("leftover temp file: %s", e.Name())
		}
	}
}

func TestSaveOverwritesExisting(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.json")
	if err := os.WriteFile(path, []byte("STALE"), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg := &Config{AgentID: "fresh"}
	if err := Save(path, cfg); err != nil {
		t.Fatalf("Save over existing: %v", err)
	}
	got, err := Load(path)
	if err != nil || got.AgentID != "fresh" {
		t.Fatalf("expected overwrite to fresh, got %+v err=%v", got, err)
	}
}
