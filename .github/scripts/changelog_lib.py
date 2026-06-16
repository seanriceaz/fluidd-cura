"""Shared helpers for parsing changelog.d/ fragments.

Fragment format:

    bump: patch | minor | major
    type: Added | Changed | Fixed | Removed | Security

    Free-form body text, folded into CHANGELOG.md under the matching
    type heading.
"""
from pathlib import Path

BUMP_LEVELS = ("patch", "minor", "major")
CHANGE_TYPES = ("Added", "Changed", "Fixed", "Removed", "Security")
IGNORED_NAMES = {"README.md", "TEMPLATE.md", ".gitkeep"}


class FragmentError(ValueError):
    pass


def fragment_paths(changelog_dir: Path):
    if not changelog_dir.is_dir():
        return []
    return sorted(
        p for p in changelog_dir.glob("*.md") if p.name not in IGNORED_NAMES
    )


def parse_fragment(path: Path) -> dict:
    text = path.read_text()
    if "\n\n" not in text:
        raise FragmentError(
            f"{path}: expected a header block, a blank line, then body text"
        )
    header_block, body = text.split("\n\n", 1)

    header = {}
    for line in header_block.splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" not in line:
            raise FragmentError(f"{path}: malformed header line {line!r}")
        key, value = line.split(":", 1)
        header[key.strip().lower()] = value.strip()

    bump = header.get("bump")
    if bump not in BUMP_LEVELS:
        raise FragmentError(
            f"{path}: 'bump' must be one of {BUMP_LEVELS}, got {bump!r}"
        )

    change_type = header.get("type", "Changed")
    if change_type not in CHANGE_TYPES:
        raise FragmentError(
            f"{path}: 'type' must be one of {CHANGE_TYPES}, got {change_type!r}"
        )

    body = body.strip()
    if not body:
        raise FragmentError(f"{path}: body text is empty")

    return {"bump": bump, "type": change_type, "body": body, "path": path}
