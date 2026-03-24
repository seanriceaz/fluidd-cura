#!/usr/bin/env bash
# =============================================================================
#  uninstall.sh — Remove Fluidd-Cura Slicer Integration
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()      { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
die()     { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }
heading() { echo -e "\n${BOLD}${CYAN}── $* ──${RESET}"; }

INSTALL_USER="${SUDO_USER:-${USER:-pi}}"
INSTALL_HOME="$(eval echo ~"$INSTALL_USER")"

echo -e "${BOLD}${RED}Fluidd-Cura Slicer — Uninstaller${RESET}"
echo ""
warn "This will remove the Moonraker plugin, nginx config, and web UI."
warn "Your sliced gcode files and cura profiles/definitions will NOT be deleted."
echo ""
read -rp "Continue? (y/N) " CONFIRM
[[ "$CONFIRM" =~ ^[Yy]$ ]] || die "Aborted."

# ── Remove Moonraker plugin ───────────────────────────────────────────────────
heading "Removing Moonraker plugin"

MOONRAKER_DIR=""
for d in "$INSTALL_HOME/moonraker" "/home/pi/moonraker" "/opt/moonraker"; do
  [ -f "$d/moonraker/server.py" ] && MOONRAKER_DIR="$d" && break
done

if [ -n "$MOONRAKER_DIR" ]; then
  PLUGIN="$MOONRAKER_DIR/moonraker/components/cura_slicer.py"
  if [ -f "$PLUGIN" ]; then
    sudo rm "$PLUGIN"
    ok "Removed $PLUGIN"
  else
    info "Plugin not found – skipping."
  fi
else
  warn "Moonraker directory not found – skipping plugin removal."
fi

# ── Remove moonraker.conf section ────────────────────────────────────────────
heading "Cleaning moonraker.conf"

MOONRAKER_DATA=""
for d in "$INSTALL_HOME/printer_data" "/home/pi/printer_data"; do
  [ -d "$d" ] && MOONRAKER_DATA="$d" && break
done

MOONRAKER_CONF="${MOONRAKER_DATA}/config/moonraker.conf"
if [ -f "$MOONRAKER_CONF" ]; then
  # Remove the [cura_slicer] block (section + all its lines until the next section)
  sudo python3 - "$MOONRAKER_CONF" <<'PYEOF'
import sys, re
path = sys.argv[1]
with open(path) as f:
    content = f.read()
# Remove the cura_slicer section and trailing comment block added by installer
cleaned = re.sub(
    r'\n# ── Cura Slicer.*?\[cura_slicer\][^\[]*',
    '',
    content,
    flags=re.DOTALL
)
# Also remove a plain [cura_slicer] section if the comment wasn't there
cleaned = re.sub(
    r'\n\[cura_slicer\][^\[]*',
    '',
    cleaned,
    flags=re.DOTALL
)
with open(path, 'w') as f:
    f.write(cleaned)
print(f"Cleaned: {path}")
PYEOF
  ok "moonraker.conf cleaned."
else
  warn "moonraker.conf not found – skipping."
fi

# ── Remove nginx snippet ──────────────────────────────────────────────────────
heading "Removing nginx config"

NGINX_SNIPPET="/etc/nginx/snippets/cura-slicer.conf"
if [ -f "$NGINX_SNIPPET" ]; then
  sudo rm "$NGINX_SNIPPET"
  ok "Removed $NGINX_SNIPPET"
fi

# Remove include from site config
for site in /etc/nginx/sites-enabled/fluidd /etc/nginx/sites-enabled/mainsail \
            /etc/nginx/sites-enabled/default; do
  if [ -f "$site" ] && grep -q 'cura-slicer' "$site"; then
    sudo sed -i '/cura-slicer/d' "$site"
    ok "Removed cura-slicer reference from $site"
    sudo nginx -t && sudo systemctl reload nginx 2>/dev/null || warn "nginx reload failed."
  fi
done

# ── Remove web UI ─────────────────────────────────────────────────────────────
heading "Removing web UI"

UI_DIR="/var/www/cura-slicer"
if [ -d "$UI_DIR" ]; then
  sudo rm -rf "$UI_DIR"
  ok "Removed $UI_DIR"
else
  info "Web UI directory not found – skipping."
fi

# ── Restart Moonraker ─────────────────────────────────────────────────────────
heading "Restarting Moonraker"
for svc in moonraker moonraker.service; do
  if systemctl list-units --quiet --no-pager "$svc" 2>/dev/null | grep -q "$svc"; then
    sudo systemctl restart "$svc" && ok "Moonraker restarted." && break
  fi
done

heading "Uninstall complete"
echo ""
ok "Fluidd-Cura Slicer has been removed."
echo ""
info "Your data is preserved at:"
[ -d "${MOONRAKER_DATA}/cura_profiles" ]     && echo "  Profiles:    ${MOONRAKER_DATA}/cura_profiles/"
[ -d "${MOONRAKER_DATA}/cura_definitions" ]  && echo "  Definitions: ${MOONRAKER_DATA}/cura_definitions/"
[ -d "${MOONRAKER_DATA}/gcodes/sliced" ]     && echo "  Sliced:      ${MOONRAKER_DATA}/gcodes/sliced/"
echo ""
