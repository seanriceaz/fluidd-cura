bump: minor
type: Added

`install.sh` now registers fluidd-cura with Moonraker's `update_manager`
(`git_repo` type), so Fluidd's Settings → Update Manager page can detect
and apply repository updates instead of never showing them.
