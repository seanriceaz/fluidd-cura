# Fluidd-Cura: STL Slicing Widget for Fluidd

Upload an STL file from the Fluidd web interface, slice it with CuraEngine directly on your Raspberry Pi, and start printing — all without leaving the browser.

---

## Features

- **Drag-and-drop STL upload** (also `.obj` and `.3mf`)
- **CuraEngine slicing** runs locally on the Pi — no cloud, no desktop Cura needed
- **Profile management**: create profiles with per-setting key-value pairs, or import `.curaprofile` files exported from the Cura desktop app
- **Printer definition management**: upload custom `.def.json` definitions, or use the ones installed by the `cura-engine` system package
- **One-click print** after slicing
- **Dark theme** matching Fluidd's aesthetic
- **Embeddable** in Fluidd's dashboard as an iframe panel

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Browser (Fluidd dashboard)                          │
│  ┌────────────────────────────────────────────────┐ │
│  │  iframe: http://<pi>/cura-slicer/             │ │
│  │  Vue 3 single-page app (served by nginx)      │ │
│  └───────────────────┬────────────────────────────┘ │
└──────────────────────│──────────────────────────────┘
                       │ REST API
┌──────────────────────▼──────────────────────────────┐
│  Moonraker (port 7125)                               │
│  Plugin: moonraker/components/cura_slicer.py        │
│  Endpoints: /server/cura_slicer/*                   │
└──────────────────────┬──────────────────────────────┘
                       │ subprocess
              ┌────────▼────────┐
              │   CuraEngine    │
              │  (CLI slicer)   │
              └─────────────────┘
```

**Data paths** (inside `~/printer_data/`):
| Path | Contents |
|---|---|
| `cura_profiles/` | Saved slicer profiles (JSON) |
| `cura_definitions/` | Custom printer definitions (.def.json) |
| `cura_temp/` | Temporary working files |
| `gcodes/sliced/` | Output gcode files |

---

## Requirements

- Raspberry Pi running **Raspberry Pi OS** (Bullseye or Bookworm)
- [Klipper](https://github.com/Klipper3d/klipper) + [Moonraker](https://github.com/Arksine/moonraker) + [Fluidd](https://github.com/fluidd-core/fluidd)
- nginx (standard in Fluidd setups)
- Internet access during installation (to download CuraEngine and Vue 3)

---

## Installation

```bash
git clone https://github.com/seanriceaz/fluidd-cura.git
cd fluidd-cura
chmod +x install.sh
./install.sh
```

### Options

| Flag | Description |
|---|---|
| `--no-nginx` | Skip nginx configuration (access via direct port instead) |
| `--source-build` | Build CuraEngine from source instead of using `apt` |

### What the installer does

1. **Checks CuraEngine** – installs via `apt-get install cura-engine` if missing or outdated
2. **Installs the Moonraker plugin** into `~/moonraker/moonraker/components/`
3. **Adds `[cura_slicer]`** to `moonraker.conf`
4. **Deploys the web UI** to `/var/www/cura-slicer/` and downloads Vue 3 for offline use
5. **Configures nginx** with a `/cura-slicer/` location block
6. **Restarts Moonraker**

---

## Adding to Fluidd Dashboard

After installing, the slicer UI is at `http://<printer-ip>/cura-slicer/`.

To embed it as a panel in Fluidd:

1. Open Fluidd → **Settings** → **Interface** → **Dashboard**
2. Find **"Additional Panels"** (or use the camera entry as an iframe workaround)
3. Add a new entry with URL `http://<printer-ip>/cura-slicer/`
4. Set type to **"HTTP page"**
5. Save and reload

> **Tip**: You can also just open `http://<printer-ip>/cura-slicer/` in a browser tab alongside Fluidd.

---

## API Reference

All endpoints are on Moonraker at `http://<printer-ip>/server/cura_slicer/`.

### `GET /server/cura_slicer/status`
Returns CuraEngine version and plugin status.

### `GET /server/cura_slicer/profiles`
Returns list of all profiles.

### `POST /server/cura_slicer/profiles`
Create a profile. Body:
```json
{
  "name": "my_profile",
  "display_name": "My Profile",
  "description": "...",
  "printer_definition": "creality_ender3",
  "settings": {
    "layer_height": "0.2",
    "infill_sparse_density": "20"
  },
  "curaprofile_b64": "<optional base64-encoded .curaprofile zip>"
}
```

### `GET /server/cura_slicer/profiles/{name}`
Get full profile including all settings.

### `PUT /server/cura_slicer/profiles/{name}`
Update a profile.

### `DELETE /server/cura_slicer/profiles/{name}`
Delete a profile.

### `GET /server/cura_slicer/definitions`
List available printer definitions (local + system-installed).

### `POST /server/cura_slicer/definitions`
Upload a printer definition. Body:
```json
{
  "name": "my_printer",
  "content_b64": "<base64-encoded .def.json>"
}
```

### `POST /server/cura_slicer/slice`
Start a slicing job. The STL file must already be uploaded to the gcodes root.
```json
{
  "filename": "my_model.stl",
  "profile": "ender3_pla_standard",
  "settings": { "layer_height": "0.15" },
  "print_after": false
}
```
Returns `{ "job_id": "abc12345", "status": "pending" }`.

### `GET /server/cura_slicer/jobs`
List recent jobs.

### `GET /server/cura_slicer/jobs/{id}`
Get job status and progress.

---

## Profile Management

### Creating a profile manually

Go to **Profiles** tab → **New Profile**. Enter a name, choose your printer definition, and add settings as key-value pairs.

Common settings:

| Key | Description | Example |
|---|---|---|
| `layer_height` | Layer height in mm | `0.2` |
| `infill_sparse_density` | Infill % | `20` |
| `material_print_temperature` | Nozzle temp (°C) | `210` |
| `material_bed_temperature` | Bed temp (°C) | `60` |
| `speed_print` | Print speed (mm/s) | `50` |
| `support_enable` | Enable supports | `true` |
| `adhesion_type` | `skirt`, `brim`, or `raft` | `skirt` |

Full list: [Ultimaker/CuraEngine settings](https://github.com/Ultimaker/CuraEngine/blob/master/resources/definitions/fdmprinter.def.json)

### Importing from Cura desktop

1. In Cura desktop: **Profile** → **Export Profile** → save `.curaprofile`
2. In Cura Slicer UI: **Profiles** → **New Profile** → **Choose .curaprofile**
3. The settings are extracted and stored in the plugin's format

### Printer definitions

The `cura-engine` apt package installs definitions for common printers at `/usr/share/cura/resources/definitions/`. These are available automatically.

To add a custom definition:
- Use the **Profiles** tab → **Upload .def.json**
- Or copy the file to `~/printer_data/cura_definitions/`

---

## Uninstall

```bash
./uninstall.sh
```

This removes the plugin, nginx config, and web UI. Your sliced files and profiles are preserved.

---

## Troubleshooting

**CuraEngine not found**
```bash
which CuraEngine
CuraEngine --version
# If missing:
sudo apt-get install cura-engine
```

**Plugin not loading in Moonraker**
Check Moonraker logs: `journalctl -u moonraker -n 50`
Ensure `[cura_slicer]` is in `moonraker.conf`.

**Slicing fails with no output**
- Verify the printer definition file exists and is valid JSON
- Run CuraEngine manually to see the error:
  ```bash
  CuraEngine slice -v -j /path/to/definition.def.json \
    -s layer_height=0.2 -o /tmp/test.gcode -l /path/to/model.stl
  ```

**UI shows "CuraEngine not found"**
Set the path explicitly in `moonraker.conf`:
```
[cura_slicer]
cura_engine_path: /usr/bin/CuraEngine
```

---

## License

GPL-3.0 — same as Klipper and Moonraker.
