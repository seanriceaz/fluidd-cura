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
warn "This will remove:"
warn "  • Moonraker plugin (cura_slicer.py)"
warn "  • [cura_slicer] section from moonraker.conf"
warn "  • Cura Slicer panel from Fluidd's dashboard"
warn "  • nginx /cura-slicer/ location config"
warn "  • Web UI files (/var/www/cura-slicer/)"
echo ""
warn "The following will NOT be deleted (your data):"
warn "  • Sliced gcode files  (printer_data/gcodes/sliced/)"
warn "  • Cura profiles       (printer_data/cura_profiles/)"
warn "  • Printer definitions (printer_data/cura_definitions/)"
echo ""
read -rp "Continue? (y/N) " CONFIRM
[[ "$CONFIRM" =~ ^[Yy]$ ]] || die "Aborted."

# =============================================================================
# 1. Remove Moonraker plugin
# =============================================================================
heading "Removing Moonraker plugin"

MOONRAKER_DIR=""
for d in "$INSTALL_HOME/moonraker" "/home/pi/moonraker" "/home/klipper/moonraker" "/opt/moonraker"; do
  if [ -f "$d/moonraker/server.py" ]; then
    MOONRAKER_DIR="$d"
    break
  fi
done

if [ -n "$MOONRAKER_DIR" ]; then
  PLUGIN="$MOONRAKER_DIR/moonraker/components/cura_slicer.py"
  if [ -f "$PLUGIN" ] || [ -L "$PLUGIN" ]; then
    sudo rm -f "$PLUGIN"
    ok "Removed $PLUGIN"
  else
    info "Plugin not found at $PLUGIN – skipping."
  fi
else
  warn "Moonraker directory not found – skipping plugin removal."
  warn "Delete moonraker/moonraker/components/cura_slicer.py manually if needed."
fi

# =============================================================================
# 2. Clean moonraker.conf
# =============================================================================
heading "Cleaning moonraker.conf"

MOONRAKER_DATA=""
for d in "$INSTALL_HOME/printer_data" "/home/pi/printer_data" "/home/klipper/printer_data"; do
  if [ -d "$d" ]; then
    MOONRAKER_DATA="$d"
    break
  fi
done

if [ -z "$MOONRAKER_DATA" ]; then
  warn "Could not find printer_data directory – skipping moonraker.conf cleanup."
else
  MOONRAKER_CONF="$MOONRAKER_DATA/config/moonraker.conf"
  if [ -f "$MOONRAKER_CONF" ]; then
    python3 - "$MOONRAKER_CONF" <<'PYEOF'
import sys, re
path = sys.argv[1]
with open(path) as f:
    content = f.read()
# Remove the installer comment block + [cura_slicer] section
cleaned = re.sub(
    r'\n# ── Cura Slicer \(added by fluidd-cura.*?\[cura_slicer\][^\[]*',
    '',
    content,
    flags=re.DOTALL,
)
# Also remove a bare [cura_slicer] section if the comment wasn't there
cleaned = re.sub(
    r'\n\[cura_slicer\][^\[]*',
    '',
    cleaned,
    flags=re.DOTALL,
)
if cleaned != content:
    with open(path, 'w') as f:
        f.write(cleaned)
    print(f"Cleaned: {path}")
else:
    print(f"No [cura_slicer] section found in {path} – skipping.")
PYEOF
    ok "moonraker.conf processed."
  else
    warn "moonraker.conf not found at $MOONRAKER_CONF – skipping."
  fi
fi

# =============================================================================
# 3. Remove Cura Slicer panel from Fluidd dashboard
# =============================================================================
heading "Removing Cura Slicer from Fluidd dashboard"

python3 <<'PYEOF'
import json, sys
import urllib.request as ureq
import urllib.error   as uerr

MOONRAKER = "http://localhost:7125"

try:
    resp    = ureq.urlopen(f"{MOONRAKER}/server/database/item?namespace=fluidd&key=cameras", timeout=5)
    value   = json.loads(resp.read()).get("result", {}).get("value", [])
    cameras = value if isinstance(value, list) else []
except uerr.HTTPError as e:
    if e.code == 404:
        print("No Fluidd dashboard panels found — skipping.")
        sys.exit(0)
    print(f"WARN: DB read failed ({e}) — skipping.")
    sys.exit(0)
except Exception as e:
    print(f"WARN: Could not reach Moonraker ({e}) — skipping.")
    sys.exit(0)

before  = len(cameras)
cameras = [c for c in cameras if not (
    "cura-slicer" in c.get("url", "").lower() or
    c.get("name", "").lower() == "cura slicer"
)]
removed = before - len(cameras)

if removed == 0:
    print("Cura Slicer panel not found in Fluidd dashboard — skipping.")
    sys.exit(0)

body = json.dumps({"namespace": "fluidd", "key": "cameras", "value": cameras}).encode()
post = ureq.Request(f"{MOONRAKER}/server/database/item", data=body,
                    headers={"Content-Type": "application/json"}, method="POST")
try:
    ureq.urlopen(post, timeout=5)
    print(f"OK: Removed Cura Slicer from Fluidd dashboard.")
except Exception as e:
    print(f"WARN: DB write failed ({e}).")
    print("      Remove it manually: Fluidd → Settings → Cameras.")
PYEOF

# =============================================================================
# 4. Remove nginx config
# =============================================================================
heading "Removing nginx config"

NGINX_SNIPPET="/etc/nginx/snippets/cura-slicer.conf"
if [ -f "$NGINX_SNIPPET" ]; then
  sudo rm "$NGINX_SNIPPET"
  ok "Removed $NGINX_SNIPPET"
else
  info "nginx snippet not found – skipping."
fi

NGINX_RELOADED=false
for site in /etc/nginx/sites-enabled/fluidd /etc/nginx/sites-enabled/mainsail \
            /etc/nginx/sites-enabled/default; do
  if [ -f "$site" ] && grep -q 'cura-slicer' "$site"; then
    sudo sed -i '/cura-slicer/d' "$site"
    ok "Removed cura-slicer reference from $site"
    NGINX_RELOADED=true
  fi
done

if [ "$NGINX_RELOADED" = true ]; then
  sudo nginx -t && sudo systemctl reload nginx && ok "nginx reloaded." \
    || warn "nginx reload failed – check config manually."
fi

# =============================================================================
# 5. Remove web UI
# =============================================================================
heading "Removing web UI"

UI_DIR="/var/www/cura-slicer"
if [ -d "$UI_DIR" ]; then
  sudo rm -rf "$UI_DIR"
  ok "Removed $UI_DIR"
else
  info "Web UI directory not found – skipping."
fi

# =============================================================================
# 6. Restart Moonraker
# =============================================================================
heading "Restarting Moonraker"

MOONRAKER_SERVICE=""
for svc in moonraker moonraker.service; do
  if systemctl list-units --quiet --no-pager "$svc" 2>/dev/null | grep -q "$svc"; then
    MOONRAKER_SERVICE="$svc"
    break
  fi
done

if [ -n "$MOONRAKER_SERVICE" ]; then
  sudo systemctl restart "$MOONRAKER_SERVICE"
  ok "Moonraker restarted."
else
  warn "Could not find moonraker systemd service – restart it manually."
fi

# =============================================================================
# Done
# =============================================================================
heading "Uninstall complete"

echo ""
ok "Fluidd-Cura Slicer has been removed."
echo ""

if [ -n "$MOONRAKER_DATA" ]; then
  SHOW_DATA=false
  [ -d "$MOONRAKER_DATA/cura_profiles" ]    && SHOW_DATA=true
  [ -d "$MOONRAKER_DATA/cura_definitions" ] && SHOW_DATA=true
  [ -d "$MOONRAKER_DATA/gcodes/sliced" ]    && SHOW_DATA=true

  if [ "$SHOW_DATA" = true ]; then
    info "Your data is preserved at:"
    [ -d "$MOONRAKER_DATA/cura_profiles" ]    && echo "  Profiles:    $MOONRAKER_DATA/cura_profiles/"
    [ -d "$MOONRAKER_DATA/cura_definitions" ] && echo "  Definitions: $MOONRAKER_DATA/cura_definitions/"
    [ -d "$MOONRAKER_DATA/gcodes/sliced" ]    && echo "  Sliced:      $MOONRAKER_DATA/gcodes/sliced/"
    echo ""
    info "To delete this data too, run:"
    echo "  rm -rf $MOONRAKER_DATA/cura_profiles $MOONRAKER_DATA/cura_definitions"
    echo ""
  fi
fi
