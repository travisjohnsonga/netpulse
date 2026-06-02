#!/usr/bin/env bash
#
# Download publicly available SNMP MIB files for OID resolution.
#
# Only two sources are reliably public + cloneable: net-snmp (standard RFC MIBs)
# and cisco/cisco-mibs. Other vendors require an authenticated portal download —
# this script tries a best-effort circitor.fr fallback for a few and otherwise
# prints manual-download instructions. Re-runnable.
set -u
export GIT_TERMINAL_PROMPT=0   # never prompt for git credentials on a 404/private repo

cd "$(dirname "$0")/.." || exit 1

echo "Downloading SNMP MIB files into mibs/ ..."

for d in standard vendor/cisco vendor/fortinet vendor/juniper vendor/arista \
         vendor/sonicwall vendor/aos-cx vendor/aruba vendor/community custom; do
  mkdir -p "mibs/$d"
done

# ── Standard RFC MIBs (net-snmp — public) ─────────────────────────────────────
BASE="https://raw.githubusercontent.com/net-snmp/net-snmp/master/mibs"
for mib in SNMPv2-SMI SNMPv2-TC SNMPv2-MIB RFC1213-MIB IF-MIB IP-MIB \
           TCP-MIB UDP-MIB HOST-RESOURCES-MIB ENTITY-MIB DISMAN-EVENT-MIB; do
  echo "  standard: $mib"
  curl -sf "$BASE/$mib.txt" -o "mibs/standard/$mib.my" \
    || echo "    WARNING: $mib not found"
done

# ── Cisco MIBs (github.com/cisco/cisco-mibs — public) ─────────────────────────
echo "  cloning cisco/cisco-mibs ..."
tmp="$(mktemp -d)"
if git clone --depth=1 --quiet https://github.com/cisco/cisco-mibs.git "$tmp" 2>/dev/null; then
  cp "$tmp"/v2/*.my mibs/vendor/cisco/ 2>/dev/null
  echo "    -> $(find mibs/vendor/cisco -maxdepth 1 -name '*.my' | wc -l) Cisco MIB(s)"
else
  echo "    WARNING: could not clone cisco-mibs (offline?)"
fi
rm -rf "$tmp"

# ── Best-effort circitor.fr fallback (public mirror; many MIBs, no guarantees) ─
CIRC="https://www.circitor.fr/Mibs/Mib"
try_circitor() {  # <letter> <MIB-NAME> <dest-dir>
  curl -sf "$CIRC/$1/$2.mib" -o "$3/$2.mib" 2>/dev/null \
    && echo "    circitor: $2" || echo "    circitor: $2 not available"
}
echo "  trying circitor.fr fallbacks ..."
try_circitor F FORTINET-CORE-MIB      mibs/vendor/fortinet
try_circitor F FORTINET-FORTIGATE-MIB mibs/vendor/fortinet
try_circitor J JUNIPER-SMI            mibs/vendor/juniper
try_circitor J JUNIPER-MIB            mibs/vendor/juniper
try_circitor A ARUBA-MIB              mibs/vendor/aruba

echo ""
echo "MIBs present in mibs/:"
find mibs -type f \( -name '*.my' -o -name '*.mib' \) | sort | sed 's/^/  /'

# ── Manual downloads (vendor portals require authentication) ──────────────────
cat <<'EOF'

==================================================
  Manual MIB Downloads (authenticated portals)
==================================================
Vendor MIBs below are not publicly cloneable. Download from the vendor portal
and extract into the matching mibs/vendor/<name>/ directory:

Fortinet FortiOS:
  https://support.fortinet.com  → Download → Firmware Images → MIBs
  → mibs/vendor/fortinet/

Arista EOS:
  https://www.arista.com/en/support/software-download  → MIBs
  → mibs/vendor/arista/

Aruba AOS (controllers/APs):
  https://asp.arubanetworks.com  → Downloads → Software → MIBs
  → mibs/vendor/aruba/

HPE AOS-CX:
  https://networkingsupport.hpe.com  → search "AOS-CX MIB"
  → mibs/vendor/aos-cx/

SonicWall SonicOS:
  https://www.sonicwall.com/support/  → Downloads → MIBs
  → mibs/vendor/sonicwall/

Juniper Junos:
  https://www.juniper.net/documentation/  → SNMP MIB Explorer
  → mibs/vendor/juniper/
==================================================
EOF
