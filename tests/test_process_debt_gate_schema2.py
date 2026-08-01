"""RED-first regression: kanoniczny process_debt_gate.py migruje żywe v2→v3.

Root cause: auth-hardening at-jobów (sealed-payload) podbił żywą bazę do
`user_version=2` i dołożył kolumny `at_jobs`; tabele `gates`/`gate_events` są
wersjonowo-stabilne (identyczne w 1 i 2). Master odrzucał wszystko >1 i degradował
wersję z powrotem do 1. Execution-claim dodaje tabelę `at_job_claims`, dlatego
nie może ponownie użyć numeru v2. Oracle zachowuje istniejący aktywny auth1 job
(odpowiednik #224), ale nowy produkcyjny writer tworzy wyłącznie auth2.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from process_debt_gate import GateStore  # noqa: E402

CODE_SHA = "0" * 40
EVIDENCE = "a" * 64
DUE = "2026-08-01T00:00:00Z"


LEGACY_V2_CLAIMS_SQL = """
CREATE TABLE at_job_claims (
    claim_id TEXT PRIMARY KEY,
    job_key TEXT NOT NULL UNIQUE REFERENCES at_jobs(job_key),
    gate_id TEXT NOT NULL REFERENCES gates(gate_id),
    status TEXT NOT NULL CHECK (status IN ('CLAIMED', 'RECEIPT_READY', 'FINALIZED')),
    binding_json TEXT NOT NULL,
    binding_sha256 TEXT NOT NULL,
    auth_tag TEXT NOT NULL,
    receipt_path TEXT NOT NULL,
    receipt_sha256 TEXT,
    receipt_dev INTEGER,
    receipt_ino INTEGER,
    receipt_ctime_ns INTEGER,
    receipt_size INTEGER,
    exit_code INTEGER,
    stdout_sha256 TEXT,
    stderr_sha256 TEXT,
    claimed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finalized_at TEXT
)
"""


def _make_v2_db(path: Path, *, with_legacy_job: bool = False) -> None:
    """Realistyczny live v2: szeroka legacy tabela claims istnieje i jest pusta."""

    store = GateStore(path)
    store.initialize()
    if with_legacy_job:
        store.add_gate(
            gate_id="legacy.job224",
            title="legacy #224 fixture",
            kind="AT_JOB",
            owner="pytest",
            due_at="2026-08-02T00:00:00Z",
            next_step="wait",
            blocker="wait",
            code_sha=CODE_SHA,
            evidence_hash=EVIDENCE,
        )

    conn = sqlite3.connect(path)
    try:
        if with_legacy_job:
            command = ["/bin/true"]
            argv_sha = hashlib.sha256(
                json.dumps(command, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            command_json = json.dumps(
                {"argv_sha256": argv_sha, "argc": 1},
                sort_keys=True,
                separators=(",", ":"),
            )
            token_hash = hashlib.sha256(b"legacy-token-224").hexdigest()
            conn.execute(
                """
                INSERT INTO at_jobs (
                    job_key, gate_id, at_job_id, status, scheduled_for,
                    command_json, runner_token_hash, created_at, updated_at,
                    last_seen_at, auth_version
                ) VALUES (
                    'at-legacy-224', 'legacy.job224', '224', 'SCHEDULED',
                    '2026-07-31T10:00:00Z', ?, ?,
                    '2026-07-30T10:00:00Z', '2026-07-30T10:00:00Z',
                    '2026-07-30T10:00:00Z', 1
                )
                """,
                (command_json, token_hash),
            )
            conn.execute(
                """
                UPDATE gates SET state='WAIT_DATA', version=2
                WHERE gate_id='legacy.job224'
                """
            )
        conn.execute("DROP TABLE at_job_claims")
        conn.execute(LEGACY_V2_CLAIMS_SQL)
        conn.execute(
            "CREATE INDEX at_job_claims_status "
            "ON at_job_claims(status, claimed_at, job_key)"
        )
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
    finally:
        conn.close()

def _make_v1_db(path: Path) -> None:
    """Deterministyczny predecessor v1 bez kolumn auth i tabeli claimów."""

    GateStore(path).initialize()
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DROP TABLE at_job_claims")
        conn.execute("DROP INDEX at_jobs_queue_id")
        conn.execute("DROP INDEX at_jobs_one_active_per_gate")
        conn.execute("ALTER TABLE at_jobs RENAME TO at_jobs_v2_fixture")
        conn.execute(
            """
            CREATE TABLE at_jobs (
                job_key TEXT PRIMARY KEY,
                gate_id TEXT NOT NULL REFERENCES gates(gate_id),
                at_job_id TEXT,
                status TEXT NOT NULL CHECK (status IN (
                    'SUBMITTING', 'SCHEDULED', 'MISSING_ALARM', 'SUCCEEDED',
                    'FAILED', 'SUBMISSION_FAILED', 'CANCELLED'
                )),
                scheduled_for TEXT NOT NULL,
                command_json TEXT NOT NULL,
                runner_token_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_seen_at TEXT,
                finished_at TEXT,
                exit_code INTEGER,
                result_evidence_hash TEXT,
                reconcile_note TEXT NOT NULL DEFAULT ''
            )
            """
        )
        legacy = (
            "job_key,gate_id,at_job_id,status,scheduled_for,command_json,"
            "runner_token_hash,created_at,updated_at,last_seen_at,finished_at,"
            "exit_code,result_evidence_hash,reconcile_note"
        )
        conn.execute(
            f"INSERT INTO at_jobs ({legacy}) SELECT {legacy} FROM at_jobs_v2_fixture"
        )
        conn.execute("DROP TABLE at_jobs_v2_fixture")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    finally:
        conn.close()


def _user_version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def test_initialize_accepts_v2_and_never_downgrades(tmp_path, monkeypatch):
    db = tmp_path / "gates.sqlite3"
    _make_v2_db(db, with_legacy_job=True)
    assert _user_version(db) == 2
    # Nie może rzucić GateError "nieobsługiwana wersja schematu SQLite: 2".
    GateStore(db).initialize()
    assert _user_version(db) == 3
    # Kolumny auth v2 muszą przetrwać ensure-schema.
    cols = {
        row[1]
        for row in sqlite3.connect(db).execute("PRAGMA table_info(at_jobs)").fetchall()
    }
    assert "auth_version" in cols and "payload_sha256" in cols
    tables = {
        row[0]
        for row in sqlite3.connect(db).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "at_job_claims" in tables
    claim_cols = {
        row[1]
        for row in sqlite3.connect(db).execute(
            "PRAGMA table_info(at_job_claims)"
        )
    }
    assert claim_cols == {
        "claim_id", "job_key", "gate_id", "status", "binding_json",
        "binding_sha256", "receipt_sha256", "exit_code", "claimed_at",
        "updated_at", "finalized_at",
    }
    store = GateStore(db)
    legacy = store.show_at_job("at-legacy-224")
    assert legacy["auth_version"] == 1
    claim = store.claim_at_job(
        "at-legacy-224",
        runner_token="legacy-token-224",
        command=["/bin/true"],
    )
    assert claim["binding"]["schema_version"] == 1

    # RED/mutation: v2→v3 DDL jest atomowe i nie narusza auth kolumn/jobów.
    for index, failure_step in enumerate(
        ("schema-ddl", "claim-table-rebuild", "validated")
    ):
        auth_db = tmp_path / f"auth-v2-{index}.sqlite3"
        _make_v2_db(auth_db, with_legacy_job=True)
        failing = GateStore(auth_db)

        def crash_v2(step, expected=failure_step):
            if step == expected:
                raise RuntimeError("synthetic v2 migration crash")

        monkeypatch.setattr(failing, "_migration_checkpoint", crash_v2)
        try:
            failing.initialize()
        except Exception:
            pass
        else:
            raise AssertionError(f"fault {failure_step} nie zatrzymał migracji")
        with sqlite3.connect(auth_db) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
            auth_cols = {
                row[1] for row in connection.execute("PRAGMA table_info(at_jobs)")
            }
            assert "auth_version" in auth_cols
            assert connection.execute(
                "SELECT auth_version FROM at_jobs WHERE at_job_id='224'"
            ).fetchone()[0] == 1
            legacy_claim_cols = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(at_job_claims)"
                )
            }
            assert "auth_tag" in legacy_claim_cols
        GateStore(auth_db).initialize()
        assert _user_version(auth_db) == 3

    # Nie wolno cicho zgubić starego claimu podczas przebudowy tabeli.
    occupied = tmp_path / "auth-v2-occupied.sqlite3"
    _make_v2_db(occupied, with_legacy_job=True)
    legacy_binding = json.dumps(
        {"schema_version": 1, "operation": "RUN"},
        sort_keys=True,
        separators=(",", ":"),
    )
    with sqlite3.connect(occupied) as connection:
        connection.execute(
            """
            INSERT INTO at_job_claims (
                claim_id, job_key, gate_id, status, binding_json,
                binding_sha256, auth_tag, receipt_path, claimed_at, updated_at
            ) VALUES (
                'legacy-claim', 'at-legacy-224', 'legacy.job224', 'CLAIMED',
                ?, ?, 'legacy-auth', '',
                '2026-07-30T10:00:00Z', '2026-07-30T10:00:00Z'
            )
            """,
            (
                legacy_binding,
                hashlib.sha256(legacy_binding.encode("utf-8")).hexdigest(),
            ),
        )
        connection.commit()
    try:
        GateStore(occupied).initialize()
    except Exception as exc:
        assert "ręcznego rozliczenia" in str(exc)
    else:
        raise AssertionError("v2 claim nie może zostać cicho usunięty")
    assert _user_version(occupied) == 2

    # RED/mutation: pełna migracja v1→v3 jest atomowa przy każdym fault seam.
    for index, failure_step in enumerate(
        ("schema-ddl", "at_jobs.auth_version", "at_jobs.payload_size", "validated")
    ):
        legacy_db = tmp_path / f"legacy-{index}.sqlite3"
        _make_v1_db(legacy_db)
        failing = GateStore(legacy_db)

        def crash(step, expected=failure_step):
            if step == expected:
                raise RuntimeError("synthetic migration crash")

        monkeypatch.setattr(failing, "_migration_checkpoint", crash)
        try:
            failing.initialize()
        except Exception:
            pass
        else:
            raise AssertionError(f"fault {failure_step} nie zatrzymał migracji")
        with sqlite3.connect(legacy_db) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
            legacy_cols = {
                row[1] for row in connection.execute("PRAGMA table_info(at_jobs)")
            }
            assert "auth_version" not in legacy_cols
            legacy_tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert "at_job_claims" not in legacy_tables
        GateStore(legacy_db).initialize()
        assert _user_version(legacy_db) == 3


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
    assert _user_version(db) == 3


def test_fresh_db_initializes_at_v1(tmp_path):
    db = tmp_path / "gates.sqlite3"
    GateStore(db).initialize()
    assert _user_version(db) == 3
