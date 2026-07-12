"""Wspolny fail-closed guard mutujacych migracji SQLite.

Source sprinty A360 nie migruja live DB. Kazdy mutator wymaga jawnego kontraktu
syntetycznego sandboxu, kanonicznego celu pod ``/tmp`` i odrzuca znane live DB,
ich hardlinki oraz dowolna sciezke zawierajaca symlink. Inspekcja read-only ma
osobny entry-point i nie korzysta z tego helpera.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


KNOWN_LIVE_EVENT_DATABASES: tuple[Path, ...] = (
    Path("/root/.openclaw/workspace/dispatch_state/events.db"),
)
SYNTHETIC_SANDBOX_ROOT = Path("/tmp")


class SyntheticMigrationTargetRequired(RuntimeError):
    pass


def _absolute_without_symlink_resolution(db_path: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(os.path.expanduser(os.fspath(db_path))))


def require_synthetic_migration_target(
    db_path: str | os.PathLike[str],
    *,
    synthetic_sandbox: bool,
) -> Path:
    """Waliduje cel PRZED sqlite3.connect i zwraca kanoniczna sciezke."""
    if not synthetic_sandbox:
        raise SyntheticMigrationTargetRequired(
            "mutating migration requires explicit synthetic sandbox"
        )
    absolute = _absolute_without_symlink_resolution(db_path)
    resolved = absolute.resolve(strict=False)
    if absolute != resolved:
        raise SyntheticMigrationTargetRequired(
            "mutating migration target cannot contain symbolic links"
        )
    sandbox = SYNTHETIC_SANDBOX_ROOT.resolve(strict=True)
    if resolved == sandbox or sandbox not in resolved.parents:
        raise SyntheticMigrationTargetRequired(
            "mutating migration target must be inside the synthetic /tmp sandbox"
        )
    for known in KNOWN_LIVE_EVENT_DATABASES:
        known_resolved = known.expanduser().resolve(strict=False)
        if resolved == known_resolved:
            raise SyntheticMigrationTargetRequired(
                "mutating migration refuses a known-live database"
            )
        try:
            if resolved.exists() and known.exists() and os.path.samefile(resolved, known):
                raise SyntheticMigrationTargetRequired(
                    "mutating migration refuses a known-live database alias"
                )
        except OSError as exc:
            raise SyntheticMigrationTargetRequired(
                "mutating migration target identity could not be verified"
            ) from exc
    return resolved


def require_synthetic_connection(
    conn: sqlite3.Connection,
    *,
    synthetic_sandbox: bool,
) -> None:
    """Powtarza guard dla pre-opened connection przed pierwszym write."""
    rows = conn.execute("PRAGMA database_list").fetchall()
    main_path = next(
        (str(row[2]) for row in rows if str(row[1]) == "main"),
        "",
    )
    if not main_path:
        if not synthetic_sandbox:
            raise SyntheticMigrationTargetRequired(
                "in-memory migration requires explicit synthetic sandbox"
            )
        return
    require_synthetic_migration_target(
        main_path,
        synthetic_sandbox=synthetic_sandbox,
    )


__all__ = [
    "KNOWN_LIVE_EVENT_DATABASES",
    "SYNTHETIC_SANDBOX_ROOT",
    "SyntheticMigrationTargetRequired",
    "require_synthetic_connection",
    "require_synthetic_migration_target",
]
