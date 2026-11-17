"""Create a consistent SQLite backup without overwriting any existing file."""

import argparse
import sqlite3
from pathlib import Path
from sqlalchemy.engine import make_url
from .db import DEFAULT_DB, ROOT
import os


def backup(output, url=None):
    url = make_url(url or os.environ.get("DATABASE_URL", DEFAULT_DB))
    if (
        url.get_backend_name() != "sqlite"
        or not url.database
        or url.database == ":memory:"
    ):
        raise ValueError(
            "Use pg_dump or RDS backup for PostgreSQL; this command accepts file-backed SQLite only."
        )
    source, target = Path(url.database).resolve(), Path(output).resolve()
    if not source.is_file() or source == target or target.exists():
        raise ValueError(
            "Source must exist and backup target must be a different, new file."
        )
    if not target.is_relative_to(ROOT):
        raise ValueError("Backup output must stay within the project workspace.")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids overwriting a target created after the existence check.
    with target.open("xb"):
        pass
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
        with sqlite3.connect(target) as copy:
            original.backup(copy)
            if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError(
                    "Backup integrity check failed; preserve the source and inspect the output."
                )
    return target


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    print(backup(args.output))


if __name__ == "__main__":
    main()
