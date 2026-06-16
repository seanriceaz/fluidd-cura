"""
cura_slicer.py - Moonraker component for CuraEngine slicing integration

Install: copy to ~/moonraker/moonraker/components/cura_slicer.py
Config:  add [cura_slicer] section to moonraker.conf

Copyright (C) 2024 fluidd-cura contributors
License: GPL-3.0
"""

from __future__ import annotations

import asyncio
import configparser
import io
import json
import logging
import os
import re
import shutil
import struct
import subprocess
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..websockets import WebRequest

logger = logging.getLogger(__name__)

# Maximum number of completed jobs to keep in memory
MAX_JOB_HISTORY = 20

# CuraEngine progress pattern from stderr with -p flag
PROGRESS_RE = re.compile(r"Progress:(\w+):([0-9.]+)")

IDENTITY_ROTATION = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]

# CuraEngine builds its entire settings registry from whatever is passed via
# -j; the distro cura-engine package ships only the engine binary, not
# Cura's resource definitions, so without a -j file CuraEngine has no
# schema at all and aborts with "Trying to retrieve setting with no value
# given" for the first setting we didn't pass explicitly. This vendored
# copy (see resources/README.md) is always loaded via -j so every setting
# has a real default; printer-specific definitions only need to override
# values that differ from this generic base.
BUNDLED_FDMPRINTER_PATH = Path(__file__).resolve().parent / "resources" / "fdmprinter.def.json"

# Per-extruder settings (material_diameter, extruder_nr, nozzle offsets, ...)
# live in a separate schema that fdmprinter.def.json doesn't define at all.
# CuraEngine only loads it – and only stamps extruder_nr onto the extruder –
# when a -j is given right after switching context with -e<N>, so this must
# always be loaded for extruder 0 even in a single-extruder setup.
BUNDLED_FDMEXTRUDER_PATH = Path(__file__).resolve().parent / "resources" / "fdmextruder.def.json"

# Generic bed/machine fallbacks layered on top of BUNDLED_FDMPRINTER_PATH's
# own (intentionally tiny, 100x100x100mm) defaults so an unresolved printer
# definition still slices for a typical desktop FDM printer. Overridden by
# whatever the resolved definition specifies, then by the profile/request
# settings.
ESSENTIAL_MACHINE_DEFAULTS: Dict[str, str] = {
    "machine_width": "220",
    "machine_depth": "220",
    "machine_height": "250",
    "machine_shape": "rectangular",
    "machine_center_is_zero": "false",
    "machine_heated_bed": "true",
    "machine_nozzle_size": "0.4",
    "machine_extruder_count": "1",
}


def _read_plugin_version() -> str:
    version_file = Path(__file__).resolve().parent.parent / "VERSION"
    try:
        return version_file.read_text().strip()
    except OSError:
        return "unknown"


PLUGIN_VERSION = _read_plugin_version()


def _transform_stl_binary(
    src_path: str,
    dst_path: str,
    rotation: list,
    scale: float,
) -> None:
    """
    Read a binary STL, apply scale + rotation + auto-placement, write to dst_path.

    rotation: 3×3 row-major list-of-lists [[r00,r01,r02], [r10,r11,r12], [r20,r21,r22]]
    scale:    uniform scale factor

    Auto-placement: center X/Y around 0, drop Z to 0.
    Raises ValueError for ASCII STLs or malformed files (caller uses original instead).
    """
    file_size = os.path.getsize(src_path)
    if file_size < 84:
        raise ValueError("File too small to be a valid binary STL")

    with open(src_path, 'rb') as f:
        header = f.read(80)
        tri_count = struct.unpack('<I', f.read(4))[0]

    expected_size = 80 + 4 + tri_count * 50
    if abs(expected_size - file_size) > 4:
        raise ValueError(
            f"Does not appear to be a binary STL "
            f"(expected {expected_size} bytes, got {file_size})"
        )

    R = rotation  # R[row][col]
    s = scale

    def transform_vertex(x, y, z):
        xs, ys, zs = x * s, y * s, z * s
        return (
            R[0][0] * xs + R[0][1] * ys + R[0][2] * zs,
            R[1][0] * xs + R[1][1] * ys + R[1][2] * zs,
            R[2][0] * xs + R[2][1] * ys + R[2][2] * zs,
        )

    def transform_normal(x, y, z):
        # Normals use rotation only (inverse-transpose of R = R for orthogonal matrices)
        return (
            R[0][0] * x + R[0][1] * y + R[0][2] * z,
            R[1][0] * x + R[1][1] * y + R[1][2] * z,
            R[2][0] * x + R[2][1] * y + R[2][2] * z,
        )

    TRI_STRUCT = struct.Struct('<3f 3f 3f 3f H')
    tris = []
    min_x = min_y = min_z = float('inf')
    max_x = max_y = float('-inf')

    with open(src_path, 'rb') as f:
        f.seek(84)  # skip 80-byte header + 4-byte tri count
        for _ in range(tri_count):
            vals = TRI_STRUCT.unpack(f.read(50))
            n  = transform_normal(vals[0], vals[1], vals[2])
            v1 = transform_vertex(vals[3], vals[4], vals[5])
            v2 = transform_vertex(vals[6], vals[7], vals[8])
            v3 = transform_vertex(vals[9], vals[10], vals[11])
            attr = vals[12]
            tris.append((n, v1, v2, v3, attr))
            for vx, vy, vz in (v1, v2, v3):
                if vx < min_x: min_x = vx
                if vx > max_x: max_x = vx
                if vy < min_y: min_y = vy
                if vy > max_y: max_y = vy
                if vz < min_z: min_z = vz

    # Auto-placement: center X/Y around 0, drop Z so min is 0
    tx = -((min_x + max_x) / 2.0)
    ty = -((min_y + max_y) / 2.0)
    tz = -min_z

    # Write output binary STL
    out = bytearray(84 + tri_count * 50)
    out[:80] = header
    struct.pack_into('<I', out, 80, tri_count)

    offset = 84
    for (n, v1, v2, v3, attr) in tris:
        TRI_STRUCT.pack_into(out, offset,
            n[0], n[1], n[2],
            v1[0] + tx, v1[1] + ty, v1[2] + tz,
            v2[0] + tx, v2[1] + ty, v2[2] + tz,
            v3[0] + tx, v3[1] + ty, v3[2] + tz,
            attr)
        offset += 50

    with open(dst_path, 'wb') as f:
        f.write(out)


class CuraSlicer:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.name = config.get_name()

        # Config options
        self.cura_engine = config.get("cura_engine_path", "CuraEngine")
        data_path = Path(self.server.get_app_args()["data_path"])

        self.profiles_dir = data_path / "cura_profiles"
        self.definitions_dir = data_path / "cura_definitions"
        self.temp_dir = data_path / "cura_temp"
        gcodes_root = data_path / "gcodes"
        self.sliced_dir = gcodes_root / "sliced"

        for d in (self.profiles_dir, self.definitions_dir,
                  self.temp_dir, self.sliced_dir):
            d.mkdir(parents=True, exist_ok=True)

        # In-memory job tracking: job_id -> job dict
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._job_order: List[str] = []

        # Persistent settings file (overrides moonraker.conf values at runtime)
        self._settings_path = data_path / "cura_slicer_settings.json"
        self._apply_saved_settings()

        # Register HTTP endpoints
        self.server.register_endpoint(
            "/server/cura_slicer/status",
            ["GET"],
            self._handle_status,
        )
        self.server.register_endpoint(
            "/server/cura_slicer/profiles",
            ["GET", "POST"],
            self._handle_profiles,
        )
        self.server.register_endpoint(
            "/server/cura_slicer/profiles/{profile_name}",
            ["GET", "POST", "DELETE"],
            self._handle_profile,
        )
        self.server.register_endpoint(
            "/server/cura_slicer/definitions",
            ["GET", "POST"],
            self._handle_definitions,
        )
        self.server.register_endpoint(
            "/server/cura_slicer/definitions/{def_name}",
            ["GET", "DELETE"],
            self._handle_definition,
        )
        self.server.register_endpoint(
            "/server/cura_slicer/slice",
            ["POST"],
            self._handle_slice,
        )
        self.server.register_endpoint(
            "/server/cura_slicer/jobs",
            ["GET"],
            self._handle_jobs,
        )
        self.server.register_endpoint(
            "/server/cura_slicer/jobs/{job_id}",
            ["GET", "DELETE"],
            self._handle_job,
        )
        self.server.register_endpoint(
            "/server/cura_slicer/settings",
            ["GET", "POST"],
            self._handle_settings,
        )

        logger.info("CuraSlicer component initialized")

    # -------------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------------

    async def _handle_status(self, web_request: WebRequest) -> Dict:
        version = await self._get_engine_version()
        definitions = self._list_definitions()
        return {
            "plugin_version": PLUGIN_VERSION,
            "cura_engine_path": self.cura_engine,
            "cura_engine_version": version,
            "cura_engine_found": version is not None,
            "profiles_count": len(self._list_profile_names()),
            "definitions_count": len(definitions),
            "definitions": definitions,
        }

    async def _get_engine_version(self) -> Optional[str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.cura_engine, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            output = (stdout or stderr or b"").decode().strip()
            # Extract version number from output like "Cura_SteamEngine version 5.x.x"
            match = re.search(r"(\d+\.\d+[\.\d]*)", output)
            return match.group(1) if match else output or "unknown"
        except (FileNotFoundError, asyncio.TimeoutError, OSError):
            return None

    # -------------------------------------------------------------------------
    # Settings
    # -------------------------------------------------------------------------

    def _apply_saved_settings(self) -> None:
        """Load persisted settings and apply them over the config defaults."""
        if not self._settings_path.exists():
            return
        try:
            with open(self._settings_path) as f:
                saved = json.load(f)
            if "cura_engine_path" in saved:
                self.cura_engine = saved["cura_engine_path"]
        except Exception as exc:
            logger.warning(f"Could not load saved settings: {exc}")

    async def _handle_settings(self, web_request: WebRequest) -> Dict:
        method = web_request.get_action()

        if method == "GET":
            version = await self._get_engine_version()
            return {
                "cura_engine_path": self.cura_engine,
                "cura_engine_version": version,
                "cura_engine_found": version is not None,
                "profiles_dir": str(self.profiles_dir),
                "sliced_dir": str(self.sliced_dir),
            }

        # POST – update settings
        body = web_request.get_args()
        saved: Dict[str, Any] = {}

        if "cura_engine_path" in body:
            path_val = str(body["cura_engine_path"]).strip()
            if path_val:
                self.cura_engine = path_val
                saved["cura_engine_path"] = path_val

        # Persist to disk
        existing: Dict[str, Any] = {}
        if self._settings_path.exists():
            try:
                with open(self._settings_path) as f:
                    existing = json.load(f)
            except Exception:
                pass
        existing.update(saved)
        with open(self._settings_path, "w") as f:
            json.dump(existing, f, indent=2)

        logger.info(f"Settings updated: {saved}")
        version = await self._get_engine_version()
        return {
            "cura_engine_path": self.cura_engine,
            "cura_engine_version": version,
            "cura_engine_found": version is not None,
        }

    # -------------------------------------------------------------------------
    # Profiles
    # -------------------------------------------------------------------------

    def _profile_path(self, name: str) -> Path:
        safe = re.sub(r"[^\w\-. ]", "_", name)
        return self.profiles_dir / f"{safe}.json"

    def _list_profile_names(self) -> List[str]:
        return [p.stem for p in sorted(self.profiles_dir.glob("*.json"))]

    def _load_profile(self, name: str) -> Optional[Dict]:
        path = self.profiles_dir / f"{name}.json"
        if not path.exists():
            # Try fuzzy match (stem only)
            matches = list(self.profiles_dir.glob(f"{name}.json"))
            if not matches:
                return None
            path = matches[0]
        with open(path) as f:
            return json.load(f)

    async def _handle_profiles(self, web_request: WebRequest) -> Any:
        method = web_request.get_action()

        if method == "GET":
            profiles = []
            for name in self._list_profile_names():
                data = self._load_profile(name)
                if data:
                    profiles.append({
                        "name": name,
                        "display_name": data.get("display_name", name),
                        "description": data.get("description", ""),
                        "printer_definition": data.get("printer_definition", ""),
                    })
            return {"profiles": profiles}

        # POST – create or replace a profile
        body = web_request.get_args()
        return await self._save_profile(body)

    async def _handle_profile(self, web_request: WebRequest) -> Any:
        profile_name = web_request.get_str("profile_name")
        method = web_request.get_action()

        if method == "GET":
            data = self._load_profile(profile_name)
            if data is None:
                raise self.server.error(f"Profile '{profile_name}' not found", 404)
            # Augment with build volume from the printer definition
            bv = self._get_build_volume(data.get("printer_definition", ""))
            data["build_volume"] = {
                "width":  bv[0] if bv else 220.0,
                "depth":  bv[1] if bv else 220.0,
                "height": bv[2] if bv else 250.0,
            }
            return data

        if method == "DELETE":
            path = self._profile_path(profile_name)
            if not path.exists():
                raise self.server.error(f"Profile '{profile_name}' not found", 404)
            path.unlink()
            logger.info(f"Deleted profile: {profile_name}")
            return {"deleted": profile_name}

        # POST – update
        body = web_request.get_args()
        body["name"] = profile_name
        return await self._save_profile(body)

    async def _save_profile(self, data: Dict) -> Dict:
        name = data.get("name", "").strip()
        if not name:
            raise self.server.error("Profile 'name' is required", 400)

        # If a .curaprofile upload was provided (base64 zip), parse it
        curaprofile_b64 = data.pop("curaprofile_b64", None)
        if curaprofile_b64:
            import base64
            raw = base64.b64decode(curaprofile_b64)
            parsed = self._parse_curaprofile(raw)
            # Merge parsed settings, explicit body values take precedence
            merged_settings = {**parsed.get("settings", {}),
                               **data.get("settings", {})}
            data = {**parsed, **data, "settings": merged_settings}
            data["name"] = name

        path = self._profile_path(name)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Saved profile: {name}")
        return {"saved": name, "profile": data}

    def _parse_curaprofile(self, raw_bytes: bytes) -> Dict:
        """
        Parse a .curaprofile zip archive and extract settings as a flat dict.

        A .curaprofile is a ZIP containing one or more .inst.cfg files
        (INI-style). We merge all [values] sections into a flat settings dict.
        """
        settings: Dict[str, str] = {}
        display_name = ""
        definition = ""

        try:
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                for entry in zf.namelist():
                    if not entry.endswith(".cfg"):
                        continue
                    raw_cfg = zf.read(entry).decode("utf-8", errors="replace")
                    cfg = configparser.ConfigParser(strict=False)
                    cfg.read_string(raw_cfg)

                    if cfg.has_section("general"):
                        if not display_name and cfg.has_option("general", "name"):
                            display_name = cfg.get("general", "name")
                        if not definition and cfg.has_option("general", "definition"):
                            definition = cfg.get("general", "definition")

                    if cfg.has_section("values"):
                        for key, value in cfg.items("values"):
                            settings[key] = value
        except zipfile.BadZipFile as exc:
            raise ValueError(f"Invalid .curaprofile file: {exc}") from exc

        return {
            "display_name": display_name,
            "printer_definition": definition,
            "settings": settings,
        }

    # -------------------------------------------------------------------------
    # Printer definitions
    # -------------------------------------------------------------------------

    # Common system locations where Cura/CuraEngine installs definitions
    _SYSTEM_DEF_DIRS: List[Path] = [
        Path("/usr/share/cura/resources/definitions"),
        Path("/usr/share/cura-engine/resources/definitions"),
        Path("/usr/share/curaengine/resources/definitions"),
        Path("/usr/lib/cura/resources/definitions"),
        Path("/usr/lib/cura-engine/resources/definitions"),
        Path("/usr/local/share/cura/resources/definitions"),
    ]

    @staticmethod
    def _def_stem(p: Path) -> str:
        """Strip .def.json suffix to get the definition name."""
        name = p.name
        return name[:-len(".def.json")] if name.endswith(".def.json") else p.stem

    def _list_definitions(self) -> List[str]:
        names = [self._def_stem(p) for p in sorted(self.definitions_dir.glob("*.def.json"))]
        for sdir in self._SYSTEM_DEF_DIRS:
            if sdir.exists():
                for p in sorted(sdir.glob("*.def.json")):
                    stem = self._def_stem(p)
                    if stem not in names:
                        names.append(stem)
        return names

    def _resolve_definition_path(self, def_name: str) -> Optional[Path]:
        """Return the path to a printer definition JSON, checking local then system."""
        local = self.definitions_dir / f"{def_name}.def.json"
        if local.exists():
            return local
        for sdir in self._SYSTEM_DEF_DIRS:
            candidate = sdir / f"{def_name}.def.json"
            if candidate.exists():
                return candidate
        return None

    def _read_definition_overrides(
        self, def_name: str, keys: Iterable[str]
    ) -> Dict[str, Any]:
        """Return {setting_id: default_value} for the given keys, read directly
        from a definition's own "overrides"/"settings" sections (no "inherits"
        resolution – CuraEngine handles that itself via -j)."""
        result: Dict[str, Any] = {}
        if not def_name:
            return result
        path = self._resolve_definition_path(def_name)
        if not path:
            return result
        try:
            with open(path) as f:
                data = json.load(f)
            for section in ("overrides", "settings"):
                sec = data.get(section, {})
                for key in keys:
                    if key not in result and key in sec and "default_value" in sec[key]:
                        result[key] = sec[key]["default_value"]
        except Exception as exc:
            logger.warning(f"Could not read overrides from '{def_name}': {exc}")
        return result

    def _get_build_volume(self, def_name: str) -> Optional[tuple]:
        """Return (width_mm, depth_mm, height_mm) from a printer definition, or None."""
        if not def_name:
            return None
        overrides = self._read_definition_overrides(
            def_name, ("machine_width", "machine_depth", "machine_height"))
        w = overrides.get("machine_width")
        d = overrides.get("machine_depth")
        h = overrides.get("machine_height")
        if w is None and d is None and h is None:
            return None
        return (
            float(w) if w is not None else 220.0,
            float(d) if d is not None else 220.0,
            float(h) if h is not None else 250.0,
        )

    async def _handle_definitions(self, web_request: WebRequest) -> Any:
        method = web_request.get_action()

        if method == "GET":
            return {"definitions": self._list_definitions()}

        # POST – upload a definition JSON (base64-encoded)
        import base64
        body = web_request.get_args()
        def_name = body.get("name", "").strip()
        content_b64 = body.get("content_b64", "")
        if not def_name or not content_b64:
            raise self.server.error(
                "'name' and 'content_b64' are required", 400)
        raw = base64.b64decode(content_b64)
        # Validate it's valid JSON
        try:
            json.loads(raw)
        except json.JSONDecodeError as exc:
            raise self.server.error(
                f"Definition file is not valid JSON: {exc}", 400) from exc
        safe_name = re.sub(r"[^\w\-. ]", "_", def_name)
        dest = self.definitions_dir / f"{safe_name}.def.json"
        dest.write_bytes(raw)
        logger.info(f"Saved printer definition: {safe_name}")
        return {"saved": safe_name}

    async def _handle_definition(self, web_request: WebRequest) -> Any:
        def_name = web_request.get_str("def_name")
        method = web_request.get_action()

        if method == "GET":
            path = self._resolve_definition_path(def_name)
            if path is None:
                raise self.server.error(
                    f"Definition '{def_name}' not found", 404)
            with open(path) as f:
                return json.load(f)

        # DELETE – only local definitions can be deleted
        local = self.definitions_dir / f"{def_name}.def.json"
        if not local.exists():
            raise self.server.error(
                f"Definition '{def_name}' not found or is a system definition", 404)
        local.unlink()
        return {"deleted": def_name}

    # -------------------------------------------------------------------------
    # Slicing
    # -------------------------------------------------------------------------

    async def _handle_slice(self, web_request: WebRequest) -> Dict:
        body = web_request.get_args()

        stl_filename: str = body.get("filename", "").strip()
        profile_name: str = body.get("profile", "").strip()
        extra_settings: Dict[str, str] = body.get("settings", {})
        print_after: bool = body.get("print_after", False)
        transform: Optional[Dict[str, Any]] = body.get("transform", None)

        if not stl_filename:
            raise self.server.error("'filename' (STL path in gcodes root) is required", 400)
        if not profile_name:
            raise self.server.error("'profile' is required", 400)

        profile = self._load_profile(profile_name)
        if profile is None:
            raise self.server.error(f"Profile '{profile_name}' not found", 404)

        # Resolve the STL path
        data_path = Path(self.server.get_app_args()["data_path"])
        gcodes_root = data_path / "gcodes"
        stl_path = gcodes_root / stl_filename
        if not stl_path.exists():
            raise self.server.error(f"STL file not found: {stl_filename}", 404)
        if stl_path.suffix.lower() not in (".stl", ".obj", ".3mf"):
            raise self.server.error("Only .stl, .obj, and .3mf files are supported", 400)

        # Create job
        job_id = str(uuid.uuid4())[:8]
        output_name = stl_path.stem + f"_{job_id}.gcode"
        output_path = self.sliced_dir / output_name

        job: Dict[str, Any] = {
            "id": job_id,
            "status": "pending",
            "progress": 0.0,
            "progress_stage": "",
            "stl": stl_filename,
            "profile": profile_name,
            "output": f"sliced/{output_name}",
            "error": None,
            "print_after": print_after,
        }
        self._jobs[job_id] = job
        self._job_order.append(job_id)
        if len(self._job_order) > MAX_JOB_HISTORY:
            old = self._job_order.pop(0)
            self._jobs.pop(old, None)

        # Launch slicing in background
        merged_settings = {**profile.get("settings", {}), **extra_settings}
        def_name = profile.get("printer_definition", "")
        asyncio.ensure_future(
            self._run_slice(job, stl_path, output_path, def_name, merged_settings, transform)
        )

        return {"job_id": job_id, "status": "pending"}

    async def _run_slice(
        self,
        job: Dict[str, Any],
        stl_path: Path,
        output_path: Path,
        def_name: str,
        settings: Dict[str, str],
        transform: Optional[Dict[str, Any]] = None,
    ) -> None:
        job["status"] = "slicing"
        job["progress"] = 0.0

        # Apply mesh transform (rotation + scale + auto-placement) for STL files.
        # The preview always auto-centers/drops the mesh (applyTransformToMesh()
        # in ui/index.html runs unconditionally), so run the same step here even
        # when the client omitted "transform" (i.e. identity rotation/scale) –
        # otherwise an off-center upload would slice from its raw, uncentered
        # coordinates while the preview showed it centered on the bed.
        input_path = stl_path
        if stl_path.suffix.lower() == '.stl':
            transform = transform or {}
            rotation = transform.get("rotation", IDENTITY_ROTATION)
            scale    = float(transform.get("scale", 1.0))
            if (isinstance(rotation, list) and len(rotation) == 3
                    and all(isinstance(r, list) and len(r) == 3 for r in rotation)):
                transformed_path = self.temp_dir / f"xf_{job['id']}.stl"
                try:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None,
                        _transform_stl_binary,
                        str(stl_path),
                        str(transformed_path),
                        rotation,
                        scale,
                    )
                    input_path = transformed_path
                    logger.info(
                        f"[job {job['id']}] Applied transform: scale={scale:.3f}")
                except ValueError as exc:
                    logger.warning(
                        f"[job {job['id']}] STL transform skipped: {exc}")
                except Exception as exc:
                    logger.error(
                        f"[job {job['id']}] STL transform failed: {exc}; using original")
            else:
                logger.warning(
                    f"[job {job['id']}] Invalid rotation matrix in transform, ignoring")

        # Always load the bundled fdmprinter schema so every setting
        # CuraEngine might query has a real default (see resources/README.md
        # and BUNDLED_FDMPRINTER_PATH above).
        cmd = [self.cura_engine, "slice", "-v", "-p", "-j", str(BUNDLED_FDMPRINTER_PATH)]

        # The named printer definition (if resolvable) only contributes
        # overrides on top of that base – bed size, origin convention,
        # nozzle, etc. – via -s, rather than its own -j.
        machine_settings = dict(ESSENTIAL_MACHINE_DEFAULTS)
        if def_name:
            if self._resolve_definition_path(def_name):
                machine_settings.update(
                    self._read_definition_overrides(
                        def_name, ESSENTIAL_MACHINE_DEFAULTS.keys()))
            else:
                logger.warning(
                    f"Printer definition '{def_name}' not found; using generic machine defaults")

        for key, value in {**machine_settings, **settings}.items():
            cmd += ["-s", f"{key}={value}"]

        # Rotation and position are already baked into the STL vertices
        # above, so tell CuraEngine the mesh itself needs no further
        # transform.
        cmd += ["-s", "mesh_rotation_matrix=[[1,0,0],[0,1,0],[0,0,1]]"]
        cmd += ["-s", "mesh_position_x=0"]
        cmd += ["-s", "mesh_position_y=0"]
        cmd += ["-s", "mesh_position_z=0"]

        # Always create extruder 0 with the bundled fdmextruder schema, even
        # for this single-extruder setup – CuraEngine has no per-extruder
        # defaults (material_diameter, extruder_nr, ...) otherwise.
        cmd += ["-e0", "-j", str(BUNDLED_FDMEXTRUDER_PATH)]

        cmd += ["-o", str(output_path), "-l", str(input_path)]

        logger.info(f"[job {job['id']}] Running: {' '.join(cmd)}")
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Stream stderr for progress updates
            stderr_lines = []
            assert proc.stderr is not None
            async for raw_line in proc.stderr:
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                stderr_lines.append(line)
                m = PROGRESS_RE.search(line)
                if m:
                    stage = m.group(1)
                    pct = float(m.group(2))
                    job["progress"] = round(pct, 3)
                    job["progress_stage"] = stage

            await proc.wait()
            returncode = proc.returncode

            if returncode != 0:
                error_output = "\n".join(stderr_lines[-30:])
                raise RuntimeError(
                    f"CuraEngine exited with code {returncode}:\n{error_output}")

            if not output_path.exists() or output_path.stat().st_size == 0:
                raise RuntimeError("CuraEngine produced no output file")

            job["status"] = "complete"
            job["progress"] = 1.0
            logger.info(f"[job {job['id']}] Slicing complete: {output_path}")

            # Clean up temp transform file if one was created
            if input_path != stl_path:
                try:
                    input_path.unlink(missing_ok=True)
                except Exception:
                    pass

            # Notify moonraker file manager of the new gcode
            try:
                fm = self.server.lookup_component("file_manager")
                await fm.get_directory("gcodes", "sliced", False)
            except Exception:
                pass  # Non-fatal

            # Auto-print if requested
            if job.get("print_after"):
                await self._start_print(job["output"])

        except Exception as exc:
            job["status"] = "error"
            job["error"] = str(exc)
            logger.exception(f"[job {job['id']}] Slicing failed: {exc}")
            # Clean up temp transform file if one was created
            if input_path != stl_path:
                try:
                    input_path.unlink(missing_ok=True)
                except Exception:
                    pass

    async def _start_print(self, gcode_path: str) -> None:
        """Tell Klipper (via Moonraker) to start printing the gcode file."""
        try:
            klipper = self.server.lookup_component("klippy_connection")
            await klipper.request(
                {
                    "method": "printer.print.start",
                    "params": {"filename": gcode_path},
                }
            )
            logger.info(f"Auto-print started: {gcode_path}")
        except Exception as exc:
            logger.error(f"Failed to start print: {exc}")

    # -------------------------------------------------------------------------
    # Job management
    # -------------------------------------------------------------------------

    async def _handle_jobs(self, web_request: WebRequest) -> Dict:
        jobs = [self._jobs[jid] for jid in reversed(self._job_order)
                if jid in self._jobs]
        return {"jobs": jobs}

    async def _handle_job(self, web_request: WebRequest) -> Any:
        job_id = web_request.get_str("job_id")
        method = web_request.get_action()

        if job_id not in self._jobs:
            raise self.server.error(f"Job '{job_id}' not found", 404)

        if method == "DELETE":
            self._jobs.pop(job_id)
            if job_id in self._job_order:
                self._job_order.remove(job_id)
            return {"deleted": job_id}

        return self._jobs[job_id]


def load_component(config: ConfigHelper) -> CuraSlicer:
    return CuraSlicer(config)
