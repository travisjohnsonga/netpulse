//go:build windows

package service

import (
	"fmt"
	"os"

	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/mgr"
)

const (
	serviceName    = "NetPulseAgent"
	serviceDisplay = "NetPulse Monitoring Agent"
	serviceDesc    = "Secure infrastructure monitoring agent for NetPulse"
)

func Install(configPath string) error {
	exePath, err := os.Executable()
	if err != nil {
		return err
	}
	m, err := mgr.Connect()
	if err != nil {
		return fmt.Errorf("connect to SCM: %w", err)
	}
	defer m.Disconnect()

	s, err := m.CreateService(serviceName, exePath, mgr.Config{
		DisplayName:      serviceDisplay,
		Description:      serviceDesc,
		StartType:        mgr.StartAutomatic,
		ServiceStartName: "LocalSystem",
	}, "--config", configPath)
	if err != nil {
		return fmt.Errorf("create service: %w", err)
	}
	defer s.Close()
	return s.Start()
}

func Uninstall() error {
	m, err := mgr.Connect()
	if err != nil {
		return err
	}
	defer m.Disconnect()

	s, err := m.OpenService(serviceName)
	if err != nil {
		return fmt.Errorf("service not found: %w", err)
	}
	defer s.Close()
	_, _ = s.Control(svc.Stop)
	return s.Delete()
}
