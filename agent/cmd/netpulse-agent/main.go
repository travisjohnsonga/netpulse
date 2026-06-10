// Command netpulse-agent is a lightweight server-monitoring agent that enrolls
// with a NetPulse server (one-time token → mTLS client cert) and pushes
// metrics + role-check results over HTTPS. Linux + Windows; single static
// binary, no runtime dependencies on the core (Linux) build.
package main

import (
	"flag"
	"log"
	"os"
	"os/signal"
	"syscall"

	"github.com/travisjohnsonga/netpulse/agent/internal/agent"
	"github.com/travisjohnsonga/netpulse/agent/internal/config"
	"github.com/travisjohnsonga/netpulse/agent/internal/service"
)

// Version is set at build time via -ldflags "-X main.Version=...".
var Version = "dev"

func main() {
	var (
		configPath       = flag.String("config", config.DefaultPath(), "Path to config file")
		enrollToken      = flag.String("enroll", "", "Enrollment token for first-time setup")
		serverURL        = flag.String("server", "", "NetPulse server URL")
		installService   = flag.Bool("install-service", false, "Install the OS service (Linux systemd / Windows)")
		uninstallService = flag.Bool("uninstall-service", false, "Uninstall the OS service (Linux systemd / Windows)")
		insecure         = flag.Bool("insecure", false, "Skip TLS cert verification (dev/self-signed)")
		showVersion      = flag.Bool("version", false, "Print version and exit")
	)
	flag.Parse()

	if *showVersion {
		log.Printf("netpulse-agent %s", Version)
		return
	}

	if *enrollToken != "" {
		if err := agent.Enroll(*serverURL, *enrollToken, *configPath, *insecure); err != nil {
			log.Fatalf("Enrollment failed: %v", err)
		}
		log.Println("Enrollment successful!")
		return
	}

	if *installService {
		if err := service.Install(*configPath); err != nil {
			log.Fatalf("Service install failed: %v", err)
		}
		log.Println("Service installed.")
		return
	}
	if *uninstallService {
		if err := service.Uninstall(); err != nil {
			log.Fatalf("Service uninstall failed: %v", err)
		}
		log.Println("Service uninstalled.")
		return
	}

	cfg, err := config.Load(*configPath)
	if err != nil {
		log.Fatalf("Failed to load config: %v", err)
	}

	a, err := agent.New(cfg, Version)
	if err != nil {
		log.Fatalf("Failed to create agent: %v", err)
	}

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		if err := a.Run(); err != nil {
			log.Fatalf("Agent error: %v", err)
		}
	}()
	<-quit
	log.Println("Shutting down agent...")
	a.Stop()
}
