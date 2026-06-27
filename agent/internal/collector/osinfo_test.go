package collector

import "testing"

func TestParseOSRelease(t *testing.T) {
	cases := []struct {
		name        string
		content     string
		wantName    string
		wantVersion string
	}{
		{
			name: "ubuntu pretty_name",
			content: `NAME="Ubuntu"
VERSION="22.04.3 LTS (Jammy Jellyfish)"
ID=ubuntu
PRETTY_NAME="Ubuntu 22.04.3 LTS"
VERSION_ID="22.04"`,
			wantName:    "Ubuntu 22.04.3 LTS",
			wantVersion: "22.04",
		},
		{
			name: "almalinux pretty_name",
			content: `NAME="AlmaLinux"
VERSION="9.4 (Seafoam Ocelot)"
ID="almalinux"
PRETTY_NAME="AlmaLinux 9.4 (Seafoam Ocelot)"
VERSION_ID="9.4"`,
			wantName:    "AlmaLinux 9.4 (Seafoam Ocelot)",
			wantVersion: "9.4",
		},
		{
			name: "debian pretty_name",
			content: `PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"
NAME="Debian GNU/Linux"
VERSION_ID="12"
VERSION="12 (bookworm)"
ID=debian`,
			wantName:    "Debian GNU/Linux 12 (bookworm)",
			wantVersion: "12",
		},
		{
			name:        "no pretty_name falls back to NAME + VERSION_ID",
			content:     "NAME=\"Alpine Linux\"\nVERSION_ID=3.19.1\nID=alpine",
			wantName:    "Alpine Linux 3.19.1",
			wantVersion: "3.19.1",
		},
		{
			name:        "missing file -> Linux",
			content:     "",
			wantName:    "Linux",
			wantVersion: "",
		},
		{
			name:        "comments and blanks ignored",
			content:     "# a comment\n\nPRETTY_NAME='Fedora Linux 40'\nVERSION_ID=40\n",
			wantName:    "Fedora Linux 40",
			wantVersion: "40",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := parseOSRelease(tc.content)
			if got.Name != tc.wantName {
				t.Errorf("Name = %q, want %q", got.Name, tc.wantName)
			}
			if got.Version != tc.wantVersion {
				t.Errorf("Version = %q, want %q", got.Version, tc.wantVersion)
			}
		})
	}
}

func TestComposeWindowsName(t *testing.T) {
	cases := []struct {
		name                            string
		product, display, build         string
		wantName, wantVersion, wantKern string
	}{
		{
			name:    "server 2025 with display version",
			product: "Windows Server 2025 Datacenter", display: "24H2", build: "26100",
			wantName: "Windows Server 2025 Datacenter", wantVersion: "24H2", wantKern: "26100",
		},
		{
			name:    "win11 fall back to build when no display version",
			product: "Windows 11 Pro", display: "", build: "22631",
			wantName: "Windows 11 Pro", wantVersion: "22631", wantKern: "22631",
		},
		{
			name:    "empty product -> Windows",
			product: "", display: "", build: "19045",
			wantName: "Windows", wantVersion: "19045", wantKern: "19045",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := composeWindowsName(tc.product, tc.display, tc.build)
			if got.Name != tc.wantName || got.Version != tc.wantVersion || got.Kernel != tc.wantKern {
				t.Errorf("composeWindowsName = %+v, want {%q %q %q}",
					got, tc.wantName, tc.wantVersion, tc.wantKern)
			}
		})
	}
}
