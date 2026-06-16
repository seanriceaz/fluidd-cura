## fdmprinter.def.json / fdmextruder.def.json

Vendored from the [Ultimaker/Cura](https://github.com/Ultimaker/Cura) repository,
tag `4.13.0` (matching the `Cura_SteamEngine 4.13.0` version of CuraEngine these
defaults are validated against), under Cura's LGPL-3.0 license.

CuraEngine builds its settings registry entirely from whatever is passed via
`-j`. The distro `cura-engine` package only ships the engine binary, not
Cura's resource definitions, so without these files CuraEngine has no schema
at all and aborts with `Trying to retrieve setting with no value given` for
the first setting that isn't explicitly passed on the command line.

- `fdmprinter.def.json` is loaded via `-j` for the global settings stack –
  printer-specific profiles only need to override values that differ from
  this generic base (bed size, origin, nozzle, ...).
- `fdmextruder.def.json` defines a *separate* schema for per-extruder
  settings (`material_diameter`, `extruder_nr`, nozzle offsets, ...) that
  isn't part of `fdmprinter.def.json` at all. CuraEngine only loads it, and
  only stamps `extruder_nr` onto the extruder, when `-j` is given right
  after switching context with `-e<N>` – so `cura_slicer.py` always does
  `-e0 -j fdmextruder.def.json` even for single-extruder prints.
