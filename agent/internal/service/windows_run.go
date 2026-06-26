//go:build windows

package service

import "golang.org/x/sys/windows/svc"

// Runnable is the minimal agent surface the service handler drives. Defined as
// an interface here (rather than importing the agent package) to avoid a
// service→agent import cycle; *agent.Agent satisfies it.
type Runnable interface {
	Run() error
	Stop()
}

// IsWindowsService reports whether the process was started by the Service
// Control Manager (vs. launched interactively).
func IsWindowsService() (bool, error) {
	return svc.IsWindowsService()
}

// handler implements svc.Handler: it bridges the SCM control protocol to the
// agent's Run()/Stop() lifecycle. Without this the SCM-launched process never
// reports Running, so Windows kills it ("did not respond to the start request").
type handler struct{ r Runnable }

func (h *handler) Execute(_ []string, req <-chan svc.ChangeRequest, status chan<- svc.Status) (bool, uint32) {
	const accepted = svc.AcceptStop | svc.AcceptShutdown
	status <- svc.Status{State: svc.StartPending}

	// Run the agent loop in the background; capture its exit so we can react if
	// it dies on its own.
	errCh := make(chan error, 1)
	go func() { errCh <- h.r.Run() }()

	status <- svc.Status{State: svc.Running, Accepts: accepted}
	for {
		select {
		case c := <-req:
			switch c.Cmd {
			case svc.Interrogate:
				status <- c.CurrentStatus
			case svc.Stop, svc.Shutdown:
				status <- svc.Status{State: svc.StopPending}
				h.r.Stop()
				<-errCh // wait for Run() to unwind
				status <- svc.Status{State: svc.Stopped}
				return false, 0
			default:
				// Ignore other controls.
			}
		case err := <-errCh:
			// The agent loop exited on its own — report the service stopped, with
			// a non-zero exit code on error so the SCM can apply recovery actions.
			status <- svc.Status{State: svc.StopPending}
			status <- svc.Status{State: svc.Stopped}
			if err != nil {
				return false, 1
			}
			return false, 0
		}
	}
}

// RunWindowsService runs the agent under the SCM, blocking until the service is
// stopped. Called from main only when IsWindowsService() is true.
func RunWindowsService(r Runnable) error {
	return svc.Run(serviceName, &handler{r: r})
}
