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
import subprocess
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..websockets import WebRequest

logger = logging.getLogger(__name__)

# Maximum number of completed jobs to keep in memory
MAX_JOB_HISTORY = 20

# CuraEngine progress pattern from stderr with -p flag
PROGRESS_RE = re.compile(r"Progress:(\w+):([0-9.]+)")


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

        logger.info("CuraSlicer component initialized")

    # -------------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------------

    async def _handle_status(self, web_request: WebRequest) -> Dict:
        version = await self._get_engine_version()
        definitions = self._list_definitions()
        return {
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
        method = web_request.get_action().name

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
        body = web_request.get_json_body()
        return await self._save_profile(body)

    async def _handle_profile(self, web_request: WebRequest) -> Any:
        profile_name = web_request.get_str("profile_name")
        method = web_request.get_action().name

        if method == "GET":
            data = self._load_profile(profile_name)
            if data is None:
                raise self.server.error(f"Profile '{profile_name}' not found", 404)
            return data

        if method == "DELETE":
            path = self._profile_path(profile_name)
            if not path.exists():
                raise self.server.error(f"Profile '{profile_name}' not found", 404)
            path.unlink()
            logger.info(f"Deleted profile: {profile_name}")
            return {"deleted": profile_name}

        # POST – update
        body = web_request.get_json_body()
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

    def _list_definitions(self) -> List[str]:
        names = [p.stem for p in sorted(self.definitions_dir.glob("*.def.json"))]
        # Also look for system-installed definitions
        system_dirs = [
            Path("/usr/share/cura/resources/definitions"),
            Path("/usr/share/curaengine/resources/definitions"),
        ]
        for sdir in system_dirs:
            if sdir.exists():
                for p in sorted(sdir.glob("*.def.json")):
                    stem = p.stem.replace(".def", "")
                    if stem not in names:
                        names.append(stem)
        return names

    def _resolve_definition_path(self, def_name: str) -> Optional[Path]:
        """Return the path to a printer definition JSON, checking local then system."""
        # Local definitions take precedence
        local = self.definitions_dir / f"{def_name}.def.json"
        if local.exists():
            return local
        # System locations
        system_dirs = [
            Path("/usr/share/cura/resources/definitions"),
            Path("/usr/share/curaengine/resources/definitions"),
        ]
        for sdir in system_dirs:
            candidate = sdir / f"{def_name}.def.json"
            if candidate.exists():
                return candidate
        return None

    async def _handle_definitions(self, web_request: WebRequest) -> Any:
        method = web_request.get_action().name

        if method == "GET":
            return {"definitions": self._list_definitions()}

        # POST – upload a definition JSON (base64-encoded)
        import base64
        body = web_request.get_json_body()
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
        method = web_request.get_action().name

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
        body = web_request.get_json_body()

        stl_filename: str = body.get("filename", "").strip()
        profile_name: str = body.get("profile", "").strip()
        extra_settings: Dict[str, str] = body.get("settings", {})
        print_after: bool = body.get("print_after", False)

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
            self._run_slice(job, stl_path, output_path, def_name, merged_settings)
        )

        return {"job_id": job_id, "status": "pending"}

    async def _run_slice(
        self,
        job: Dict[str, Any],
        stl_path: Path,
        output_path: Path,
        def_name: str,
        settings: Dict[str, str],
    ) -> None:
        job["status"] = "slicing"
        job["progress"] = 0.0

        cmd = [self.cura_engine, "slice", "-v", "-p"]

        # Printer definition
        if def_name:
            def_path = self._resolve_definition_path(def_name)
            if def_path:
                cmd += ["-j", str(def_path)]
            else:
                logger.warning(
                    f"Printer definition '{def_name}' not found; slicing without it")

        # Settings
        for key, value in settings.items():
            cmd += ["-s", f"{key}={value}"]

        cmd += ["-o", str(output_path), "-l", str(stl_path)]

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
        method = web_request.get_action().name

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
