"""Verify vendored B sources against their pinned archive; never contact a model."""

import hashlib
import json
from .db import ROOT


def main():
    manifest = json.loads((ROOT / "B_SOURCE.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        actual = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit("B source changed: " + name)
    print(
        f"Verified {len(manifest['files'])} unchanged B source files at {manifest['commit']}."
    )


if __name__ == "__main__":
    main()
