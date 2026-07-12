from __future__ import annotations

import json
import os
import stat
from datetime import date
from pathlib import Path

import pytest

from dispatch_v2.privacy import private_ledger as P
from dispatch_v2.tools import data_retention_migrate as MIG
from dispatch_v2.tools import data_retention_plan as PLAN
from dispatch_v2.tools import data_retention_rotate as ROT


SYNTH_KEY = b"synthetic-test-key-material-only-32-bytes-plus"


class StaticKey:
    def key_for_scope(self, scope: str) -> bytes:
        assert scope
        return SYNTH_KEY


def _key_file(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic.key-material"
    path.write_bytes(SYNTH_KEY)
    path.chmod(0o600)
    return path


def _malicious_record() -> dict:
    return {
        "schema": "synthetic.v1",
        "ts": "2026-01-02T03:04:05+00:00",
        "order_id": "synthetic-order-alpha",
        "delivery_address": "SYNTHETIC-ADDRESS-SECRET",
        "best": {
            "courier_id": "synthetic-courier-beta",
            "name": "SYNTHETIC-PERSON-SECRET",
            "score": 12.5,
            "pos": [1.25, 2.5],
        },
        "nested": {
            "harmless": "SYNTHETIC-FREE-TEXT-SECRET",
            "lat": 9.9,
            "pickup_lat": 53.123456,
            "dropoff_lng": 23.654321,
            "fallback_lon": 22.112233,
            "contact phone": "SYNTHETIC-CONTACT-SECRET",
            "phone": 987654321,
            "customer": 246813579,
            "numeric_name": 135792468,
            "reason": 1122334455,
            "courier_times": {"Alice": 17.25},
            "123456": {"value": "SYNTHETIC-MAPPING-SECRET"},
        },
        "verdict": "PROPOSE",
    }


def test_recursive_redaction_is_stable_scoped_and_covers_malicious_nested_values():
    source = _malicious_record()
    a1 = P.redact_record(source, key=SYNTH_KEY, scope="scope-a", ledger="world")
    a2 = P.redact_record(source, key=SYNTH_KEY, scope="scope-a", ledger="world")
    b = P.redact_record(source, key=SYNTH_KEY, scope="scope-b", ledger="world")

    assert a1 == a2
    assert a1["record"]["order_id"].startswith("p:order:")
    assert a1["record"]["order_id"] != b["record"]["order_id"]
    rendered = json.dumps(a1, ensure_ascii=False)
    for secret in (
        "synthetic-order-alpha", "SYNTHETIC-ADDRESS-SECRET",
        "synthetic-courier-beta", "SYNTHETIC-PERSON-SECRET",
        "SYNTHETIC-FREE-TEXT-SECRET", "SYNTHETIC-CONTACT-SECRET",
        "SYNTHETIC-MAPPING-SECRET", "123456",
        "53.123456", "23.654321", "22.112233", "987654321",
        "246813579", "135792468", "1122334455",
        "Alice",
    ):
        assert secret not in rendered
    assert source == _malicious_record(), "redactor must not mutate producer payload"
    dynamic_keys = list(a1["record"]["nested"]["courier_times"])
    assert len(dynamic_keys) == 1 and dynamic_keys[0].startswith("p:mapping-key:")
    assert "nested" in a1["record"] and "courier_times" in a1["record"]["nested"]


def test_secure_create_is_0600_even_under_umask_zero_and_parents_are_0700(tmp_path):
    root = tmp_path / "new-root" / "ledger"
    target = root / "records.jsonl"
    previous = os.umask(0)
    try:
        P.SecureJsonlWriter(target).append({"schema": "synthetic.v1", "value": 1})
    finally:
        os.umask(previous)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(root.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / ".records.jsonl.lock").stat().st_mode) == 0o600


def test_first_ledger_create_fsyncs_file_then_directory_entry(tmp_path, monkeypatch):
    target = tmp_path / "private" / "records.jsonl"
    original_fsync = P.os.fsync
    fsync_types: list[str] = []

    def observed_fsync(fd: int) -> None:
        mode = os.fstat(fd).st_mode
        fsync_types.append("dir" if stat.S_ISDIR(mode) else "file")
        original_fsync(fd)

    monkeypatch.setattr(P.os, "fsync", observed_fsync)
    P.SecureJsonlWriter(target).append({"synthetic": True})
    assert "file" in fsync_types
    assert "dir" in fsync_types
    assert fsync_types.index("file") < fsync_types.index("dir")


def test_existing_0644_symlink_hardlink_and_replaced_path_fail_closed(tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)

    wrong = root / "wrong.jsonl"
    wrong.write_text("", encoding="utf-8")
    wrong.chmod(0o644)
    with pytest.raises(P.PrivateLedgerError):
        P.SecureJsonlWriter(wrong).append({"x": 1})

    real = root / "real.jsonl"
    real.write_text("", encoding="utf-8")
    real.chmod(0o600)
    symlink = root / "symlink.jsonl"
    symlink.symlink_to(real.name)
    with pytest.raises((P.PrivateLedgerError, OSError)):
        P.SecureJsonlWriter(symlink).append({"x": 1})

    linked = root / "linked.jsonl"
    os.link(real, linked)
    with pytest.raises(P.PrivateLedgerError):
        P.SecureJsonlWriter(real).append({"x": 1})
    linked.unlink()

    replaced = root / "replaced.jsonl"
    P.SecureJsonlWriter(replaced).append({"before": True})

    def replace_path(_dir_fd: int, _data_fd: int, _name: str) -> None:
        replaced.rename(root / "displaced.jsonl")
        replaced.write_text("", encoding="utf-8")
        replaced.chmod(0o600)

    with pytest.raises(P.PrivateLedgerError):
        P.SecureJsonlWriter(replaced).append({"must_not_land": True}, pre_write_hook=replace_path)
    assert "must_not_land" not in replaced.read_text(encoding="utf-8")


def test_ancestor_symlink_is_refused_by_componentwise_dirfd_walk(tmp_path):
    real = tmp_path / "real-private-root"
    real.mkdir(mode=0o700)
    ancestor = tmp_path / "redirected-root"
    ancestor.symlink_to(real, target_is_directory=True)
    with pytest.raises(P.PrivateLedgerError, match="ancestor symlink|invalid component"):
        P.SecureJsonlWriter(ancestor / "nested" / "records.jsonl").append({"x": 1})
    assert not (real / "nested").exists()


def test_lock_path_replace_after_flock_is_detected_before_data_write(tmp_path):
    root = tmp_path / "private"
    target = root / "records.jsonl"
    writer = P.SecureJsonlWriter(target)
    writer.append({"sequence": 1})
    lock_path = root / ".records.jsonl.lock"

    def replace_lock(_dir_fd: int, _lock_fd: int, _name: str) -> None:
        lock_path.rename(root / ".records.jsonl.displaced-lock")
        lock_path.write_text("", encoding="utf-8")
        lock_path.chmod(0o600)

    with pytest.raises(P.PrivateLedgerError, match="lock path was replaced"):
        writer.append({"sequence": 2}, post_lock_hook=replace_lock)
    assert [json.loads(line) for line in target.read_text().splitlines()] == [
        {"sequence": 1},
    ]


def test_key_file_security_contract_rejects_mode_symlink_and_hardlink(tmp_path):
    key = _key_file(tmp_path)
    assert P.FileKeyProvider(key).key_for_scope("scope") == SYNTH_KEY
    key.chmod(0o644)
    with pytest.raises(P.PrivateLedgerError):
        P.FileKeyProvider(key).key_for_scope("scope")
    key.chmod(0o600)
    alias = tmp_path / "alias.key-material"
    os.link(key, alias)
    with pytest.raises(P.PrivateLedgerError):
        P.FileKeyProvider(key).key_for_scope("scope")
    alias.unlink()
    symlink = tmp_path / "symlink.key-material"
    symlink.symlink_to(key.name)
    with pytest.raises(P.PrivateLedgerError):
        P.FileKeyProvider(symlink).key_for_scope("scope")

    real_parent = tmp_path / "real-key-parent"
    real_parent.mkdir(mode=0o700)
    nested_key = _key_file(real_parent)
    redirected = tmp_path / "redirected-key-parent"
    redirected.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(P.PrivateLedgerError, match="ancestor symlink|invalid component"):
        P.FileKeyProvider(redirected / nested_key.name).key_for_scope("scope")


def test_key_and_private_reader_typo_paths_are_no_create(tmp_path, monkeypatch):
    missing_key_parent = tmp_path / "typo-key-parent"
    with pytest.raises(P.PrivateLedgerError, match="no-create reader"):
        P.FileKeyProvider(missing_key_parent / "fixture-material").key_for_scope("scope")
    assert not missing_key_parent.exists()

    missing_reader_root = tmp_path / "typo-reader-root"
    monkeypatch.setenv("ZIOMEK_PRIVATE_LEDGER_ROOT", str(missing_reader_root))
    with pytest.raises(P.PrivateLedgerError, match="no-create reader"):
        list(P.iter_ledger_records(
            missing_reader_root / "missing.jsonl", key_provider=StaticKey(),
        ))
    assert not missing_reader_root.exists()


def test_compat_is_exact_and_private_writes_only_secure_artifact(tmp_path, monkeypatch):
    from dispatch_v2 import shadow_dispatcher as SD

    legacy = tmp_path / "legacy.jsonl"
    record = {"ts": "2026-01-01T00:00:00+00:00", "order_id": "synthetic-a", "verdict": "PROPOSE"}
    monkeypatch.delenv("ZIOMEK_PRIVATE_LEDGER_MODE", raising=False)
    SD._append_decision(str(legacy), record)
    assert json.loads(legacy.read_text(encoding="utf-8")) == record

    root = tmp_path / "private-root"
    cfg = P.LedgerConfig(mode="private", root=str(root), scope="scope-a", key_file="unused")
    outcome = P.append_ledger_record(
        "shadow_decisions", legacy, record, config=cfg, key_provider=StaticKey())
    assert outcome == P.AppendOutcome("private", False, True)
    assert len(legacy.read_text(encoding="utf-8").splitlines()) == 1
    private_path = root / "shadow_decisions" / legacy.name
    private_raw = json.loads(private_path.read_text(encoding="utf-8"))
    assert private_raw["schema"] == P.SCHEMA
    assert stat.S_IMODE(private_path.stat().st_mode) == 0o600


def test_compat_writer_is_byte_identical_to_legacy_appender(tmp_path):
    from dispatch_v2.core.jsonl_appender import append_jsonl

    record = {"schema": "synthetic.v1", "nested": {"value": 1}, "text": "SYNTHETIC"}
    direct = tmp_path / "direct.jsonl"
    routed = tmp_path / "routed.jsonl"
    append_jsonl(direct, record)
    outcome = P.append_ledger_record(
        "shadow_decisions", routed, record,
        config=P.LedgerConfig(mode="compat"),
    )
    assert outcome == P.AppendOutcome("compat", True, False)
    assert routed.read_bytes() == direct.read_bytes()


def test_private_missing_key_writes_only_identifier_free_status(tmp_path):
    legacy = tmp_path / "must-not-exist.jsonl"
    root = tmp_path / "private-root"
    cfg = P.LedgerConfig(
        mode="private", root=str(root), scope="scope-a",
        key_file=str(tmp_path / "missing.key-material"),
    )
    with pytest.raises(P.PrivateLedgerError, match="cannot open private-ledger key"):
        P.append_ledger_record(
            "shadow_decisions", legacy, _malicious_record(), config=cfg)
    assert not legacy.exists()
    status_path = root / "status" / "shadow_decisions.jsonl"
    status_text = status_path.read_text(encoding="utf-8")
    assert "private_write_degraded" in status_text
    assert "SYNTHETIC" not in status_text and "synthetic-order" not in status_text


def test_invalid_mode_fails_before_both_artifacts_can_be_lost(tmp_path):
    legacy = tmp_path / "legacy.jsonl"
    with pytest.raises(P.PrivateLedgerError, match="invalid private-ledger mode"):
        P.append_ledger_record(
            "shadow_decisions", legacy, _malicious_record(),
            config=P.LedgerConfig(mode="typo"),
        )
    assert not legacy.exists()


def test_writer_failure_propagates_through_shadow_producer(tmp_path, monkeypatch):
    from dispatch_v2 import shadow_dispatcher as SD

    def fail_loud(*args, **kwargs):
        raise P.PrivateLedgerError("synthetic producer-visible failure")

    monkeypatch.setattr(P, "append_ledger_record", fail_loud)
    with pytest.raises(P.PrivateLedgerError, match="producer-visible"):
        SD._append_decision(str(tmp_path / "legacy.jsonl"), _malicious_record())


def test_writer_failure_propagates_through_world_producer(tmp_path, monkeypatch):
    from dispatch_v2 import world_record as WR

    monkeypatch.setattr(WR, "RECORD_DIR", str(tmp_path / "world"))
    monkeypatch.setattr(WR, "legacy_gc_allowed", lambda: False)

    def fail_loud(*args, **kwargs):
        raise P.PrivateLedgerError("synthetic world-visible failure")

    monkeypatch.setattr(WR, "append_ledger_record", fail_loud)

    class Result:
        verdict = "PROPOSE"

    with pytest.raises(P.PrivateLedgerError, match="world-visible"):
        WR._capture({}, {}, None, {}, [], Result(), live_inputs={})


def test_mirror_is_hold_and_retry_cannot_duplicate_legacy(tmp_path):
    legacy = tmp_path / "legacy.jsonl"
    cfg = P.LedgerConfig(
        mode="mirror", root=str(tmp_path / "private-root"),
        scope="scope-a", key_file="synthetic-fixture-carrier",
    )

    for _attempt in range(2):
        with pytest.raises(P.PrivateLedgerError, match="mirror mode is HOLD"):
            P.append_ledger_record(
                "shadow_decisions", legacy, _malicious_record(),
                config=cfg, key_provider=StaticKey(),
            )
    assert not legacy.exists()
    assert not (tmp_path / "private-root").exists()


def test_world_mirror_hold_cannot_run_gc_or_change_old_artifact(tmp_path, monkeypatch):
    from dispatch_v2 import world_record as WR

    legacy_root = tmp_path / "legacy-world"
    legacy_root.mkdir()
    old = legacy_root / "world_record-20000101.jsonl"
    old.write_text('{"synthetic":true}\n', encoding="utf-8")
    old.chmod(0o600)
    before = old.stat()
    unlink_calls: list[Path] = []

    monkeypatch.setenv("ZIOMEK_PRIVATE_LEDGER_MODE", "mirror")
    monkeypatch.setenv("ZIOMEK_PRIVATE_LEDGER_ROOT", str(tmp_path / "private-root"))
    monkeypatch.setenv("ZIOMEK_PRIVATE_LEDGER_SCOPE", "scope-a")
    monkeypatch.setenv("ZIOMEK_PRIVATE_LEDGER_KEY_FILE", "synthetic-unused-carrier")
    monkeypatch.setattr(WR, "RECORD_DIR", str(legacy_root))
    monkeypatch.setattr(
        Path, "unlink", lambda self, *args, **kwargs: unlink_calls.append(self),
    )

    class Result:
        verdict = "PROPOSE"

    assert P.legacy_gc_allowed() is False
    assert P.private_mode_active() is False
    with pytest.raises(P.PrivateLedgerError, match="mirror mode is HOLD"):
        WR._capture({}, {}, None, {}, [], Result(), live_inputs={})

    after = old.stat()
    assert unlink_calls == []
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
    assert list(legacy_root.iterdir()) == [old]
    assert not (tmp_path / "private-root").exists()


def test_world_private_mode_never_runs_legacy_delete_gc(tmp_path, monkeypatch):
    from dispatch_v2 import world_record as WR

    key = _key_file(tmp_path)
    monkeypatch.setenv("ZIOMEK_PRIVATE_LEDGER_MODE", "private")
    monkeypatch.setenv("ZIOMEK_PRIVATE_LEDGER_ROOT", str(tmp_path / "private-root"))
    monkeypatch.setenv("ZIOMEK_PRIVATE_LEDGER_SCOPE", "scope-a")
    monkeypatch.setenv("ZIOMEK_PRIVATE_LEDGER_KEY_FILE", str(key))
    monkeypatch.setattr(WR, "RECORD_DIR", str(tmp_path / "legacy-world"))
    gc_calls: list[object] = []
    monkeypatch.setattr(WR, "_gc", lambda now: gc_calls.append(now))

    class Result:
        verdict = "PROPOSE"

    WR._capture(
        {"order_id": "synthetic-order"}, {}, None, {}, [], Result(),
        live_inputs={},
    )
    assert gc_calls == []
    assert not (tmp_path / "legacy-world").exists()
    private_files = list((tmp_path / "private-root" / "world_record").glob("*.jsonl"))
    assert len(private_files) == 1


def test_old_and_new_reader_and_pseudonymized_golden_decision_projection_parity(tmp_path):
    old = {"ts": "2026-01-01T00:00:00+00:00", "order_id": "synthetic-a",
           "verdict": "PROPOSE", "best": {"courier_id": "synthetic-b", "score": 7.5},
           "pool_feasible_count": 2}
    new = P.redact_record(old, key=SYNTH_KEY, scope="scope-a", ledger="shadow_decisions")
    path = tmp_path / "mixed.jsonl"
    path.write_text(json.dumps(old) + "\n" + json.dumps(new) + "\n", encoding="utf-8")
    got = list(P.iter_ledger_records(path, key_provider=StaticKey()))
    assert got[0] == old
    assert got[1]["order_id"].startswith("p:order:")

    def decision_projection(rec: dict) -> tuple:
        return rec["verdict"], rec["best"]["score"], rec["pool_feasible_count"]

    assert decision_projection(got[0]) == decision_projection(got[1])


def test_private_reader_rejects_mode_leaf_symlink_hardlink_and_ancestor_symlink(
        tmp_path, monkeypatch):
    root = tmp_path / "private-reader-root"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("ZIOMEK_PRIVATE_LEDGER_ROOT", str(root))
    envelope = P.redact_record(
        {"ts": "2026-01-01T00:00:00+00:00", "order_id": "synthetic"},
        key=SYNTH_KEY, scope="scope-a", ledger="shadow_decisions",
    )
    good = root / "good.jsonl"
    P.SecureJsonlWriter(good).append(envelope)
    assert len(list(P.iter_ledger_records(good, key_provider=StaticKey()))) == 1

    good.chmod(0o644)
    with pytest.raises(P.PrivateLedgerError, match="mode mismatch"):
        list(P.iter_ledger_records(good, key_provider=StaticKey()))
    good.chmod(0o600)

    alias = root / "alias.jsonl"
    os.link(good, alias)
    with pytest.raises(P.PrivateLedgerError, match="hardlink count"):
        list(P.iter_ledger_records(good, key_provider=StaticKey()))
    alias.unlink()

    leaf_link = root / "leaf-link.jsonl"
    leaf_link.symlink_to(good.name)
    with pytest.raises(P.PrivateLedgerError, match="cannot open secure"):
        list(P.iter_ledger_records(leaf_link, key_provider=StaticKey()))

    real_root = tmp_path / "real-reader-root"
    real_root.mkdir(mode=0o700)
    real_file = real_root / "record.jsonl"
    P.SecureJsonlWriter(real_file).append(envelope)
    redirected_root = tmp_path / "redirected-reader-root"
    redirected_root.symlink_to(real_root, target_is_directory=True)
    monkeypatch.setenv("ZIOMEK_PRIVATE_LEDGER_ROOT", str(redirected_root))
    with pytest.raises(P.PrivateLedgerError, match="ancestor symlink|invalid component"):
        list(P.iter_ledger_records(
            redirected_root / real_file.name, key_provider=StaticKey(),
        ))


def test_retention_is_metadata_only_would_delete_and_never_calls_unlink(tmp_path, monkeypatch):
    root = tmp_path / "ledgers"
    root.mkdir()
    old = root / "world_record-20260101.jsonl"
    fresh = root / "world_record-20260130.jsonl"
    old.write_bytes(b"x" * 12)
    fresh.write_bytes(b"y" * 8)
    calls: list[Path] = []
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: calls.append(self))
    report = PLAN.plan_retention(root, retention_days=10, as_of=date(2026, 2, 1))
    assert report["mode"] == "would-delete"
    assert report["candidate_count"] == 1
    assert report["would_delete_bytes"] == 12
    assert calls == []
    assert old.exists() and fresh.exists()


def test_source_only_migrator_is_dry_run_apply_hold_and_retry_safe(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(mode=0o700)
    source = sandbox / "old.jsonl"
    destination = sandbox / "private" / "new.jsonl"
    source.write_text(json.dumps(_malicious_record()) + "\n", encoding="utf-8")
    key = _key_file(sandbox)
    before = {p.relative_to(sandbox): (p.stat().st_size, p.stat().st_mtime_ns)
              for p in sandbox.iterdir()}
    for _attempt in range(2):
        report = MIG.migrate_sandbox(
            source, destination, sandbox_root=sandbox, ledger="world_record",
            scope="scope-a", key_file=key, synthetic_fixture=True,
        )
        assert report["status"] == "WOULD_MIGRATE" and not report["mutated"]
        with pytest.raises(P.PrivateLedgerError, match="migration apply is HOLD"):
            MIG.migrate_sandbox(
                source, destination, sandbox_root=sandbox, ledger="world_record",
                scope="scope-a", key_file=key, synthetic_fixture=True, apply=True,
            )
    after = {p.relative_to(sandbox): (p.stat().st_size, p.stat().st_mtime_ns)
             for p in sandbox.iterdir()}
    assert after == before and not destination.exists()

    with pytest.raises(ValueError, match="live migration"):
        MIG.migrate_sandbox(
            "/root/.openclaw/workspace/dispatch_state/world_record/example.jsonl",
            destination, sandbox_root=sandbox, ledger="world_record",
            scope="scope-a", key_file=key, synthetic_fixture=True,
        )

    outside_key = _key_file(tmp_path)
    with pytest.raises(ValueError, match="inside sandbox"):
        MIG.migrate_sandbox(
            source, destination, sandbox_root=sandbox, ledger="world_record",
            scope="scope-a", key_file=outside_key, synthetic_fixture=True,
        )

    destination.parent.mkdir(mode=0o700)
    destination.write_text("synthetic-existing", encoding="utf-8")
    destination.chmod(0o600)
    existing_before = destination.stat().st_mtime_ns
    with pytest.raises(ValueError, match="destination must be absent"):
        MIG.migrate_sandbox(
            source, destination, sandbox_root=sandbox, ledger="world_record",
            scope="scope-a", key_file=key, synthetic_fixture=True,
        )
    assert destination.read_text() == "synthetic-existing"
    assert destination.stat().st_mtime_ns == existing_before


def test_rotation_tool_defaults_dry_run_and_apply_refuses_known_live(tmp_path, monkeypatch):
    sandbox = tmp_path / "sandbox"
    target = sandbox / "private" / "records.jsonl"
    P.SecureJsonlWriter(target).append({"synthetic": True})
    before = (target.stat().st_size, target.stat().st_mtime_ns)
    plan = ROT.plan_rotation(target, "records-archive.jsonl")
    assert plan["mode"] == "would-rotate" and not plan["mutated"]
    assert (target.stat().st_size, target.stat().st_mtime_ns) == before

    calls: list[object] = []
    monkeypatch.setattr(ROT, "rotate_secure_jsonl", lambda *a, **k: calls.append((a, k)))
    with pytest.raises(ValueError, match="known-live rotation"):
        ROT.rotate_sandbox(
            "/root/.openclaw/workspace/dispatch_state/private_ledger/records.jsonl",
            "records-archive.jsonl", sandbox_root=sandbox,
        )
    assert calls == []


def test_redactor_mutation_probe_unknown_string_would_be_detected(monkeypatch):
    # Mutation oracle: removing the default-free-text branch must expose marker.
    original = P._redact

    def mutated(value, pseudo, *, key="", depth=0):
        if key == "harmless" and isinstance(value, str):
            return value
        return original(value, pseudo, key=key, depth=depth)

    monkeypatch.setattr(P, "_redact", mutated)
    leaked = P.redact_record(_malicious_record(), key=SYNTH_KEY,
                             scope="scope-a", ledger="world")
    assert "SYNTHETIC-FREE-TEXT-SECRET" in json.dumps(leaked)


def test_redactor_numeric_and_location_key_mutations_are_observable(monkeypatch):
    original_location = P._is_location_key

    def mutated_location(key: str) -> bool:
        if key in {"pickup_lat", "dropoff_lng", "fallback_lon"}:
            return False
        return original_location(key)

    monkeypatch.setattr(P, "_is_location_key", mutated_location)
    monkeypatch.setattr(
        P, "_SENSITIVE_TEXT_PARTS",
        tuple(part for part in P._SENSITIVE_TEXT_PARTS
              if part not in {"phone", "customer", "name", "reason"}),
    )
    leaked = P.redact_record(
        _malicious_record(), key=SYNTH_KEY, scope="scope-a", ledger="world",
    )
    rendered = json.dumps(leaked)
    for exposed in (
        "53.123456", "23.654321", "22.112233", "987654321",
        "246813579", "135792468", "1122334455",
    ):
        assert exposed in rendered


def test_redactor_dynamic_mapping_key_mutation_is_observable(monkeypatch):
    monkeypatch.setattr(P, "_is_dynamic_mapping_container", lambda key: False)
    leaked = P.redact_record(
        _malicious_record(), key=SYNTH_KEY, scope="scope-a", ledger="world",
    )
    assert "Alice" in json.dumps(leaked)


def test_private_reader_missing_key_bad_auth_and_corrupt_envelope_are_fail_loud(tmp_path, monkeypatch):
    from dispatch_v2.tools import ledger_io as LIO
    from dispatch_v2.tools import world_replay as WR

    legacy = {"ts": "2026-01-01T00:00:00+00:00", "order_id": "synthetic-legacy"}
    private = P.redact_record(
        {"ts": "2026-01-01T00:00:01+00:00", "order_id": "synthetic-private"},
        key=SYNTH_KEY, scope="scope-a", ledger="shadow_decisions",
    )
    path = tmp_path / "mixed.jsonl"
    path.write_text("{legacy-corrupt-json\n" + json.dumps(legacy) + "\n" + json.dumps(private) + "\n",
                    encoding="utf-8")
    monkeypatch.delenv("ZIOMEK_PRIVATE_LEDGER_KEY_FILE", raising=False)

    legacy_only = tmp_path / "legacy-only.jsonl"
    legacy_only.write_text("{legacy-corrupt-json\n" + json.dumps(legacy) + "\n",
                           encoding="utf-8")
    assert list(P.iter_ledger_records(legacy_only)) == [legacy]

    # Legacy malformed JSON keeps historical skip semantics, but the recognised
    # private row cannot vanish from the denominator.
    with pytest.raises(P.PrivateLedgerError, match="key required"):
        list(P.iter_ledger_records(path))
    monkeypatch.setitem(LIO.LEDGER, "shadow", str(path))
    with pytest.raises(P.PrivateLedgerError, match="key required"):
        list(LIO.iter_shadow_decisions(None))
    with pytest.raises(P.PrivateLedgerError, match="key required"):
        list(WR._iter_jsonl(path))

    tampered = dict(private)
    tampered["record"] = dict(private["record"], verdict="TAMPERED")
    with pytest.raises(P.PrivateLedgerError, match="authentication failed"):
        P.decode_ledger_record(tampered, key_provider=StaticKey())
    corrupt = {"schema": P.SCHEMA, "classification": "pseudonymized"}
    with pytest.raises(P.PrivateLedgerError, match="payload invalid"):
        P.decode_ledger_record(corrupt, key_provider=StaticKey())


def test_private_file_malformed_or_truncated_json_is_fail_loud_everywhere(
        tmp_path, monkeypatch):
    from dispatch_v2.tools import ledger_io as LIO
    from dispatch_v2.tools import world_replay as WR

    root = tmp_path / "private-root"
    root.mkdir(mode=0o700)
    path = root / "shadow.jsonl"
    path.write_text('{"schema":"private_ledger.v1","record":', encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setenv("ZIOMEK_PRIVATE_LEDGER_ROOT", str(root))
    monkeypatch.setitem(LIO.LEDGER, "shadow", str(path))

    for read in (
        lambda: list(P.iter_ledger_records(path)),
        lambda: list(LIO.iter_shadow_decisions(None)),
        lambda: list(WR._iter_jsonl(path)),
    ):
        with pytest.raises(P.PrivateLedgerError, match="malformed or truncated"):
            read()

    valid = P.redact_record(
        {"ts": "2026-01-01T00:00:00+00:00", "order_id": "synthetic"},
        key=SYNTH_KEY, scope="scope-a", ledger="shadow_decisions",
    )
    mixed = tmp_path / "mixed-transition.jsonl"
    mixed.write_text(json.dumps(valid) + "\n{", encoding="utf-8")
    with pytest.raises(P.PrivateLedgerError, match="malformed or truncated"):
        list(P.iter_ledger_records(mixed, key_provider=StaticKey()))


def test_world_replay_plans_and_lock_redirect_are_coupled_fail_closed(
        tmp_path, monkeypatch):
    from dispatch_v2.tools import world_replay as WR

    class DP:
        _A2_FEED_CACHE = {}
        _perf_plans_cache = {}

    class C:
        A2_RELIABILITY_FEED_PATH = tmp_path / "unused.json"

    patched: list[str] = []

    def rejecting_patch(module, attr, value):
        patched.append(attr)
        if attr == "LOCK_FILE":
            raise RuntimeError("synthetic coupled-lock redirect failure")
        monkeypatch.setattr(module, attr, value)

    rec = {"live_inputs": {"plans": {"synthetic": {"value": 1}}}}
    with pytest.raises(RuntimeError, match="coupled-lock"):
        WR._serve_live_inputs(rec, DP, C, str(tmp_path), rejecting_patch)
    assert patched == ["PLANS_FILE", "LOCK_FILE"]


def test_private_denominator_mutation_probe_detects_skip_semantics(tmp_path, monkeypatch):
    private = P.redact_record(
        {"ts": "2026-01-01T00:00:00+00:00", "order_id": "synthetic-private"},
        key=SYNTH_KEY, scope="scope-a", ledger="shadow_decisions",
    )
    path = tmp_path / "one.jsonl"
    path.write_text(json.dumps(private) + "\n", encoding="utf-8")
    with pytest.raises(P.PrivateLedgerError):
        list(P.iter_ledger_records(path))

    # Explicit mutation witness: the historical bad pattern (private error ->
    # continue) changes denominator 1 -> 0 and is therefore observable.
    original = P.decode_ledger_record

    def mutated_skip(record, *, key_provider=None):
        try:
            return original(record, key_provider=key_provider)
        except P.PrivateLedgerError:
            return None

    monkeypatch.setattr(P, "decode_ledger_record", mutated_skip)
    assert list(P.iter_ledger_records(path)) == []
