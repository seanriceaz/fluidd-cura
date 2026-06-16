#!/usr/bin/env python3
"""Cut a release from changelog.d/ fragments. Used by release.yml.

Reads every fragment in changelog.d/, bumps VERSION by the highest bump
level found, folds the fragments into CHANGELOG.md grouped by type,
deletes the consumed fragments, and writes release notes for the new
version to a temp file. Sets the following GitHub Actions outputs:

    released   "true" if a release was cut, "false" if there was nothing
               to release (no fragments present)
    version    the new version, e.g. "1.2.0" (only set if released)
    notes_file path to a file containing the release notes (only set if
               released)
"""
import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from changelog_lib import CHANGE_TYPES, fragment_paths, parse_fragment  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION_FILE = REPO_ROOT / "VERSION"
CHANGELOG_FILE = REPO_ROOT / "CHANGELOG.md"
CHANGELOG_DIR = REPO_ROOT / "changelog.d"
MARKER = "<!-- release-notes-start -->"
BUMP_RANK = {"patch": 0, "minor": 1, "major": 2}


def set_output(name: str, value: str) -> None:
    out_path = os.environ.get("GITHUB_OUTPUT")
    line = f"{name}={value}\n"
    if out_path:
        with open(out_path, "a") as f:
            f.write(line)
    else:
        print(line, end="")


def bump_version(current: str, bump: str) -> str:
    major, minor, patch = (int(p) for p in current.strip().split("."))
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def build_notes(version: str, fragments: list) -> str:
    today = datetime.date.today().isoformat()
    lines = [f"## [{version}] - {today}", ""]
    by_type = {t: [] for t in CHANGE_TYPES}
    for frag in fragments:
        by_type[frag["type"]].append(frag["body"])
    for change_type in CHANGE_TYPES:
        entries = by_type[change_type]
        if not entries:
            continue
        lines.append(f"### {change_type}")
        for entry in entries:
            for i, line in enumerate(entry.splitlines()):
                lines.append(line if i else f"- {line}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    paths = fragment_paths(CHANGELOG_DIR)
    if not paths:
        print("No changelog fragments found; nothing to release.")
        set_output("released", "false")
        return 0

    fragments = [parse_fragment(p) for p in paths]
    bump = max((f["bump"] for f in fragments), key=BUMP_RANK.get)

    current_version = VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else "0.0.0"
    new_version = bump_version(current_version, bump)
    notes = build_notes(new_version, fragments)

    changelog_text = CHANGELOG_FILE.read_text() if CHANGELOG_FILE.exists() else (
        "# Changelog\n\n" + MARKER + "\n"
    )
    if MARKER not in changelog_text:
        raise SystemExit(f"{CHANGELOG_FILE}: missing {MARKER!r} marker")
    changelog_text = changelog_text.replace(MARKER, MARKER + "\n\n" + notes.rstrip(), 1)

    VERSION_FILE.write_text(new_version + "\n")
    CHANGELOG_FILE.write_text(changelog_text)
    for frag in fragments:
        frag["path"].unlink()

    notes_path = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "release_notes.md"
    notes_path.write_text(notes)

    print(f"Releasing v{new_version} ({bump} bump) from {len(fragments)} fragment(s).")
    set_output("released", "true")
    set_output("version", new_version)
    set_output("notes_file", str(notes_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
