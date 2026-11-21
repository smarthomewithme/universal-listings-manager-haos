"""Offline recovery helper for a local developer install. HAOS uses Supervisor backups."""

import argparse
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path


def backup(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    source_db = source / "listings.sqlite3"
    if not source_db.is_file():
        raise FileNotFoundError(source_db)
    if source == destination or source in destination.parents:
        raise ValueError("Miejsce docelowe musi znajdować się poza /data.")
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        with (
            closing(sqlite3.connect(source_db.as_uri() + "?mode=ro", uri=True)) as db,
            closing(sqlite3.connect(destination / source_db.name)) as target,
        ):
            db.backup(target)
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Kopia SQLite jest uszkodzona.")
        key = source / "olx.key"
        if key.is_file():
            shutil.copyfile(key, destination / key.name)
            os.chmod(destination / key.name, 0o600)
        legacy = source / "photos"
        if legacy.is_dir():
            shutil.copytree(legacy, destination / "photos", symlinks=True)
    except BaseException:
        shutil.rmtree(destination)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Utwórz kopię do nowego katalogu.")
    parser.add_argument("source")
    parser.add_argument("destination")
    args = parser.parse_args()
    backup(args.source, args.destination)
