#!/usr/bin/env bash
#
# Download publicly available SNMP MIB files for OID resolution.
# Pulls standard RFC MIBs from net-snmp and vendor MIBs from public GitHub repos.
# Re-runnable; missing sources are warned about, not fatal.
set -u

cd "$(dirname "$0")/.." || exit 1

echo "Downloading SNMP MIB files into mibs/ ..."

for d in standard vendor/cisco vendor/fortinet vendor/juniper vendor/arista \
         vendor/sonicwall vendor/aos-cx vendor/aruba vendor/community custom; do
  mkdir -p "mibs/$d"
done

# ── Standard RFC MIBs (net-snmp) ──────────────────────────────────────────────
BASE="https://raw.githubusercontent.com/net-snmp/net-snmp/master/mibs"
for mib in SNMPv2-SMI SNMPv2-TC SNMPv2-MIB RFC1213-MIB IF-MIB IP-MIB \
           TCP-MIB UDP-MIB HOST-RESOURCES-MIB ENTITY-MIB DISMAN-EVENT-MIB; do
  echo "  standard: $mib"
  curl -sf "$BASE/$mib.txt" -o "mibs/standard/$mib.my" \
    || echo "    WARNING: $mib not found"
done

# ── helper: shallow-clone a repo and copy MIB files into a vendor dir ─────────
clone_copy() {  # <repo-url> <dest-dir> <src-subdir-or-.>
  local url="$1" dest="$2" sub="${3:-.}" tmp
  tmp="$(mktemp -d)"
  echo "  cloning $(basename "$url" .git) ..."
  if git clone --depth=1 --quiet "$url" "$tmp" 2>/dev/null; then
    cp "$tmp/$sub"/*.my  "$dest"/ 2>/dev/null
    cp "$tmp/$sub"/*.mib "$dest"/ 2>/dev/null
    cp "$tmp/$sub"/*.txt "$dest"/ 2>/dev/null
    echo "    -> $(find "$dest" -maxdepth 1 -type f \( -name '*.my' -o -name '*.mib' -o -name '*.txt' \) | wc -l) file(s) in $dest"
  else
    echo "    WARNING: could not clone $url (offline? private?) — see $dest/README.md"
  fi
  rm -rf "$tmp"
}

clone_copy https://github.com/cisco/cisco-mibs.git           mibs/vendor/cisco     v2
clone_copy https://github.com/fortinet/fortios-mibs.git      mibs/vendor/fortinet  .
clone_copy https://github.com/aristanetworks/eos-snmp-mibs.git mibs/vendor/arista  .
clone_copy https://github.com/sonicwall/sonicwall-mibs.git   mibs/vendor/sonicwall .
clone_copy https://github.com/aruba/aruba-mibs.git           mibs/vendor/aruba     .
# HPE AOS-CX — try a couple of known org paths.
clone_copy https://github.com/aruba/aos-cx-mibs.git          mibs/vendor/aos-cx    . \
  || clone_copy https://github.com/hpe-networking/aos-cx-mibs.git mibs/vendor/aos-cx .

echo ""
echo "MIB download complete. Files in mibs/:"
find mibs -type f \( -name '*.my' -o -name '*.mib' \) | sort | sed 's/^/  /'
echo ""
echo "Note: standard MIBs are git-ignored; small vendor MIBs (fortinet/arista) are"
echo "committed. Re-run any time to refresh."
