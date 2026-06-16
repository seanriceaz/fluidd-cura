# Changelog fragments

Every PR that changes user-facing behavior must add one file here, named
after the change (e.g. `fix-mesh-rotation-crash.md`). Copy `TEMPLATE.md` to
start.

Format:

```
bump: patch | minor | major
type: Added | Changed | Fixed | Removed | Security

One or two sentences describing the change, for end users.
```

- `bump` follows [semver](https://semver.org/): `patch` for fixes, `minor`
  for backwards-compatible features, `major` for breaking changes.
- `type` groups the entry in `CHANGELOG.md` (Keep a Changelog categories).

When a PR merges to `main`, the [release workflow](../.github/workflows/release.yml)
collects every fragment, bumps `VERSION` by the highest bump found, folds the
fragments into `CHANGELOG.md`, deletes them, and tags/publishes a GitHub
Release.

PRs that don't change user-facing behavior (docs, CI, refactors with no
behavior change) can skip this by applying the `skip-changelog` label
instead of adding a fragment.
