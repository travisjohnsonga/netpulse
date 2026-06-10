module github.com/travisjohnsonga/netpulse/agent

go 1.22

// Windows-only collectors (build-tagged) use these; the Linux/core build is
// stdlib-only and links none of them. CI runs `go mod tidy` before building.
require (
	github.com/StackExchange/wmi v1.2.1
	golang.org/x/sys v0.18.0
)

require github.com/go-ole/go-ole v1.2.5 // indirect
