#!/usr/bin/env bash
#
# Download publicly available SNMP MIB files for OID resolution.
#   - Standard RFC MIBs   : github.com/net-snmp/net-snmp
#   - Cisco MIBs          : github.com/cisco/cisco-mibs
#   - All other vendors   : github.com/librenms/librenms  (mibs/, by vendor)
#
# LibreNMS stores MIBs WITHOUT a file extension (the filename is the module
# name), so we copy them with a .mib suffix — that's what the parser/index and
# .gitignore key on. Re-runnable.
set -u
export GIT_TERMINAL_PROMPT=0   # never prompt for credentials on a missing/private repo

cd "$(dirname "$0")/.." || exit 1

echo "Downloading SNMP MIB files into mibs/ ..."

for d in standard vendor/cisco vendor/fortinet vendor/juniper vendor/arista \
         vendor/sonicwall vendor/aos-cx vendor/aruba vendor/paloalto \
         vendor/mikrotik vendor/community custom; do
  mkdir -p "mibs/$d"
done

# ── Standard RFC MIBs (net-snmp — public) ─────────────────────────────────────
BASE="https://raw.githubusercontent.com/net-snmp/net-snmp/master/mibs"
for mib in SNMPv2-SMI SNMPv2-TC SNMPv2-MIB RFC1213-MIB IF-MIB IP-MIB \
           TCP-MIB UDP-MIB HOST-RESOURCES-MIB ENTITY-MIB ENTITY-SENSOR-MIB \
           POWER-ETHERNET-MIB DISMAN-EVENT-MIB; do
  curl -sf "$BASE/$mib.txt" -o "mibs/standard/$mib.my" 2>/dev/null \
    || echo "  WARNING: standard $mib not found"
done
# POWER-ETHERNET-MIB (RFC 3621, PoE) — for reference only; NetPulse reads the
# pethMainPse OIDs raw (the MIB isn't required for collection). net-snmp may not
# carry it, so fall back to circitor's archive.
if [ ! -s "mibs/standard/POWER-ETHERNET-MIB.my" ]; then
  curl -sf "https://www.circitor.fr/Mibs/Files/P/POWER-ETHERNET-MIB.mib" \
    -o "mibs/standard/POWER-ETHERNET-MIB.mib" 2>/dev/null \
    || echo "  WARNING: POWER-ETHERNET-MIB not found (PoE still works — read raw)"
fi
echo "  standard: $(find mibs/standard -type f \( -name '*.my' -o -name '*.mib' \) | wc -l) MIBs"

# ── Cisco MIBs (github.com/cisco/cisco-mibs — public) ─────────────────────────
tmp="$(mktemp -d)"
if git clone --depth=1 --quiet https://github.com/cisco/cisco-mibs.git "$tmp" 2>/dev/null; then
  cp "$tmp"/v2/*.my mibs/vendor/cisco/ 2>/dev/null
  echo "  cisco: $(find mibs/vendor/cisco -maxdepth 1 -name '*.my' | wc -l) MIBs"
else
  echo "  WARNING: could not clone cisco-mibs"
fi
rm -rf "$tmp"

# ── Vendor MIBs (LibreNMS — comprehensive public collection) ──────────────────
echo "Downloading vendor MIBs from LibreNMS..."
LNMS="$(mktemp -d)"
if git clone --depth=1 --quiet --filter=blob:none --sparse \
     https://github.com/librenms/librenms.git "$LNMS" 2>/dev/null \
   && git -C "$LNMS" sparse-checkout set mibs >/dev/null 2>&1; then

  # copy_lnms <librenms-mibs-subdir> <dest-vendor-dir>
  # LibreNMS MIBs are extensionless → add .mib so the index + .gitignore match.
  copy_lnms() {
    local src="$LNMS/mibs/$1" dest="$2" n=0 f base
    [ -d "$src" ] || { echo "  ${dest##*/}: (no LibreNMS dir '$1')"; return 0; }
    mkdir -p "$dest"
    for f in "$src"/*; do
      [ -f "$f" ] || continue
      base="$(basename "$f")"
      case "$base" in
        *.my|*.mib|*.txt) cp "$f" "$dest/$base" ;;
        *)                cp "$f" "$dest/$base.mib" ;;
      esac
      n=$((n + 1))
    done
    echo "  ${dest##*/}: $n MIBs"
  }

  copy_lnms fortinet         mibs/vendor/fortinet
  copy_lnms arubaos          mibs/vendor/aruba
  copy_lnms arubaos-cx       mibs/vendor/aos-cx
  copy_lnms sonicwall        mibs/vendor/sonicwall
  copy_lnms juniper          mibs/vendor/juniper
  copy_lnms arista           mibs/vendor/arista
  copy_lnms paloaltonetworks mibs/vendor/paloalto
  copy_lnms mikrotik         mibs/vendor/mikrotik
  echo "LibreNMS MIB download complete."
else
  echo "❌ LibreNMS MIB download failed — try manually:"
  echo "   https://github.com/librenms/librenms/tree/master/mibs"
fi
rm -rf "$LNMS"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=== MIB Summary ==="
for dir in mibs/vendor/*/; do
  count=$(find "$dir" -type f \( -name '*.mib' -o -name '*.my' \) 2>/dev/null | wc -l)
  [ "$count" -gt 0 ] && echo "  $(basename "$dir"): $count files"
done
echo "  standard: $(find mibs/standard -type f \( -name '*.mib' -o -name '*.my' \) | wc -l) files"
echo "  TOTAL: $(find mibs -type f \( -name '*.mib' -o -name '*.my' \) | wc -l) MIB files"
