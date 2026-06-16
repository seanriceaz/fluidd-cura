## fdmprinter.def.json

Vendored from the [Ultimaker/Cura](https://github.com/Ultimaker/Cura) repository,
tag `4.13.0` (matching the `Cura_SteamEngine 4.13.0` version of CuraEngine these
defaults are validated against), under Cura's LGPL-3.0 license.

CuraEngine builds its settings registry from whatever is passed via `-j`. The
distro `cura-engine` package only ships the engine binary, not Cura's resource
definitions, so without this file CuraEngine has no schema at all and aborts
with `Trying to retrieve setting with no value given` for the first setting
that isn't explicitly passed on the command line. `cura_slicer.py` always
loads this file via `-j` to give every setting a real default; printer-specific
profiles only need to override values that differ from the generic base
(bed size, origin, nozzle, ...).
