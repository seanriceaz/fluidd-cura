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

if [ -f "$PLUGIN_DEST" ]; then
  info "Existing plugin found – replacing."
fi
sudo cp "$PLUGIN_SRC" "$PLUGIN_DEST"
sudo chown root:root "$PLUGIN_DEST" 2>/dev/null || true
ok "Plugin installed: $PLUGIN_DEST"

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
# Done
# =============================================================================
heading "Installation complete"

PRINTER_IP="$(hostname -I | awk '{print $1}')"

echo ""
echo -e "${GREEN}${BOLD}Cura Slicer installed successfully!${RESET}"
echo ""
echo "  Web UI:  http://${PRINTER_IP}/cura-slicer/"
echo "  API:     http://${PRINTER_IP}/server/cura_slicer/status"
echo ""
echo -e "${BOLD}Next steps:${RESET}"
echo "  • Open http://${PRINTER_IP}/cura-slicer/ in your browser"
echo "  • Import your first profile: Profiles tab → New Profile"
echo "    (or copy a starter profile from profiles/examples/ to"
echo "     ~/printer_data/cura_profiles/)"
echo ""
