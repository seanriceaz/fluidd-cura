#!/usr/bin/env bash
# =============================================================================
#  deploy_ui.sh — (Re)deploy the Cura Slicer web UI to /var/www/cura-slicer
#
#  Copies ui/index.html into place. Runs without sudo: install.sh makes the
#  install user the owner of /var/www/cura-slicer specifically so this can
#  run unattended as Moonraker's update_manager install_script after a git
#  pull, without needing passwordless sudo.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UI_DEST="/var/www/cura-slicer"

mkdir -p "$UI_DEST"
cp "$SCRIPT_DIR/ui/index.html" "$UI_DEST/index.html"
chmod 644 "$UI_DEST/index.html"

echo "Cura Slicer UI deployed to $UI_DEST/index.html"
