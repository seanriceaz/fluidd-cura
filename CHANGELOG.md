# Changelog

All notable changes to this project are documented in this file.

Entries are written by contributors as changelog fragments under
[`changelog.d/`](changelog.d/TEMPLATE.md) and compiled into this file
automatically by the [release workflow](.github/workflows/release.yml)
when a PR merges to `main`.
<!-- release-notes-start -->

## [1.1.0] - 2026-06-16

### Added
- Added semantic versioning, a changelog-fragment convention for PRs, and a
GitHub Actions workflow that tags and publishes a GitHub Release with an
assembled changelog whenever a PR merges to `main`. The plugin and UI now
report the running version.
- `install.sh` now registers fluidd-cura with Moonraker's `update_manager`
(`git_repo` type), so Fluidd's Settings → Update Manager page can detect
and apply repository updates instead of never showing them. Applying an
update now also redeploys the web UI automatically via a new
`scripts/deploy_ui.sh` install_script, instead of requiring a manual
`./install.sh` rerun.

### Fixed
- Fixed a CuraEngine crash ("no value given: mesh_rotation_matrix") that made
every slice job fail, regardless of whether a rotation was applied.

## [1.0.0] - 2026-06-16

Initial versioned release.
