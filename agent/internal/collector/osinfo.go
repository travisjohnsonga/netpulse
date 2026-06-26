package collector

import "strings"

// OSInfo is the human-facing OS identity the agent reports IN ADDITION to
// runtime.GOOS (which stays the os_family the agent branches on). Name is a
// display string ("Ubuntu 22.04.3 LTS", "Windows Server 2025"); Version is the
// machine version id ("22.04", "24H2"); Kernel is the kernel/build ("6.8.0-31",
// "26100"). All are best-effort — empty when undetectable, so the server/UI can
// fall back to os_family.
type OSInfo struct {
	Name    string `json:"os_name"`
	Version string `json:"os_version"`
	Kernel  string `json:"kernel"`
}

// parseOSRelease extracts a display name + version from /etc/os-release content
// (the freedesktop standard). It's pure (takes the file body) so it's testable
// on any platform. Precedence for the name: PRETTY_NAME, then NAME + VERSION_ID,
// then "Linux" when the file is absent/empty. Version is VERSION_ID.
func parseOSRelease(content string) OSInfo {
	kv := map[string]string{}
	for _, line := range strings.Split(content, "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		k, v, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		// Values may be quoted ("Ubuntu 22.04.3 LTS") or bare (22.04).
		kv[strings.TrimSpace(k)] = strings.Trim(strings.TrimSpace(v), `"'`)
	}

	info := OSInfo{Version: kv["VERSION_ID"]}
	switch {
	case kv["PRETTY_NAME"] != "":
		info.Name = kv["PRETTY_NAME"]
	case kv["NAME"] != "":
		info.Name = strings.TrimSpace(kv["NAME"] + " " + kv["VERSION_ID"])
	default:
		info.Name = "Linux"
	}
	return info
}

// composeWindowsName builds an OSInfo from the registry values read on Windows
// (CurrentVersion\ProductName, DisplayVersion|ReleaseId, CurrentBuild). Pure +
// not build-tagged so the Windows naming logic is testable on Linux CI. Name
// falls back to "Windows"; Version prefers DisplayVersion, then the build.
func composeWindowsName(productName, displayVersion, build string) OSInfo {
	name := strings.TrimSpace(productName)
	if name == "" {
		name = "Windows"
	}
	version := strings.TrimSpace(displayVersion)
	if version == "" {
		version = strings.TrimSpace(build)
	}
	return OSInfo{Name: name, Version: version, Kernel: strings.TrimSpace(build)}
}
