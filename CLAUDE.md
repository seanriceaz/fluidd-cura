# CLAUDE.md

Context for AI-assisted development of fluidd-cura.

## Project Summary

**fluidd-cura** is a browser-based STL slicing widget for [Fluidd](https://github.com/fluidd-core/fluidd) (3D printer web UI). It lets users slice STL/OBJ/3MF files directly in the browser using CuraEngine running locally on the Raspberry Pi — no cloud, no desktop app.

The slicer loads as an iframe panel inside the Fluidd dashboard.

## Architecture

```
Browser (Fluidd iframe)
    ↓ HTTP REST
nginx (/cura-slicer/) → serves ui/index.html
    ↓ API proxy
Moonraker (port 7125) → moonraker-plugin/cura_slicer.py
    ↓ subprocess
CuraEngine CLI
    ↓ output
~/printer_data/gcodes/sliced/
```

**Runtime directories on the Pi** (not in this repo):
- `~/printer_data/cura_profiles/` – saved JSON profiles
- `~/printer_data/cura_definitions/` – custom printer definitions
- `~/printer_data/cura_temp/` – temporary slice files

## Key Files

| File | Role |
|------|------|
| `moonraker-plugin/cura_slicer.py` | Python Moonraker plugin (~600 lines). All REST endpoints, CuraEngine subprocess runner, job tracking. |
| `ui/index.html` | Single-file Vue 3 SPA (~1000 lines). Tabs: Slicer, Profiles, Jobs, Settings. Dark theme matching Fluidd. |
| `install.sh` | Bash installer. Detects Moonraker, installs CuraEngine (apt or `--source-build`), deploys files, configures nginx. |
| `uninstall.sh` | Reverses install steps. |
| `config/moonraker_cura_slicer.conf` | Config snippet for `moonraker.conf`. |
| `nginx/cura-slicer.nginx.conf` | Nginx location block for `/cura-slicer/` with iframe CSP headers. |
| `profiles/examples/` | Sample profiles: Ender3 PLA/PETG, generic PLA. |

## API Endpoints

All under `/server/cura_slicer/` on Moonraker:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/status` | CuraEngine version, plugin info |
| GET/POST | `/profiles` | List / create profiles |
| GET/PUT/DELETE | `/profiles/{name}` | Get / update / delete profile |
| GET | `/definitions` | List printer definitions |
| POST | `/definitions` | Upload `.def.json` |
| POST | `/slice` | Start a slice job → returns `job_id` |
| GET | `/jobs` | List recent jobs |
| GET | `/jobs/{id}` | Job status and progress |
| GET/POST | `/settings` | Get / update CuraEngine path |

## Tech Stack

- **Backend**: Python 3, Moonraker plugin API
- **Frontend**: Vue 3 (CDN, no build step), vanilla HTML/CSS
- **Slicer**: CuraEngine CLI (apt package or source build)
- **Web server**: Nginx
- **Platform**: Raspberry Pi, Debian Bullseye/Bookworm
- **License**: GPL-3.0

## Development Notes

- The UI is a **single HTML file** (`ui/index.html`) — no build step, no node_modules.
- The Moonraker plugin is **symlinked** into Moonraker's component directory (not copied), so edits to the repo file take effect after restarting Moonraker.
- The install script places a symlink: `~/moonraker/moonraker/components/cura_slicer.py` → repo file.
- CuraEngine is invoked via subprocess with JSON profile parameters passed as `-s key=value` flags.
- Jobs are tracked in-memory in the plugin (not persisted across restarts).

## Common Tasks

**Test the plugin locally (on a Pi with Moonraker):**
```bash
sudo systemctl restart moonraker
journalctl -u moonraker -f   # watch logs
```

**Install / reinstall:**
```bash
bash install.sh               # standard
bash install.sh --source-build  # build CuraEngine from source
bash install.sh --no-nginx    # skip nginx config
```

**Lint/format** — no automated linting configured yet.

## Branch Convention

Feature branches follow: `claude/<description>-<short-id>`

Active branches:
- `claude/fluidd-stl-cura-slicer-UAGgB` — main feature work
- `claude/add-context-documentation-GbTuZ` — this branch (context docs)
