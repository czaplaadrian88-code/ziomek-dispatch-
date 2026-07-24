"""RED-first regression: kanoniczny process_debt_gate.py MUSI współpracować z żywą
bazą ledgera na `user_version=2`.

Root cause: auth-hardening at-jobów (sealed-payload) podbił żywą bazę do
`user_version=2` i dołożył kolumny `at_jobs`; tabele `gates`/`gate_events` są
wersjonowo-stabilne (identyczne w 1 i 2). Master odrzucał wszystko >1 i degradował
wersję z powrotem do 1 (`PRAGMA user_version = 1`), co blokowało transition bramek
i desynchronizowało kontrakt auth at-jobów. Oracle poniżej czerwienieje na
niezmienionym module i zielenieje po fixie; mutacja (przywrócenie odrzucenia/degradacji)
ponownie czerwieni.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from process_debt_gate import GateStore  # noqa: E402

CODE_SHA = "0" * 40
EVIDENCE = "a" * 64
DUE = "2026-08-01T00:00:00Z"

# Dokładnie kolumny auth v2 obecne w żywym ledgerze (patrz `.schema` at_jobs).
AUTH_V2_ALTERS = [
    "ALTER TABLE at_jobs ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 1 "
    "CHECK (auth_version IN (1, 2))",
    "ALTER TABLE at_jobs ADD COLUMN runner_auth_tag TEXT",
    "ALTER TABLE at_jobs ADD COLUMN command_sha256 TEXT",
    "ALTER TABLE at_jobs ADD COLUMN payload_path TEXT",
    "ALTER TABLE at_jobs ADD COLUMN payload_sha256 TEXT",
    "ALTER TABLE at_jobs ADD COLUMN payload_dev INTEGER",
    "ALTER TABLE at_jobs ADD COLUMN payload_ino INTEGER",
    "ALTER TABLE at_jobs ADD COLUMN payload_ctime_ns INTEGER",
    "ALTER TABLE at_jobs ADD COLUMN payload_size INTEGER",
    "ALTER TABLE at_jobs ADD COLUMN artifact_root TEXT",
]


def _make_v2_db(path: Path) -> None:
    """Kanoniczna baza v1, potem hardening auth at-jobów (user_version=2)."""
    GateStore(path).initialize()
    conn = sqlite3.connect(path)
    try:
        for stmt in AUTH_V2_ALTERS:
            conn.execute(stmt)
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
    finally:
        conn.close()


def _user_version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def test_initialize_accepts_v2_and_never_downgrades(tmp_path):
    db = tmp_path / "gates.sqlite3"
    _make_v2_db(db)
    assert _user_version(db) == 2
    # Nie może rzucić GateError "nieobsługiwana wersja schematu SQLite: 2".
    GateStore(db).initialize()
    # Kanoniczna wersja NIE może zostać zdegradowana (desync kontraktu auth at-jobów).
    assert _user_version(db) == 2
    # Kolumny auth v2 muszą przetrwać ensure-schema.
    cols = {
        row[1]
        for row in sqlite3.connect(db).execute("PRAGMA table_info(at_jobs)").fetchall()
    }
    assert "auth_version" in cols and "payload_sha256" in cols


def test_transition_records_on_v2_db(tmp_path):
    db = tmp_path / "gates.sqlite3"
    _make_v2_db(db)
    store = GateStore(db)
    store.add_gate(
        gate_id="test.schema2-probe",
        title="probe",
        kind="TEST",
        owner="CTO",
        due_at=DUE,
        next_step="n",
        blocker="b",
        code_sha=CODE_SHA,
        evidence_hash=EVIDENCE,
    )
    rec = store.transition(
        "test.schema2-probe",
        "WAIT_DATA",
        expected_version=1,
        actor="test",
        reason="probe",
    )
    assert rec["state"] == "WAIT_DATA"
    assert _user_version(db) == 2


def test_fresh_db_initializes_at_v1(tmp_path):
    db = tmp_path / "gates.sqlite3"
    GateStore(db).initialize()
    assert _user_version(db) == 1
