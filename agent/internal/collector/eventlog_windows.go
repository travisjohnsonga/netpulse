//go:build windows

package collector

import (
	"time"

	"github.com/StackExchange/wmi"
)

type EventLogEntry struct {
	EventID uint32 `json:"event_id"`
	Level   string `json:"level"`
	Source  string `json:"source"`
	Message string `json:"message"`
	Channel string `json:"channel"`
}

type win32NTLogEvent struct {
	EventCode  uint32
	Type       string
	SourceName string
	Message    string
	LogFile    string
}

// CollectEventLog returns recent error/warning events since the given time.
// Phase 1 uses WMI (Win32_NTLogEvent) — simpler than the wevtapi.
func CollectEventLog(since time.Time) ([]EventLogEntry, error) {
	query := `SELECT EventCode, Type, SourceName, Message, LogFile FROM Win32_NTLogEvent ` +
		`WHERE (Type='error' OR Type='warning') AND TimeGenerated > '` +
		since.Format("20060102150405") + `.000000+000'`
	var events []win32NTLogEvent
	if err := wmi.Query(query, &events); err != nil {
		return nil, err
	}
	entries := make([]EventLogEntry, 0, len(events))
	for _, e := range events {
		entries = append(entries, EventLogEntry{
			EventID: e.EventCode, Level: e.Type, Source: e.SourceName,
			Message: e.Message, Channel: e.LogFile,
		})
	}
	return entries, nil
}
