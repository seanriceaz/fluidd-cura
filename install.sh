#!/usr/bin/env bash
# =============================================================================
#  install.sh — Fluidd-Cura Slicer Integration Installer
# =============================================================================
#
#  What this script does:
#    1. Checks for / installs CuraEngine (via apt or from source)
#    2. Locates the Moonraker installation and installs the plugin
#    3. Adds [cura_slicer] to moonraker.conf (if not already present)
#    4. Deploys the web UI and downloads Vue 3
#    5. Configures nginx to serve the UI at /cura-slicer/
#    6. Restarts moonraker
#
#  Usage:
#    chmod +x install.sh
#    ./install.sh [--no-nginx] [--source-build]
#
#  Options:
#    --no-nginx      Skip nginx configuration (serve UI manually)
#    --source-build  Build CuraEngine from source instead of using apt
# =============================================================================

set -euo pipefail

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()      { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
die()     { error "$*"; exit 1; }
heading() { echo -e "\n${BOLD}${CYAN}── $* ──${RESET}"; }

# ── Parse args ────────────────────────────────────────────────────────────────
DO_NGINX=true
SOURCE_BUILD=false
for arg in "$@"; do
  case "$arg" in
    --no-nginx)     DO_NGINX=false ;;
    --source-build) SOURCE_BUILD=true ;;
    *) warn "Unknown argument: $arg" ;;
  esac
done

# ── Detect user / home ────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_USER="${SUDO_USER:-${USER:-pi}}"
INSTALL_HOME="$(eval echo ~"$INSTALL_USER")"

# ── Minimum CuraEngine version we consider acceptable ─────────────────────────
MIN_CURA_MAJOR=4

# =============================================================================
# 1. CuraEngine
# =============================================================================
heading "CuraEngine"

CURA_BIN=""
ENGINE_OK=false

check_engine() {
  local bin="$1"
  if ! command -v "$bin" &>/dev/null && [ ! -x "$bin" ]; then return 1; fi
  local ver
  ver="$("$bin" --version 2>&1 | grep -oP '\d+\.\d+' | head -1)"
  local major="${ver%%.*}"
  if [ -z "$major" ] || [ "$major" -lt "$MIN_CURA_MAJOR" ] 2>/dev/null; then
    warn "Found $bin but version '$ver' is below $MIN_CURA_MAJOR.x"
    return 1
  fi
  ok "Found CuraEngine $ver at $bin"
  CURA_BIN="$bin"
  ENGINE_OK=true
  return 0
}

# Check common locations
for candidate in CuraEngine /usr/bin/CuraEngine /usr/local/bin/CuraEngine; do
  check_engine "$candidate" && break || true
done

if [ "$ENGINE_OK" = false ]; then
  if [ "$SOURCE_BUILD" = true ]; then
    install_from_source
  else
    info "Attempting to install cura-engine via apt…"
    sudo apt-get update -qq
    if sudo apt-get install -y cura-engine; then
      check_engine CuraEngine || true
    fi
  fi
fi

if [ "$ENGINE_OK" = false ]; then
  warn "CuraEngine not found or version too old."
  echo ""
  echo "  Options:"
  echo "  a) Install manually: sudo apt-get install cura-engine"
  echo "  b) Build from source: ./install.sh --source-build"
  echo "  c) Set cura_engine_path in moonraker.conf after installing"
  echo ""
  read -rp "Continue installation anyway? (y/N) " CONTINUE
  [[ "$CONTINUE" =~ ^[Yy]$ ]] || die "Aborted."
  CURA_BIN="CuraEngine"
fi

# ── Optional: build from source ───────────────────────────────────────────────
install_from_source() {
  heading "Building CuraEngine from source"
  local build_deps=(git cmake build-essential libboost-dev libprotobuf-dev protobuf-compiler)
  info "Installing build dependencies: ${build_deps[*]}"
  sudo apt-get update -qq
  sudo apt-get install -y "${build_deps[@]}"

  local tmp_dir
  tmp_dir="$(mktemp -d)"
  trap "rm -rf '$tmp_dir'" EXIT

  info "Cloning CuraEngine repository…"
  git clone --depth=1 https://github.com/Ultimaker/CuraEngine.git "$tmp_dir/CuraEngine"
  cd "$tmp_dir/CuraEngine"

  info "Building (this may take 20-40 minutes on Raspberry Pi)…"
  mkdir build && cd build
  cmake .. -DCMAKE_BUILD_TYPE=Release
  make -j"$(nproc)"
  sudo make install
  cd "$SCRIPT_DIR"

  check_engine CuraEngine || warn "Build may have failed – check output above."
}

# =============================================================================
# 2. Locate Moonraker
# =============================================================================
heading "Locating Moonraker"

MOONRAKER_DIR=""
MOONRAKER_CONF=""
MOONRAKER_DATA=""

# Common installation paths
for candidate_dir in \
    "$INSTALL_HOME/moonraker" \
    "/home/pi/moonraker" \
    "/home/klipper/moonraker" \
    "/opt/moonraker"; do
  if [ -f "$candidate_dir/moonraker/server.py" ]; then
    MOONRAKER_DIR="$candidate_dir"
    break
  fi
done

# Detect moonraker data path (printer_data or klipper_config)
for data_candidate in \
    "$INSTALL_HOME/printer_data" \
    "/home/pi/printer_data" \
    "/home/klipper/printer_data"; do
  if [ -d "$data_candidate" ]; then
    MOONRAKER_DATA="$data_candidate"
    break
  fi
done

# Find moonraker.conf
for conf_candidate in \
    "${MOONRAKER_DATA:-}/config/moonraker.conf" \
    "$INSTALL_HOME/klipper_config/moonraker.conf" \
    "/home/pi/klipper_config/moonraker.conf"; do
  if [ -f "$conf_candidate" ]; then
    MOONRAKER_CONF="$conf_candidate"
    break
  fi
done

if [ -z "$MOONRAKER_DIR" ]; then
  warn "Could not auto-detect Moonraker installation directory."
  read -rp "Enter Moonraker installation path (e.g. /home/pi/moonraker): " MOONRAKER_DIR
  [ -f "$MOONRAKER_DIR/moonraker/server.py" ] || die "Not a valid Moonraker directory: $MOONRAKER_DIR"
fi

if [ -z "$MOONRAKER_CONF" ]; then
  warn "Could not auto-detect moonraker.conf."
  read -rp "Enter full path to moonraker.conf: " MOONRAKER_CONF
  [ -f "$MOONRAKER_CONF" ] || die "File not found: $MOONRAKER_CONF"
fi

ok "Moonraker directory: $MOONRAKER_DIR"
ok "moonraker.conf: $MOONRAKER_CONF"

COMPONENTS_DIR="$MOONRAKER_DIR/moonraker/components"
[ -d "$COMPONENTS_DIR" ] || die "Components directory not found: $COMPONENTS_DIR"

# =============================================================================
# 3. Install Moonraker plugin
# =============================================================================
heading "Installing Moonraker plugin"

PLUGIN_SRC="$SCRIPT_DIR/moonraker-plugin/cura_slicer.py"
PLUGIN_DEST="$COMPONENTS_DIR/cura_slicer.py"

[ -f "$PLUGIN_SRC" ] || die "Plugin source not found: $PLUGIN_SRC"

# Remove any previously copied file so we can replace it with a symlink
if [ -e "$PLUGIN_DEST" ] || [ -L "$PLUGIN_DEST" ]; then
  info "Existing plugin found – replacing with symlink."
  sudo rm -f "$PLUGIN_DEST"
fi
sudo ln -s "$PLUGIN_SRC" "$PLUGIN_DEST"
ok "Plugin symlinked: $PLUGIN_DEST -> $PLUGIN_SRC"

# =============================================================================
# 4. Configure moonraker.conf
# =============================================================================
heading "Updating moonraker.conf"

if grep -q '^\[cura_slicer\]' "$MOONRAKER_CONF"; then
  ok "[cura_slicer] section already present in moonraker.conf – skipping."
else
  info "Adding [cura_slicer] section to moonraker.conf…"
  cat >> "$MOONRAKER_CONF" <<EOF

# ── Cura Slicer (added by fluidd-cura install.sh) ──
[cura_slicer]
# Path to the CuraEngine binary (default: CuraEngine from PATH)
cura_engine_path: ${CURA_BIN:-CuraEngine}
EOF
  ok "Section added to $MOONRAKER_CONF"
fi

# =============================================================================
# 5. Deploy web UI
# =============================================================================
heading "Deploying web UI"

UI_DEST="/var/www/cura-slicer"
sudo mkdir -p "$UI_DEST"
sudo cp "$SCRIPT_DIR/ui/index.html" "$UI_DEST/index.html"

# Download Vue 3 for offline use
VUE_URL="https://cdn.jsdelivr.net/npm/vue@3/dist/vue.global.prod.js"
VUE_DEST="$UI_DEST/vue.min.js"

if [ -f "$VUE_DEST" ]; then
  ok "Vue 3 already downloaded."
else
  info "Downloading Vue 3 from CDN…"
  if sudo wget -q -O "$VUE_DEST" "$VUE_URL"; then
    ok "Vue 3 downloaded."
  else
    warn "Download failed – UI will fall back to CDN on first load (requires internet)."
    # Create a fallback stub that loads from CDN
    sudo tee "$VUE_DEST" > /dev/null <<'STUB'
// Fallback: load Vue from CDN
document.write('<script src="https://cdn.jsdelivr.net/npm/vue@3/dist/vue.global.prod.js"><\/script>');
STUB
  fi
fi

# Download Three.js r128 (last version with non-ESM global builds) for 3D preview
THREE_BASE="https://cdn.jsdelivr.net/npm/three@0.128.0"
for item in \
    "three.min.js|${THREE_BASE}/build/three.min.js" \
    "three.STLLoader.js|${THREE_BASE}/examples/js/loaders/STLLoader.js" \
    "three.OrbitControls.js|${THREE_BASE}/examples/js/controls/OrbitControls.js"; do
  fname="${item%%|*}"; url="${item##*|}"
  dest="$UI_DEST/$fname"
  if [ -f "$dest" ]; then
    ok "$fname already downloaded."
  else
    info "Downloading $fname …"
    if sudo wget -q -O "$dest" "$url"; then
      ok "$fname downloaded."
    else
      warn "Download failed for $fname – 3D preview will not work without this file."
    fi
  fi
done

sudo chown -R www-data:www-data "$UI_DEST" 2>/dev/null || true
ok "UI deployed to $UI_DEST"

# =============================================================================
# 6. Nginx configuration
# =============================================================================
if [ "$DO_NGINX" = true ]; then
  heading "Configuring nginx"

  NGINX_CONF_SRC="$SCRIPT_DIR/nginx/cura-slicer.nginx.conf"
  NGINX_SNIPPET="/etc/nginx/snippets/cura-slicer.conf"

  if [ ! -f "$NGINX_CONF_SRC" ]; then
    warn "nginx config template not found at $NGINX_CONF_SRC – generating inline."
    cat > /tmp/cura-slicer.nginx.conf <<'NGINX'
location /cura-slicer/ {
    alias /var/www/cura-slicer/;
    index index.html;
    try_files $uri $uri/ /cura-slicer/index.html;
}
NGINX
    NGINX_CONF_SRC="/tmp/cura-slicer.nginx.conf"
  fi

  sudo cp "$NGINX_CONF_SRC" "$NGINX_SNIPPET"

  # Inject into the active Fluidd site config if not already there
  FLUIDD_NGINX=""
  for site in /etc/nginx/sites-enabled/fluidd /etc/nginx/sites-enabled/mainsail \
              /etc/nginx/sites-enabled/default; do
    [ -f "$site" ] && FLUIDD_NGINX="$site" && break
  done

  if [ -z "$FLUIDD_NGINX" ]; then
    warn "Could not find active nginx site config."
    info "Add the following to your nginx server block manually:"
    cat "$NGINX_SNIPPET"
  else
    if grep -q 'cura-slicer' "$FLUIDD_NGINX"; then
      ok "cura-slicer already referenced in $FLUIDD_NGINX"
    else
      info "Injecting cura-slicer snippet into $FLUIDD_NGINX…"
      # Insert include before the closing brace of the server block
      sudo sed -i '/^}/i \    include snippets\/cura-slicer.conf;' "$FLUIDD_NGINX"
      ok "Snippet included in $FLUIDD_NGINX"
    fi
    sudo nginx -t && sudo systemctl reload nginx || warn "nginx reload failed – check config."
  fi
fi

# =============================================================================
# 7. Restart Moonraker
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
  warn "Could not find moonraker systemd service. Restart it manually."
fi

# =============================================================================
# 8. Embed Cura Slicer as a dashboard panel in Fluidd
#
# Fluidd's dashboard supports embedding any URL as an iframe panel — it
# calls these "cameras" internally, but they work for any web page.
# The list is stored in Moonraker's database (namespace=fluidd, key=cameras).
# We wait up to 60 s for Moonraker to be ready after the restart above.
# =============================================================================
heading "Embedding Cura Slicer in Fluidd dashboard"

PRINTER_IP="$(hostname -I | awk '{print $1}')"
SLICER_URL="http://${PRINTER_IP}/cura-slicer/"

python3 - "$SLICER_URL" <<'PYEOF'
import json, sys, time, uuid
import urllib.request as ureq
import urllib.error   as uerr

MOONRAKER  = "http://localhost:7125"
slicer_url = sys.argv[1]

# Wait up to 60 s for Moonraker to accept requests
for _ in range(20):
    try:
        ureq.urlopen(f"{MOONRAKER}/server/info", timeout=3)
        break
    except Exception:
        time.sleep(3)
else:
    print("WARN: Moonraker not ready after 60 s — skipping dashboard embed.")
    print(f"      You can add it manually: Fluidd → Settings → Cameras → Add")
    print(f"      Name: Cura Slicer  |  Type: HTTP page  |  URL: {slicer_url}")
    sys.exit(0)

# Read the existing panel list
cameras = []
try:
    resp   = ureq.urlopen(f"{MOONRAKER}/server/database/item?namespace=fluidd&key=cameras", timeout=5)
    value  = json.loads(resp.read()).get("result", {}).get("value", [])
    cameras = value if isinstance(value, list) else []
except uerr.HTTPError as e:
    if e.code != 404:
        print(f"WARN: DB read failed ({e}) — skipping dashboard embed.")
        sys.exit(0)
except Exception as e:
    print(f"WARN: {e} — skipping dashboard embed.")
    sys.exit(0)

# Skip if already present
if any(c.get("url", "").rstrip("/") == slicer_url.rstrip("/") for c in cameras):
    print("OK: Cura Slicer already in Fluidd dashboard.")
    sys.exit(0)

# Add the entry (type "http" = iframe panel in Fluidd)
cameras.append({
    "id":      str(uuid.uuid4()),
    "name":    "Cura Slicer",
    "type":    "http",
    "url":     slicer_url,
    "enabled": True,
    "flipX":   False,
    "flipY":   False,
    "rotate":  0,
    "height":  640,
})

body = json.dumps({"namespace": "fluidd", "key": "cameras", "value": cameras}).encode()
post = ureq.Request(f"{MOONRAKER}/server/database/item", data=body,
                    headers={"Content-Type": "application/json"}, method="POST")
try:
    ureq.urlopen(post, timeout=5)
    print("OK: Cura Slicer panel added to Fluidd dashboard.")
except Exception as e:
    print(f"WARN: DB write failed ({e}) — skipping dashboard embed.")
    print(f"      Add manually: Fluidd → Settings → Cameras → Add")
    print(f"      Name: Cura Slicer  |  Type: HTTP page  |  URL: {slicer_url}")
PYEOF

# =============================================================================
# Done
# =============================================================================
heading "Installation complete"

echo ""
echo -e "${GREEN}${BOLD}Cura Slicer installed successfully!${RESET}"
echo ""
echo "  Web UI:  http://${PRINTER_IP}/cura-slicer/"
echo "  API:     http://${PRINTER_IP}/server/cura_slicer/status"
echo ""
echo -e "${BOLD}Next steps:${RESET}"
echo "  • Refresh Fluidd — the Cura Slicer panel will appear on the dashboard."
echo "    (If it isn't visible, drag the Camera widget onto the layout from"
echo "     Settings → Interface → Dashboard.)"
echo "  • Import your first profile: Profiles tab → New Profile"
echo "    (or copy a starter from profiles/examples/ to ~/printer_data/cura_profiles/)"
echo ""
