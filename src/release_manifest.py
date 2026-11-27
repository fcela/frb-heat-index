"""Record the exact inputs needed to replay a published index."""

import hashlib
import json
from pathlib import Path


def write_manifest(root: Path, path: Path, command: str, built_at: str,
                   through: str, inputs: list[Path]) -> None:
    digests = {}
    for source in sorted(set(inputs)):
        with source.open("rb") as f:
            digests[source.relative_to(root).as_posix()] = hashlib.file_digest(f, "sha256").hexdigest()
    manifest = {
        "build_command": command,
        "built_at_utc": built_at,
        "through_quarter": through,
        "inputs_sha256": digests,
    }
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
