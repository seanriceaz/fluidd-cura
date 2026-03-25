#!/usr/bin/env bash
# =============================================================================
#  repair-cura-engine.sh — Fix CuraEngine path in moonraker.conf
#
#  Run this if CuraEngine was installed after the main install, or if the
#  slicer reports "CuraEngine not found" at runtime.
#
#  Usage:
#    chmod +x repair-cura-engine.sh
#    ./repair-cura-engine.sh [/path/to/CuraEngine]
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()  { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()    { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
die()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }

INSTALL_USER="${SUDO_USER:-${USER:-pi}}"
INSTALL_HOME="$(eval echo ~"$INSTALL_USER")"

# =============================================================================
# 1. Find CuraEngine
# =============================================================================
CURA_BIN="${1:-}"

find_engine() {
  # Explicit paths to probe
  for candidate in \
      CuraEngine \
      /usr/bin/CuraEngine \
      /usr/local/bin/CuraEngine \
      /usr/lib/cura-engine/CuraEngine \
      /opt/cura/CuraEngine; do
    if command -v "$candidate" &>/dev/null; then
      echo "$(command -v "$candidate")"
      return 0
    elif [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done

  # Try dpkg if cura-engine package is installed
  if dpkg -l cura-engine &>/dev/null 2>&1; then
    local dp
    dp="$(dpkg -L cura-engine 2>/dev/null | grep -m1 'CuraEngine$' || true)"
    if [ -x "$dp" ]; then
      echo "$dp"
      return 0
    fi
  fi

  return 1
}

if [ -z "$CURA_BIN" ]; then
  info "Searching for CuraEngine…"
  CURA_BIN="$(find_engine)" || true
fi

if [ -z "$CURA_BIN" ] || [ ! -x "$CURA_BIN" ]; then
  echo ""
  warn "CuraEngine not found automatically."
  echo ""
  echo "  Install it first:"
  echo "    sudo apt-get install cura-engine"
  echo "  or pass the path directly:"
  echo "    ./repair-cura-engine.sh /path/to/CuraEngine"
  echo ""
  read -rp "Enter path to CuraEngine binary (or press Enter to abort): " CURA_BIN
  [ -n "$CURA_BIN" ] || die "Aborted."
  [ -x "$CURA_BIN" ] || die "Not executable: $CURA_BIN"
fi

ok "CuraEngine found at: $CURA_BIN"

VER="$("$CURA_BIN" --version 2>&1 | grep -oP '\d+\.\d+' | head -1 || true)"
[ -n "$VER" ] && ok "Version: $VER" || warn "Could not determine version."

# =============================================================================
# 2. Find moonraker.conf
# =============================================================================
MOONRAKER_CONF=""
for candidate in \
    "$INSTALL_HOME/printer_data/config/moonraker.conf" \
    "/home/pi/printer_data/config/moonraker.conf" \
    "/home/klipper/printer_data/config/moonraker.conf" \
    "$INSTALL_HOME/klipper_config/moonraker.conf" \
    "/home/pi/klipper_config/moonraker.conf"; do
  if [ -f "$candidate" ]; then
    MOONRAKER_CONF="$candidate"
    break
  fi
done

if [ -z "$MOONRAKER_CONF" ]; then
  warn "Could not auto-detect moonraker.conf."
  read -rp "Enter full path to moonraker.conf: " MOONRAKER_CONF
  [ -f "$MOONRAKER_CONF" ] || die "File not found: $MOONRAKER_CONF"
fi

ok "moonraker.conf: $MOONRAKER_CONF"

# =============================================================================
# 3. Update / insert cura_engine_path
# =============================================================================
if grep -q '^\[cura_slicer\]' "$MOONRAKER_CONF"; then
  if grep -q '^cura_engine_path' "$MOONRAKER_CONF"; then
    info "Updating existing cura_engine_path…"
    sudo sed -i "s|^cura_engine_path:.*|cura_engine_path: ${CURA_BIN}|" "$MOONRAKER_CONF"
  else
    info "Inserting cura_engine_path under [cura_slicer]…"
    sudo sed -i "/^\[cura_slicer\]/a cura_engine_path: ${CURA_BIN}" "$MOONRAKER_CONF"
  fi
  ok "cura_engine_path set to: $CURA_BIN"
else
  info "[cura_slicer] section not found — adding it."
  cat | sudo tee -a "$MOONRAKER_CONF" > /dev/null <<EOF

# ── Cura Slicer (added by repair-cura-engine.sh) ──
[cura_slicer]
cura_engine_path: ${CURA_BIN}
EOF
  ok "[cura_slicer] section added."
fi

# =============================================================================
# 4. Restart Moonraker
# =============================================================================
for svc in moonraker moonraker.service; do
  if systemctl list-units --quiet --no-pager "$svc" 2>/dev/null | grep -q "$svc"; then
    info "Restarting $svc…"
    sudo systemctl restart "$svc"
    ok "Moonraker restarted."
    break
  fi
done

echo ""
echo -e "${GREEN}${BOLD}Repair complete.${RESET}"
echo "  CuraEngine: $CURA_BIN"
echo "  Config:     $MOONRAKER_CONF"
echo ""
echo "  Verify by visiting: http://$(hostname -I | awk '{print $1}')/server/cura_slicer/status"
echo ""
