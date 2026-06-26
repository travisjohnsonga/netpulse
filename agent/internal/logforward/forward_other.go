//go:build !linux

package logforward

// Shipper mirrors the Linux build's interface so callers compile cross-platform.
type Shipper interface {
	SendLogs(source string, lines []string) error
}

// Forwarder is a no-op on non-Linux: the Stage-1 security profile (auth/service/
// kernel) is Linux-only. Windows/web log profiles are a later stage.
type Forwarder struct{}

func New(_ Shipper) *Forwarder { return &Forwarder{} }

func (f *Forwarder) Apply(_ bool, _ []string) {}
func (f *Forwarder) Stop()                    {}
