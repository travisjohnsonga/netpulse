//go:build windows

package collector

import "golang.org/x/sys/windows/registry"

// CollectOSInfo reads the product name + version from the Windows registry
// (HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion) — no WMI/PowerShell
// dependency. ProductName gives "Windows Server 2025" / "Windows 11 Pro";
// DisplayVersion (newer) or ReleaseId (older) gives "24H2"/"22H2"; CurrentBuild
// gives the build number. The naming logic lives in composeWindowsName (pure,
// tested on Linux); this just supplies the registry values.
func CollectOSInfo() OSInfo {
	k, err := registry.OpenKey(registry.LOCAL_MACHINE,
		`SOFTWARE\Microsoft\Windows NT\CurrentVersion`, registry.QUERY_VALUE)
	if err != nil {
		return OSInfo{Name: "Windows"}
	}
	defer k.Close()

	get := func(name string) string {
		v, _, _ := k.GetStringValue(name)
		return v
	}
	product := get("ProductName")
	version := get("DisplayVersion")
	if version == "" {
		version = get("ReleaseId")
	}
	return composeWindowsName(product, version, get("CurrentBuild"))
}
