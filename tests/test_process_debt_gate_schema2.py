"""RED-first regression: kanoniczny process_debt_gate.py migruje żywe v2→v4.

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
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from process_debt_gate import (  # noqa: E402
    ClaimConflict,
    GateError,
    GateStore,
    StorageError,
    _canonical_schema_sql,
    export_payload,
    public_ledger_projection,
)
import process_debt_gate as gate_module  # noqa: E402

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
        store.add_gate(
            gate_id="legacy.job225",
            title="legacy #225 fixture",
            kind="AT_JOB",
            owner="pytest",
            due_at="2026-08-02T00:00:00Z",
            next_step="investigate",
            blocker="missing alarm",
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
                INSERT INTO at_jobs (
                    job_key, gate_id, at_job_id, status, scheduled_for,
                    command_json, runner_token_hash, created_at, updated_at,
                    last_seen_at, auth_version, reconcile_note
                ) VALUES (
                    'at-legacy-225', 'legacy.job225', '225', 'MISSING_ALARM',
                    '2026-07-31T10:00:00Z', ?, ?,
                    '2026-07-30T10:00:00Z', '2026-07-30T10:00:00Z',
                    '2026-07-30T10:00:00Z', 1, 'legacy missing alarm'
                )
                """,
                (command_json, hashlib.sha256(b"legacy-token-225").hexdigest()),
            )
            conn.execute(
                """
                UPDATE gates SET state='WAIT_DATA', version=2
                WHERE gate_id='legacy.job224'
                """
            )
            conn.execute(
                """
                UPDATE gates SET state='WAIT_DATA', version=2, alarm=1,
                    alarm_reason='legacy missing alarm'
                WHERE gate_id='legacy.job225'
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


def _insert_valid_legacy_claim(path: Path, status: str) -> str:
    binding = json.dumps(
        {
            "schema_version": 1,
            "operation": "RUN",
            "job_key": "at-legacy-224",
            "gate_id": "legacy.job224",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(binding.encode("utf-8")).hexdigest()
    receipt_ready = status in {"RECEIPT_READY", "FINALIZED"}
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO at_job_claims (
                claim_id, job_key, gate_id, status, binding_json,
                binding_sha256, auth_tag, receipt_path, receipt_sha256,
                receipt_dev, receipt_ino, receipt_ctime_ns, receipt_size,
                exit_code, stdout_sha256, stderr_sha256, claimed_at,
                updated_at, finalized_at
            ) VALUES (
                ?, 'at-legacy-224', 'legacy.job224', ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, '2026-07-30T10:00:00Z',
                '2026-07-30T10:05:00Z', ?
            )
            """,
            (
                f"legacy-{status.lower()}",
                status,
                binding,
                digest,
                "d" * 64,
                "/nonexistent/pytest-receipt.json" if receipt_ready else "",
                "e" * 64 if receipt_ready else None,
                1 if receipt_ready else None,
                2 if receipt_ready else None,
                3 if receipt_ready else None,
                4 if receipt_ready else None,
                0 if receipt_ready else None,
                "f" * 64 if receipt_ready else None,
                "a" * 64 if receipt_ready else None,
                "2026-07-30T10:06:00Z" if status == "FINALIZED" else None,
            ),
        )
        connection.commit()
    return digest


@pytest.mark.parametrize("legacy_status", ["CLAIMED", "RECEIPT_READY", "FINALIZED"])
def test_nonempty_v2_claims_block_migration_without_mutating_v2(
    tmp_path: Path,
    legacy_status: str,
) -> None:
    db = tmp_path / f"legacy-{legacy_status.lower()}.sqlite3"
    _make_v2_db(db, with_legacy_job=True)
    _insert_valid_legacy_claim(db, legacy_status)
    with sqlite3.connect(db) as connection:
        before = dict(
            zip(
                [column[1] for column in connection.execute("PRAGMA table_info(at_job_claims)")],
                connection.execute("SELECT * FROM at_job_claims").fetchone(),
            )
        )
    before_json = json.dumps(before, sort_keys=True, separators=(",", ":"))

    traced_statements: list[str] = []

    class TracedStore(GateStore):
        @contextmanager
        def _write_connection(self):
            with super()._write_connection() as connection:
                connection.set_trace_callback(traced_statements.append)
                yield connection

    with pytest.raises(GateError, match="at_job_claims nie jest puste"):
        TracedStore(db).initialize()
    attempted_ddl = [
        statement
        for statement in traced_statements
        if statement.lstrip().upper().startswith(("CREATE ", "ALTER ", "DROP "))
    ]
    assert attempted_ddl == []
    assert _user_version(db) == 2
    with sqlite3.connect(db) as connection:
        after_columns = [
            column[1] for column in connection.execute("PRAGMA table_info(at_job_claims)")
        ]
        after = dict(
            zip(after_columns, connection.execute("SELECT * FROM at_job_claims").fetchone())
        )
        assert json.dumps(after, sort_keys=True, separators=(",", ":")) == before_json
        assert "auth_tag" in after_columns
        assert connection.execute(
            "SELECT status FROM at_jobs WHERE job_key='at-legacy-225'"
        ).fetchone()[0] == "MISSING_ALARM"


def test_export_refuses_nonempty_v2_before_any_public_projection(
    tmp_path: Path,
) -> None:
    db = tmp_path / "export-v2.sqlite3"
    _make_v2_db(db, with_legacy_job=True)
    _insert_valid_legacy_claim(db, "FINALIZED")
    with sqlite3.connect(db) as connection:
        legacy_binding = json.loads(
            connection.execute(
                "SELECT binding_json FROM at_job_claims"
            ).fetchone()[0]
        )
        legacy_binding["nested_auth_tag"] = "NESTED-MUST-NOT-LEAK"
        binding_json = json.dumps(
            legacy_binding,
            sort_keys=True,
            separators=(",", ":"),
        )
        connection.execute(
            "UPDATE at_job_claims SET binding_json=?, binding_sha256=?",
            (
                binding_json,
                hashlib.sha256(binding_json.encode("utf-8")).hexdigest(),
            ),
        )
        connection.commit()
    assert _user_version(db) == 2

    with pytest.raises(GateError, match="at_job_claims nie jest puste"):
        export_payload(
            GateStore(db),
            as_of=datetime(2026, 8, 1, 12, tzinfo=timezone.utc),
        )
    assert _user_version(db) == 2
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM at_job_claims").fetchone()[0] == 1
        assert "NESTED-MUST-NOT-LEAK" in connection.execute(
            "SELECT binding_json FROM at_job_claims"
        ).fetchone()[0]


def test_nonempty_drifted_v2_claim_table_still_fails_before_any_ddl(
    tmp_path: Path,
) -> None:
    db = tmp_path / "legacy-drifted-nonempty.sqlite3"
    _make_v2_db(db, with_legacy_job=True)
    _insert_valid_legacy_claim(db, "CLAIMED")
    with sqlite3.connect(db) as connection:
        connection.execute(
            "ALTER TABLE at_job_claims ADD COLUMN unreviewed_drift TEXT"
        )
        connection.commit()

    traced_statements: list[str] = []

    class TracedStore(GateStore):
        @contextmanager
        def _write_connection(self):
            with super()._write_connection() as connection:
                connection.set_trace_callback(traced_statements.append)
                yield connection

    with pytest.raises(GateError, match="at_job_claims nie jest puste"):
        TracedStore(db).initialize()
    assert _user_version(db) == 2
    assert not any(
        statement.lstrip().upper().startswith(("CREATE ", "ALTER ", "DROP "))
        for statement in traced_statements
    )


def test_export_rejects_unknown_columns_and_projection_is_allowlisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "schema-drift.sqlite3"
    _make_v2_db(db, with_legacy_job=True)
    store = GateStore(db)
    store.initialize()
    with sqlite3.connect(db) as connection:
        connection.execute(
            "ALTER TABLE at_jobs ADD COLUMN unexpected_sensitive TEXT"
        )
        connection.execute(
            "UPDATE at_jobs SET unexpected_sensitive='must-not-leak'"
        )
        connection.commit()

    with sqlite3.connect(db) as connection:
        connection.row_factory = sqlite3.Row
        material = {
            "schema_version": 3,
            "tables": {
                table: [
                    dict(row)
                    for row in connection.execute(
                        f"SELECT * FROM {table} ORDER BY {order_by}"
                    ).fetchall()
                ]
                for table, order_by in {
                    "gates": "gate_id",
                    "gate_events": "event_id",
                    "at_jobs": "job_key",
                    "at_job_claims": "claim_id",
                }.items()
            },
        }
    _gates, _events, jobs, _claims = public_ledger_projection(material)
    assert all("unexpected_sensitive" not in job for job in jobs)
    with pytest.raises(GateError, match="nadmiar"):
        export_payload(
            store,
            as_of=datetime(2026, 8, 1, 12, tzinfo=timezone.utc),
        )

    race_db = tmp_path / "schema-boundary-race.sqlite3"
    _make_v2_db(race_db, with_legacy_job=True)
    race_store = GateStore(race_db)
    race_store.initialize()
    original_initialize = race_store.initialize
    injected = False

    def initialize_then_inject_schema_drift() -> None:
        nonlocal injected
        original_initialize()
        if injected:
            return
        injected = True
        with sqlite3.connect(race_db) as connection:
            connection.execute(
                "ALTER TABLE at_jobs ADD COLUMN after_boundary_secret TEXT"
            )
            connection.execute(
                "UPDATE at_jobs SET after_boundary_secret='never-public'"
            )
            connection.commit()

    monkeypatch.setattr(race_store, "initialize", initialize_then_inject_schema_drift)
    with pytest.raises(GateError, match="exact schema manifest"):
        export_payload(
            race_store,
            as_of=datetime(2026, 8, 1, 12, tzinfo=timezone.utc),
        )
    assert injected is True


def test_corrupt_v2_claim_rolls_back_whole_migration(tmp_path: Path) -> None:
    db = tmp_path / "corrupt-v2.sqlite3"
    _make_v2_db(db, with_legacy_job=True)
    _insert_valid_legacy_claim(db, "FINALIZED")
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE at_job_claims SET binding_sha256=?",
            ("0" * 64,),
        )
        connection.commit()
    with pytest.raises(GateError, match="at_job_claims nie jest puste"):
        GateStore(db).initialize()
    assert _user_version(db) == 2
    with sqlite3.connect(db) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(at_job_claims)")
        }
        assert "auth_tag" in columns and "legacy_v2_record_sha256" not in columns
        assert connection.execute("SELECT COUNT(*) FROM at_job_claims").fetchone()[0] == 1


def test_initialize_accepts_v2_and_never_downgrades(tmp_path, monkeypatch):
    db = tmp_path / "gates.sqlite3"
    _make_v2_db(db, with_legacy_job=True)
    assert _user_version(db) == 2
    # Nie może rzucić GateError "nieobsługiwana wersja schematu SQLite: 2".
    GateStore(db).initialize()
    assert _user_version(db) == 4
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
        "binding_sha256", "receipt_path", "receipt_sha256", "receipt_dev",
        "receipt_ino", "receipt_ctime_ns", "receipt_size", "exit_code",
        "stdout_sha256", "stderr_sha256", "claimed_at", "updated_at",
        "finalized_at",
    }
    store = GateStore(db)
    legacy = store.show_at_job("at-legacy-224")
    assert legacy["auth_version"] == 1
    legacy_missing = store.show_at_job("at-legacy-225")
    assert legacy_missing["auth_version"] == 1
    assert legacy_missing["status"] == "MISSING_ALARM"
    assert legacy_missing["reconcile_note"] == "legacy missing alarm"
    claim = store.claim_at_job(
        "at-legacy-224",
        runner_token="legacy-token-224",
        command=["/bin/true"],
    )
    assert claim["binding"]["schema_version"] == 2

    # RED/mutation: pusta v2→v4 DDL jest atomowa i nie narusza auth kolumn/jobów.
    for index, failure_step in enumerate(
        (
            "schema-ddl",
            "legacy-claims-empty",
            "claim-table-rebuild",
            "legacy-claims-dropped",
            "validated",
        )
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
            assert connection.execute("SELECT COUNT(*) FROM at_job_claims").fetchone()[0] == 0
        GateStore(auth_db).initialize()
        assert _user_version(auth_db) == 4
        assert GateStore(auth_db).list_at_claims() == []

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
                ?, ?, ?, '',
                '2026-07-30T10:00:00Z', '2026-07-30T10:00:00Z'
            )
            """,
            (
                legacy_binding,
                hashlib.sha256(legacy_binding.encode("utf-8")).hexdigest(),
                "c" * 64,
            ),
        )
        connection.commit()
    with pytest.raises(GateError, match="at_job_claims nie jest puste"):
        GateStore(occupied).initialize()
    assert _user_version(occupied) == 2
    with sqlite3.connect(occupied) as connection:
        assert connection.execute(
            "SELECT status FROM at_job_claims"
        ).fetchone()[0] == "CLAIMED"

    # RED/mutation: pełna migracja v1→v4 jest atomowa przy każdym fault seam.
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
        assert _user_version(legacy_db) == 4


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
    assert _user_version(db) == 4


def test_fresh_db_initializes_at_v1(tmp_path):
    db = tmp_path / "gates.sqlite3"
    GateStore(db).initialize()
    assert _user_version(db) == 4


def test_exact_schema_attests_unique_checks_and_all_schema_objects(tmp_path: Path) -> None:
    db = tmp_path / "exact.sqlite3"
    store = GateStore(db)
    store.initialize()
    material = store.ledger_attestation_material()
    index = next(
        item
        for item in material["schema_manifest"]["tables"]["at_jobs"]["indexes"]
        if item["name"] == "at_jobs_one_active_per_gate"
    )
    assert index["unique"] == 1
    assert index["partial"] == 1

    with sqlite3.connect(db) as connection:
        connection.execute("DROP INDEX at_jobs_one_active_per_gate")
        connection.execute(
            "CREATE INDEX at_jobs_one_active_per_gate ON at_jobs(gate_id) "
            "WHERE status IN ('SUBMITTING', 'SCHEDULED', 'MISSING_ALARM')"
        )
        connection.commit()
    with pytest.raises(GateError, match="exact schema manifest"):
        store.initialize()
    with pytest.raises(GateError, match="exact schema manifest"):
        export_payload(store, as_of=datetime(2026, 8, 1, 12, tzinfo=timezone.utc))

    trigger_db = tmp_path / "trigger.sqlite3"
    trigger_store = GateStore(trigger_db)
    trigger_store.initialize()
    with sqlite3.connect(trigger_db) as connection:
        connection.execute(
            "CREATE TRIGGER injected_writer AFTER UPDATE ON gates "
            "BEGIN UPDATE gates SET blocker='mutated' WHERE gate_id=NEW.gate_id; END"
        )
        connection.commit()
    with pytest.raises(GateError, match="exact schema manifest"):
        trigger_store.initialize()

    # Token boundary i quoted literal są częścią manifestu; whitespace nie może
    # skleić `x IN` do funkcji/identyfikatora `xin` ani zmienić 'A B' na 'AB'.
    assert _canonical_schema_sql("CHECK (x IN ('A B'))") != _canonical_schema_sql(
        "CHECK (xin('A B'))"
    )
    assert _canonical_schema_sql("CHECK (x='A B')") != _canonical_schema_sql(
        "CHECK (x='AB')"
    )


def test_public_export_is_deny_by_default_and_never_leaks_private_paths(
    tmp_path: Path,
) -> None:
    marker = "PRIVATE-MARKER-NOT-PUBLIC"
    private_root = tmp_path / marker
    private_root.mkdir()
    payload_path = private_root / "payload.json"
    payload_path.write_bytes(b"sealed")
    payload_path.chmod(0o600)
    info = payload_path.stat()
    identity = {
        "sha256": hashlib.sha256(b"sealed").hexdigest(),
        "device": info.st_dev,
        "inode": info.st_ino,
        "ctime_ns": info.st_ctime_ns,
        "size": info.st_size,
    }
    artifact_root = str((private_root / "results").absolute())
    db = tmp_path / "public.sqlite3"
    store = GateStore(db)
    command = ["/bin/true", marker]
    token = "redaction-token"
    command_sha = hashlib.sha256(
        json.dumps(command, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    from process_debt_gate import runner_auth_binding, runner_auth_tag

    binding = runner_auth_binding(
        job_key="job-redaction",
        gate_id="public.redaction",
        scheduled_for="2026-08-01T10:00:00Z",
        command_sha256=command_sha,
        payload_sha256=identity["sha256"],
        artifact_root=artifact_root,
    )
    store.register_at_job(
        gate_id="public.redaction",
        title="redaction",
        owner="pytest",
        due_at="2026-08-03T00:00:00Z",
        blocker="wait",
        code_sha=CODE_SHA,
        evidence_hash=EVIDENCE,
        opened_at="2026-08-01T09:00:00Z",
        actor="pytest",
        job_key="job-redaction",
        runner_token_hash=hashlib.sha256(token.encode()).hexdigest(),
        scheduled_for="2026-08-01T10:00:00Z",
        command=command,
        runner_auth_hmac=runner_auth_tag(token, binding),
        payload_path=str(payload_path.absolute()),
        payload_identity=identity,
        artifact_root=artifact_root,
    )
    store.confirm_at_job("job-redaction", "991")
    claim = store.claim_at_job(
        "job-redaction",
        runner_token=token,
        command=command,
        payload_path=str(payload_path.absolute()),
        payload_identity=identity,
        artifact_root=artifact_root,
        require_auth_version=2,
        now=datetime(2026, 8, 1, 10, 0, 1, tzinfo=timezone.utc),
    )
    assert marker in store.show_at_job("job-redaction")["payload_path"]
    assert marker in claim["binding"]["receipt_path"]
    attestation = store.verify_active_run_claim(
        "job-redaction",
        claim_id=claim["claim_id"],
        command=command,
    )
    assert attestation["binding_sha256"] == claim["binding_sha256"]
    assert "receipt_path" not in attestation
    with pytest.raises(ClaimConflict, match="argv"):
        store.verify_active_run_claim(
            "job-redaction",
            claim_id=claim["claim_id"],
            command=["/bin/false", marker],
        )

    # Publiczna attestation nie może deklarować v4 na uszkodzonym/starym DB.
    with sqlite3.connect(db) as connection:
        connection.execute("DROP INDEX gates_open_order")
        connection.commit()
    with pytest.raises(ClaimConflict, match="schema drift"):
        store.verify_active_run_claim(
            "job-redaction",
            claim_id=claim["claim_id"],
            command=command,
        )
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE INDEX gates_open_order ON gates(state, opened_at, gate_id)"
        )
        connection.execute("PRAGMA user_version = 3")
        connection.commit()
    with pytest.raises(ClaimConflict, match="DB ma v3"):
        store.verify_active_run_claim(
            "job-redaction",
            claim_id=claim["claim_id"],
            command=command,
        )
    with sqlite3.connect(db) as connection:
        connection.execute("PRAGMA user_version = 4")
        connection.commit()

    public = export_payload(
        store,
        as_of=datetime(2026, 8, 1, 12, tzinfo=timezone.utc),
    )
    serialized = json.dumps(public, ensure_ascii=False, sort_keys=True)
    assert marker not in serialized
    assert str(db) not in serialized
    assert public["source"] == "process-gates-ledger"
    assert public["export_format_version"] == 2
    assert "metadata" not in public["gates"][0]
    assert "snapshot_json" not in public["gate_events"][0]
    assert "payload_path" not in public["at_jobs"][0]
    assert "artifact_root" not in public["at_jobs"][0]
    assert "receipt_path" not in public["at_job_claims"][0]
    assert "binding" not in public["at_job_claims"][0]
    assert set(public["at_job_claims"][0]) == {
        "claim_id", "job_key", "gate_id", "status", "binding_sha256",
        "receipt_sha256", "exit_code", "stdout_sha256", "stderr_sha256",
        "claimed_at", "updated_at", "finalized_at", "operation",
        "submission_rollback",
    }

    # Nawet samospójny digest nie może zalegalizować receipt poza artifact_root;
    # canonical verifier wyprowadza ścieżkę z root/job/claim, nie kopiuje inputu.
    injected = dict(claim["binding"])
    injected["receipt_path"] = "/off-root/injected/receipt.json"
    injected_json = json.dumps(injected, sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE at_job_claims SET binding_json=?, binding_sha256=?, receipt_path=? "
            "WHERE claim_id=?",
            (
                injected_json,
                hashlib.sha256(injected_json.encode()).hexdigest(),
                injected["receipt_path"],
                claim["claim_id"],
            ),
        )
        connection.commit()
    with pytest.raises(ClaimConflict, match="kanoniczne drzewo|identity mismatch"):
        store.verify_active_run_claim(
            "job-redaction",
            claim_id=claim["claim_id"],
            command=command,
        )


def test_storage_errors_are_domain_errors_at_gate_store_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_connect(*_args, **_kwargs):
        raise sqlite3.OperationalError("synthetic storage failure")

    monkeypatch.setattr(gate_module.sqlite3, "connect", broken_connect)
    with pytest.raises(StorageError, match="synthetic storage failure"):
        GateStore(tmp_path / "broken.sqlite3").initialize()


@pytest.mark.parametrize(
    "commit_error",
    [
        sqlite3.OperationalError("synthetic commit I/O"),
        sqlite3.IntegrityError("synthetic commit integrity"),
    ],
)
def test_commit_and_integrity_errors_never_escape_storage_boundary(
    tmp_path: Path,
    commit_error: sqlite3.Error,
) -> None:
    class CommitFaultConnection:
        def __init__(self, wrapped: sqlite3.Connection):
            self.wrapped = wrapped

        def __getattr__(self, name: str):
            return getattr(self.wrapped, name)

        def commit(self) -> None:
            raise commit_error

    class CommitFaultStore(GateStore):
        @contextmanager
        def _write_connection(self):
            with super()._write_connection() as connection:
                yield CommitFaultConnection(connection)

    with pytest.raises(StorageError, match="synthetic commit"):
        CommitFaultStore(tmp_path / "commit-fault.sqlite3").initialize()

    healthy = GateStore(tmp_path / "integrity-boundary.sqlite3")
    healthy.initialize()
    with pytest.raises(StorageError, match="synthetic direct integrity"):
        with healthy._write_connection():
            raise sqlite3.IntegrityError("synthetic direct integrity")


def test_rollback_and_close_errors_are_domain_storage_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RollbackFaultConnection:
        def __init__(self, wrapped: sqlite3.Connection):
            self.wrapped = wrapped

        def __getattr__(self, name: str):
            return getattr(self.wrapped, name)

        def commit(self) -> None:
            raise sqlite3.OperationalError("synthetic primary commit failure")

        def rollback(self) -> None:
            raise sqlite3.OperationalError("synthetic rollback failure")

    class RollbackFaultStore(GateStore):
        @contextmanager
        def _write_connection(self):
            with super()._write_connection() as connection:
                yield RollbackFaultConnection(connection)

    with pytest.raises(StorageError, match="synthetic rollback failure"):
        RollbackFaultStore(tmp_path / "rollback-fault.sqlite3").initialize()

    real_connect = sqlite3.connect
    close_targets: list[sqlite3.Connection] = []
    close_db = tmp_path / "close-fault.sqlite3"

    class CloseFaultConnection:
        def __init__(self, wrapped: sqlite3.Connection):
            object.__setattr__(self, "wrapped", wrapped)

        def __getattr__(self, name: str):
            return getattr(self.wrapped, name)

        def __setattr__(self, name: str, value) -> None:
            setattr(self.wrapped, name, value)

        def close(self) -> None:
            raise sqlite3.OperationalError("synthetic close failure")

    def connect_with_close_fault(database, *args, **kwargs):
        connection = real_connect(database, *args, **kwargs)
        if str(database) == str(close_db):
            close_targets.append(connection)
            return CloseFaultConnection(connection)
        return connection

    monkeypatch.setattr(gate_module.sqlite3, "connect", connect_with_close_fault)
    with pytest.raises(StorageError, match="synthetic close failure"):
        GateStore(close_db).initialize()
    for connection in close_targets:
        connection.close()


@pytest.mark.parametrize("mutation", ["index", "claim-status"])
def test_reviewed_schema_digest_rejects_unreviewed_source_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    if mutation == "index":
        changed = gate_module.SCHEMA + (
            "\nCREATE INDEX unreviewed_extra_writer_surface ON gates(owner);\n"
        )
    else:
        changed = gate_module.SCHEMA.replace(
            "'OUTCOME_UNKNOWN'\n    )),",
            "'OUTCOME_UNKNOWN', 'UNREVIEWED_BYPASS'\n    )),",
            1,
        )
        assert changed != gate_module.SCHEMA
    monkeypatch.setattr(gate_module, "SCHEMA", changed)
    with pytest.raises(GateError, match="reviewowanym manifestem"):
        GateStore(tmp_path / f"schema-mutation-{mutation}.sqlite3").initialize()
