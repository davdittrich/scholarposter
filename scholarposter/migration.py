"""Migration from legacy lasttoot*.txt files to state.json."""
from __future__ import annotations

import json
import os
from pathlib import Path


# Maps legacy file names to platform names
_LEGACY_FILES = {
    "lasttoot_bluesky.txt": "bluesky",
    "lasttoot.txt": "linkedin",
}


def migrate_state_files(source_dir: Path, target_dir: Path) -> None:
    """Read legacy lasttoot*.txt files and write state.json.

    Does not overwrite an existing state.json.
    Silently skips missing source files.
    """
    state_path = target_dir / "state.json"
    if state_path.exists():
        return  # Do not overwrite

    state: dict = {}
    for filename, platform in _LEGACY_FILES.items():
        src = source_dir / filename
        if not src.exists():
            continue
        try:
            toot_id = int(src.read_text().strip())
            state[platform] = {"last_toot_id": toot_id, "last_status": "migrated"}
        except (ValueError, OSError):
            pass

    if not state:
        return

    tmp_path = state_path.with_suffix(".json.tmp")
    with open(tmp_path, "w") as f:
        json.dump(state, f, indent=2)
    os.rename(tmp_path, state_path)
    os.chmod(state_path, 0o600)
