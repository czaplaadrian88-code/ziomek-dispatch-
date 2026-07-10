"""Read-only adapters over the 10 identity sources (Z-P1-05 Faza A).

Every loader takes an EXPLICIT path — no path is baked into a signature default
and none is a frozen module constant used implicitly (C17). ``default_paths()``
computes the live locations late (env override -> canon fallback; repo_root
self-locates from this file). ``load_all()`` returns a :class:`SourceBundle`
plus a list of human-readable notes (missing/optional sources).

Nothing here writes, connects to a network, or mutates the sources. The sqlite
source (courier_api.db) is optional and opened read-only; a missing/locked db is
skipped with a note.
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .schema import canon_cid

__all__ = [
    "default_paths",
    "SourceBundle",
    "load_all",
    "load_kurier_ids",
    "load_kurier_piny",
    "load_courier_names",
    "load_courier_tiers",
    "load_grafik_full_names",
    "load_daily_full_names",
    "load_shift_ignored",
    "load_whitelist",
    "load_excluded_cids",
    "load_courier_api_names",
]


def default_paths(
    state_root: Optional[str] = None,
    repo_root: Optional[str] = None,
) -> Dict[str, str]:
    """Late-bound canonical paths for all sources.

    ``state_root`` (env ``ZIOMEK_STATE_ROOT`` -> dispatch_state) holds the JSON
    state + courier_api.db. ``repo_root`` (env ``ZIOMEK_REPO_ROOT`` -> the
    dispatch_v2 dir containing this package) holds daily_accounting/*.
    """
    if state_root is None:
        state_root = os.environ.get(
            "ZIOMEK_STATE_ROOT", "/root/.openclaw/workspace/dispatch_state"
        )
    if repo_root is None:
        repo_root = os.environ.get("ZIOMEK_REPO_ROOT") or str(
            Path(__file__).resolve().parents[1]
        )
    s = Path(state_root)
    r = Path(repo_root)
    return {
        "state_root": str(s),
        "repo_root": str(r),
        "kurier_ids": str(s / "kurier_ids.json"),
        "kurier_piny": str(s / "kurier_piny.json"),
        "courier_names": str(s / "courier_names.json"),
        "courier_tiers": str(s / "courier_tiers.json"),
        "grafik_full_names": str(s / "grafik_full_names.json"),
        "shift_ignored": str(s / "shift_ignored_names.json"),
        "whitelist": str(s / "courier_whitelist_v1.json"),
        "courier_api_db": str(s / "courier_api.db"),
        "daily_full_names": str(r / "daily_accounting" / "kurier_full_names.json"),
    }


def _load_json(path: str) -> Any:
    """Fail-open JSON read. Returns ``None`` on any failure (caller decides)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_kurier_ids(path: str) -> Dict[str, Any]:
    """``{alias -> cid}`` with cid value preserved (int in JSON) for 1:1 parity."""
    data = _load_json(path)
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items()}


def load_kurier_piny(path: str) -> Dict[str, str]:
    """``{pin -> alias}`` (pin kept as str; treated as secret downstream)."""
    data = _load_json(path)
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def load_courier_names(path: str) -> Dict[str, str]:
    """``{cid(str) -> panel display name}``."""
    data = _load_json(path)
    if not isinstance(data, dict):
        return {}
    return {canon_cid(k): str(v) for k, v in data.items()}


def load_courier_tiers(path: str) -> Dict[str, Any]:
    """Raw ``{cid(str) -> tier dict}`` including ``_meta`` (caller skips it)."""
    data = _load_json(path)
    if not isinstance(data, dict):
        return {}
    out: Dict[str, Any] = {}
    for k, v in data.items():
        out[k if k == "_meta" else canon_cid(k)] = v
    return out


def load_grafik_full_names(path: str) -> Dict[str, str]:
    """``{full name -> cid(str)}`` (JSON holds int)."""
    data = _load_json(path)
    if not isinstance(data, dict):
        return {}
    return {str(k): canon_cid(v) for k, v in data.items()}


def load_daily_full_names(path: str) -> Dict[str, str]:
    """``{alias -> full name}`` (daily_accounting; lives in the repo)."""
    data = _load_json(path)
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def load_shift_ignored(path: str) -> List[str]:
    """List of ignored full names (from the ``names`` key)."""
    data = _load_json(path)
    if isinstance(data, dict):
        names = data.get("names")
        if isinstance(names, list):
            return [str(n) for n in names]
    return []


def load_whitelist(path: str) -> Dict[str, str]:
    """``{cid(str) -> tier}`` derived from CONDITIONAL entries (optional source)."""
    data = _load_json(path)
    if not isinstance(data, dict):
        return {}
    out: Dict[str, str] = {}
    for section in ("WHITELIST", "CONDITIONAL", "BLACKLIST", "INSUFFICIENT_DATA"):
        rows = data.get(section)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and "cid" in row:
                out[canon_cid(row["cid"])] = str(row.get("tier") or "")
    return out


def load_excluded_cids(source: Optional[Any] = None) -> Set[str]:
    """EXCLUDED_CIDS as ``{cid(str)}``.

    ``source`` may be an explicit iterable of cids (tests) — otherwise the
    daily_accounting config constant is lazily imported. Fail-open to empty.
    """
    if source is None:
        try:
            from dispatch_v2.daily_accounting.config import EXCLUDED_CIDS as source
        except Exception:
            return set()
    try:
        return {canon_cid(c) for c in source}
    except Exception:
        return set()


def load_courier_api_names(db_path: str) -> Optional[Dict[str, str]]:
    """``{cid(str) -> name}`` from courier_api.db (OPTIONAL, read-only sqlite).

    Returns ``None`` when the db is absent/unreadable (caller adds a note). Reads
    the denormalized ``courier_name`` from whichever known tables exist; last
    non-null wins.
    """
    if not db_path or not os.path.exists(db_path):
        return None
    out: Dict[str, str] = {}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    except Exception:
        return None
    try:
        cur = con.cursor()
        for table in ("sessions", "courier_status_events", "courier_phones",
                      "courier_availability"):
            try:
                cur.execute(
                    f"SELECT courier_id, courier_name FROM {table} "
                    f"WHERE courier_name IS NOT NULL AND courier_id IS NOT NULL"
                )
            except Exception:
                continue  # table or column missing — skip
            for cid, name in cur.fetchall():
                c = canon_cid(cid)
                if c and name:
                    out[c] = str(name)
    finally:
        con.close()
    return out


@dataclass
class SourceBundle:
    """All raw identity sources loaded read-only, plus provenance notes."""

    paths: Dict[str, str]
    kurier_ids: Dict[str, Any] = field(default_factory=dict)
    kurier_piny: Dict[str, str] = field(default_factory=dict)
    courier_names: Dict[str, str] = field(default_factory=dict)
    courier_tiers: Dict[str, Any] = field(default_factory=dict)
    grafik_full_names: Dict[str, str] = field(default_factory=dict)
    daily_full_names: Dict[str, str] = field(default_factory=dict)
    shift_ignored: List[str] = field(default_factory=list)
    whitelist: Dict[str, str] = field(default_factory=dict)
    excluded_cids: Set[str] = field(default_factory=set)
    courier_api_names: Optional[Dict[str, str]] = None
    notes: List[str] = field(default_factory=list)


def load_all(
    paths: Optional[Dict[str, str]] = None,
    *,
    excluded_source: Optional[Any] = None,
    with_sqlite: bool = True,
) -> SourceBundle:
    """Load every source read-only into a :class:`SourceBundle`."""
    if paths is None:
        paths = default_paths()
    notes: List[str] = []

    def _note_if_missing(key: str) -> None:
        p = paths.get(key)
        if p and not os.path.exists(p):
            notes.append(f"source missing: {key} ({p})")

    for key in ("kurier_ids", "kurier_piny", "courier_names", "courier_tiers",
                "grafik_full_names", "daily_full_names", "shift_ignored",
                "whitelist"):
        _note_if_missing(key)

    api_names = None
    if with_sqlite:
        api_names = load_courier_api_names(paths.get("courier_api_db", ""))
        if api_names is None:
            notes.append("source skipped (optional): courier_api.db")

    return SourceBundle(
        paths=paths,
        kurier_ids=load_kurier_ids(paths.get("kurier_ids", "")),
        kurier_piny=load_kurier_piny(paths.get("kurier_piny", "")),
        courier_names=load_courier_names(paths.get("courier_names", "")),
        courier_tiers=load_courier_tiers(paths.get("courier_tiers", "")),
        grafik_full_names=load_grafik_full_names(paths.get("grafik_full_names", "")),
        daily_full_names=load_daily_full_names(paths.get("daily_full_names", "")),
        shift_ignored=load_shift_ignored(paths.get("shift_ignored", "")),
        whitelist=load_whitelist(paths.get("whitelist", "")),
        excluded_cids=load_excluded_cids(excluded_source),
        courier_api_names=api_names,
        notes=notes,
    )
