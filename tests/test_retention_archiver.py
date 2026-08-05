"""Testy OD-7 retention archiver (tools/retention_archiver.py).

Hermetyczne w 100 %: każdy test buduje własne drzewo plików w `tmp_path` i własną
politykę wskazującą na to drzewo. Żywy `dispatch_state`/`scripts/logs` nie jest ani
czytany, ani (tym bardziej) zapisywany — poza testami, które ASERTUJĄ, że bramka
zapisu odrzuca ścieżki żywych korzeni.

Zakres (brief 297 §6): klasyfikacja · progi wieku · report vs apply gating ·
manifest+sha · maskowanie · fail-closed.
"""
import gzip
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Ładujemy NARZĘDZIE Z TEGO drzewa po ścieżce pliku (nie przez pakiet `dispatch_v2.tools`),
# bo w pkgroocie `dispatch_v2` rozwiązuje się do ŻYWEGO repo — test musi sprawdzać ten kod,
# który leży obok niego w worktree.
_TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "retention_archiver.py"
_spec = importlib.util.spec_from_file_location("od7_retention_archiver_under_test", _TOOL_PATH)
ra = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ra)

DAY = 86400.0


# --------------------------------------------------------------------------- #
# Fixtury                                                                      #
# --------------------------------------------------------------------------- #
def _touch(path, content=b"x\n", age_days=0.0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)
    if age_days:
        ts = time.time() - age_days * DAY
        os.utime(path, (ts, ts))
    return path


def _policy(tmp_path, **over):
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    state.mkdir(exist_ok=True)
    logs.mkdir(exist_ok=True)
    pol = {
        "policy_version": "OD-7/TEST",
        "roots": {"dispatch_state": str(state), "logs": str(logs)},
        "default_margin_days": 3,
        "sqlite_snapshot_every_days": 30,
        "classes": {
            "gps": {"live_days": 90, "archive_days": 270, "pii_mask_days": 90, "archivable": True},
            "world_record": {"live_days": 30, "archive_days": 365, "pii_mask_days": 90, "archivable": True},
            "decision_logs": {"live_days": 30, "archive_days": 365, "pii_mask_days": 90, "archivable": True},
            "events_db": {"live_days": 180, "archive_days": None, "pii_mask_days": None, "archivable": True},
            "ops_logs": {"live_days": None, "archive_days": None, "pii_mask_days": 90, "archivable": False},
            "live_state": {"live_days": None, "archive_days": None, "pii_mask_days": None, "archivable": False},
            "protected": {"live_days": None, "archive_days": None, "pii_mask_days": None, "archivable": False},
            "unknown": {"live_days": None, "archive_days": None, "pii_mask_days": None, "archivable": False},
        },
        "exclude_globs": ["**/*.lock", "**/__pycache__/**"],
        "rules": [
            {"id": "protected.corpus", "root": "dispatch_state", "globs": ["protected_*.jsonl*"],
             "class": "protected", "granularity": "any", "live_delete_owner": "brak"},
            {"id": "wr.daily", "root": "dispatch_state",
             "globs": ["world_record/world_record-*.jsonl"], "class": "world_record",
             "granularity": "sealed_dated", "date_from": "name",
             "live_delete_owner": "world_record.py:_gc",
             "competing_gc": {"deletes_after_days": 14}},
            {"id": "dec.rotated", "root": "dispatch_state",
             "globs": ["decision_eta_log.jsonl", "decision_eta_log.jsonl.*"],
             "class": "decision_logs", "granularity": "auto",
             "live_delete_owner": "logrotate", "competing_gc": {"deletes_after_days": 30}},
            {"id": "gps.history", "root": "dispatch_state", "globs": ["gps_track.jsonl*"],
             "class": "gps", "granularity": "auto", "live_delete_owner": "brak"},
            {"id": "events.db", "root": "dispatch_state", "globs": ["events.db"],
             "class": "events_db", "granularity": "sqlite_db",
             "sqlite": {"table": "audit_log", "ts_column": "ts", "ts_is_epoch": True},
             "live_delete_owner": "event_bus_cleanup"},
            {"id": "ops.text_logs", "root": "logs", "globs": ["*.log", "*.log.*"],
             "class": "ops_logs", "granularity": "auto", "live_delete_owner": "logrotate",
             "competing_gc": {"deletes_after_days": 14}},
        ],
        "pii": {
            "redact_token": "[PII-REDACTED]",
            "pseudonym_prefix": "pii_",
            "key_patterns_redact": ["adres", "address", "phone", "email", "customer_name"],
            "key_patterns_pseudonymize": ["courier_name"],
            "value_patterns": [
                {"name": "email", "regex": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"},
                {"name": "phone_pl", "regex": r"(?<![0-9])(?:\+48[ -]?)?[0-9]{3}[ -]?[0-9]{3}[ -]?[0-9]{3}(?![0-9])"},
            ],
        },
    }
    pol.update(over)
    ppath = tmp_path / "policy.json"
    ppath.write_text(json.dumps(pol, ensure_ascii=False), encoding="utf-8")
    return str(ppath), pol, str(state), str(logs)


def _run(argv):
    """Uruchom CLI i zwróć exit code (GATE jest ustawiany wewnątrz main)."""
    return ra.main(argv)


def _plan_for(tmp_path, policy_path, archive_root=None, now=None):
    pol, sha = ra.load_policy(policy_path)
    now = now or datetime.now(timezone.utc)
    errors = []
    facts = ra.scan_roots(pol, now, errors)
    manifest = ra.read_manifest(archive_root)
    plan = ra.build_plan(pol, facts, now, archive_root, manifest, set())
    return pol, sha, plan, errors


def _by_rel(plan, rel):
    for item in plan["actions"]:
        if item["rel_path"] == rel:
            return item
    raise AssertionError(f"brak pliku {rel} w planie: {[a['rel_path'] for a in plan['actions']]}")


# --------------------------------------------------------------------------- #
# 1. Polityka                                                                  #
# --------------------------------------------------------------------------- #
def test_real_policy_loads_and_is_consistent():
    """Kanoniczna polityka w repo musi się walidować i cytować liczby ownera OD-7."""
    path = os.path.join(os.path.dirname(ra.__file__), "retention_od7_policy.json")
    pol, sha = ra.load_policy(path)
    assert len(sha) == 64
    assert pol["classes"]["gps"]["live_days"] == 90
    assert pol["classes"]["gps"]["archive_days"] == 270
    assert pol["classes"]["world_record"]["live_days"] == 30
    assert pol["classes"]["world_record"]["archive_days"] == 365
    assert pol["classes"]["decision_logs"]["archive_days"] == 365
    assert pol["classes"]["events_db"]["live_days"] == 180
    assert pol["classes"]["events_db"]["archive_days"] is None      # bezterminowo
    assert pol["classes"]["ops_logs"]["pii_mask_days"] == 90
    # żadna reguła nie oddaje kasowania żywych plików temu narzędziu
    assert all((r.get("live_delete_owner") or "") != "archiver" for r in pol["rules"])


def test_policy_missing_key_is_fail_closed(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"policy_version": "x", "roots": {}, "classes": {}}), encoding="utf-8")
    with pytest.raises(ra.PolicyError):
        ra.load_policy(str(bad))


def test_policy_rule_with_unknown_class_is_rejected(tmp_path):
    ppath, pol, _, _ = _policy(tmp_path)
    pol["rules"].append({"id": "x", "root": "logs", "globs": ["*"], "class": "nope",
                         "granularity": "auto"})
    (tmp_path / "policy.json").write_text(json.dumps(pol), encoding="utf-8")
    with pytest.raises(ra.PolicyError):
        ra.load_policy(ppath)


# --------------------------------------------------------------------------- #
# 2. Klasyfikacja                                                              #
# --------------------------------------------------------------------------- #
def test_classification_maps_files_to_od7_classes(tmp_path):
    ppath, _, state, logs = _policy(tmp_path)
    _touch(f"{state}/world_record/world_record-20260101.jsonl")
    _touch(f"{state}/decision_eta_log.jsonl.1", age_days=20)
    _touch(f"{state}/gps_track.jsonl.2.gz", age_days=200)
    _touch(f"{state}/protected_corpus.jsonl", age_days=400)
    _touch(f"{state}/cos_zupelnie_innego.jsonl", age_days=400)
    _touch(f"{logs}/watcher.log.1", age_days=400)
    _touch(f"{state}/orders_state.json.lock")          # exclude
    _, _, plan, _ = _plan_for(tmp_path, ppath)

    assert _by_rel(plan, "world_record/world_record-20260101.jsonl")["cls"] == "world_record"
    assert _by_rel(plan, "decision_eta_log.jsonl.1")["cls"] == "decision_logs"
    assert _by_rel(plan, "gps_track.jsonl.2.gz")["cls"] == "gps"
    assert _by_rel(plan, "protected_corpus.jsonl")["cls"] == "protected"
    assert _by_rel(plan, "cos_zupelnie_innego.jsonl")["cls"] == "unknown"
    assert _by_rel(plan, "watcher.log.1")["cls"] == "ops_logs"
    assert all(a["rel_path"] != "orders_state.json.lock" for a in plan["actions"])


def test_granularity_dated_rotated_and_live(tmp_path):
    ppath, _, state, _ = _policy(tmp_path)
    _touch(f"{state}/world_record/world_record-20260101.jsonl")
    _touch(f"{state}/decision_eta_log.jsonl.1", age_days=20)
    _touch(f"{state}/decision_eta_log.jsonl")
    _, _, plan, _ = _plan_for(tmp_path, ppath)
    assert _by_rel(plan, "world_record/world_record-20260101.jsonl")["granularity"] == "sealed_dated"
    assert _by_rel(plan, "decision_eta_log.jsonl.1")["granularity"] == "sealed_rotated"
    assert _by_rel(plan, "decision_eta_log.jsonl")["granularity"] == "live_append"


def test_live_append_is_never_archived_and_lands_in_rotation_debt(tmp_path):
    ppath, _, state, _ = _policy(tmp_path)
    _touch(f"{state}/gps_track.jsonl", b"a" * 1000, age_days=400)
    _, _, plan, _ = _plan_for(tmp_path, ppath)
    item = _by_rel(plan, "gps_track.jsonl")
    assert item["action"] == ra.SKIP_LIVE_APPEND
    assert item["action"] not in ra.MUTATING_ACTIONS
    assert any(d["rel_path"] == "gps_track.jsonl" for d in plan["rotation_debt"])


def test_protected_and_unknown_never_get_mutating_action(tmp_path):
    ppath, _, state, _ = _policy(tmp_path)
    _touch(f"{state}/protected_corpus.jsonl.1", age_days=999)
    _touch(f"{state}/zagadka.jsonl.1", age_days=999)
    _, _, plan, _ = _plan_for(tmp_path, ppath)
    assert _by_rel(plan, "protected_corpus.jsonl.1")["action"] not in ra.MUTATING_ACTIONS
    assert _by_rel(plan, "zagadka.jsonl.1")["action"] == ra.REPORT_UNKNOWN


# --------------------------------------------------------------------------- #
# 3. Progi wieku i konflikt z istniejącym GC                                    #
# --------------------------------------------------------------------------- #
def test_archive_threshold_beats_competing_gc(tmp_path):
    """OD-7 daje 30 dni życia, ale GC kasuje po 14 → kopiujemy po 11 dniach (margines 3)."""
    ppath, pol, _, _ = _policy(tmp_path)
    p, _ = ra.load_policy(ppath)
    rule = next(r for r in p["rules"] if r["id"] == "wr.daily")
    at, conflict = ra.effective_archive_at_days(p, rule, p["classes"]["world_record"])
    assert at == 11.0
    assert conflict["od7_live_days"] == 30
    assert conflict["existing_gc_deletes_after_days"] == 14.0


def test_no_competing_gc_uses_policy_live_days(tmp_path):
    ppath, _, _, _ = _policy(tmp_path)
    p, _ = ra.load_policy(ppath)
    rule = next(r for r in p["rules"] if r["id"] == "gps.history")
    at, conflict = ra.effective_archive_at_days(p, rule, p["classes"]["gps"])
    assert at == 90.0 and conflict is None


def test_too_young_is_not_archived_and_old_enough_is(tmp_path):
    ppath, _, state, _ = _policy(tmp_path)
    now = datetime.now(timezone.utc)
    young = (now - timedelta(days=3)).strftime("%Y%m%d")
    old = (now - timedelta(days=12)).strftime("%Y%m%d")
    _touch(f"{state}/world_record/world_record-{young}.jsonl")
    _touch(f"{state}/world_record/world_record-{old}.jsonl")
    _, _, plan, _ = _plan_for(tmp_path, ppath, now=now)
    assert _by_rel(plan, f"world_record/world_record-{young}.jsonl")["action"] == ra.SKIP_TOO_YOUNG
    assert _by_rel(plan, f"world_record/world_record-{old}.jsonl")["action"] == ra.ACT_ARCHIVE


def test_dated_file_age_comes_from_name_not_mtime(tmp_path):
    """Plik dobowy sprzed 12 dni z mtime=teraz i tak jest stary (data w nazwie)."""
    ppath, _, state, _ = _policy(tmp_path)
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=12)).strftime("%Y%m%d")
    _touch(f"{state}/world_record/world_record-{old}.jsonl")  # mtime = teraz
    _, _, plan, _ = _plan_for(tmp_path, ppath, now=now)
    item = _by_rel(plan, f"world_record/world_record-{old}.jsonl")
    assert 11.0 <= item["age_days"] <= 12.0     # data z nazwy, nie mtime (=teraz)
    assert item["action"] == ra.ACT_ARCHIVE


def test_old_data_is_archived_masked(tmp_path):
    ppath, _, state, _ = _policy(tmp_path)
    _touch(f"{state}/gps_track.jsonl.1", b'{"a":1}\n', age_days=200)
    _, _, plan, _ = _plan_for(tmp_path, ppath)
    assert _by_rel(plan, "gps_track.jsonl.1")["action"] == ra.ACT_ARCHIVE_MASKED


def test_open_for_write_file_is_skipped(tmp_path):
    ppath, _, state, _ = _policy(tmp_path)
    path = _touch(f"{state}/gps_track.jsonl.1", b"x\n", age_days=200)
    pol, _ = ra.load_policy(ppath)
    now = datetime.now(timezone.utc)
    facts = ra.scan_roots(pol, now, [])
    with open(path, "ab"):
        opened = ra.open_for_write_paths()
        assert os.path.realpath(path) in opened
        plan = ra.build_plan(pol, facts, now, None, [], opened)
    assert _by_rel(plan, "gps_track.jsonl.1")["action"] == ra.SKIP_OPEN


# --------------------------------------------------------------------------- #
# 4. Bramka report vs apply                                                    #
# --------------------------------------------------------------------------- #
def test_report_mode_writes_only_to_out(tmp_path):
    ppath, _, state, logs = _policy(tmp_path)
    _touch(f"{state}/world_record/world_record-20260101.jsonl", b'{"a":1}\n')
    _touch(f"{logs}/watcher.log.1", b"ul. Testowa 5\n", age_days=200)
    before = _tree_snapshot(tmp_path)
    out = tmp_path / "raport.json"
    txt = tmp_path / "raport.md"
    rc = _run(["--policy", ppath, "--out", str(out), "--text-out", str(txt), "--quiet"])
    assert rc == 0
    after = _tree_snapshot(tmp_path)
    assert set(after) - set(before) == {str(out), str(txt)}
    assert {k: v for k, v in after.items() if k in before} == before  # nic nie zmienione
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["mode"] == "report"
    assert rep["summary"]["files_scanned"] == 2
    assert "OD-7" in txt.read_text(encoding="utf-8")


def _tree_snapshot(root):
    out = {}
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            p = os.path.join(dp, f)
            st = os.stat(p)
            out[p] = (st.st_size, st.st_mtime_ns)
    return out


def test_report_does_not_create_archive_root(tmp_path):
    ppath, _, state, _ = _policy(tmp_path)
    _touch(f"{state}/world_record/world_record-20260101.jsonl")
    arch = tmp_path / "nie_istnieje"
    rc = _run(["--policy", ppath, "--archive-root", str(arch), "--quiet"])
    assert rc == 0
    assert not arch.exists()


def test_apply_without_token_is_hard_stop(tmp_path, capsys):
    ppath, _, state, _ = _policy(tmp_path)
    _touch(f"{state}/world_record/world_record-20260101.jsonl")
    arch = tmp_path / "arch"
    arch.mkdir()
    rc = _run(["--policy", ppath, "--apply", "--archive-root", str(arch), "--quiet"])
    assert rc == 2
    assert "ACK" in capsys.readouterr().err
    assert list(arch.iterdir()) == []


def test_apply_with_wrong_token_is_hard_stop(tmp_path):
    ppath, _, state, _ = _policy(tmp_path)
    _touch(f"{state}/world_record/world_record-20260101.jsonl")
    arch = tmp_path / "arch"
    arch.mkdir()
    rc = _run(["--policy", ppath, "--apply", "--ack-token", "OD7-19700101-deadbeefcafe",
               "--archive-root", str(arch), "--quiet"])
    assert rc == 2
    assert list(arch.iterdir()) == []


def test_apply_token_is_invalidated_by_policy_change(tmp_path):
    ppath, pol, _, _ = _policy(tmp_path)
    _, sha1 = ra.load_policy(ppath)
    tok1 = ra.ack_token(sha1)
    pol["classes"]["gps"]["live_days"] = 91
    (tmp_path / "policy.json").write_text(json.dumps(pol, ensure_ascii=False), encoding="utf-8")
    _, sha2 = ra.load_policy(ppath)
    assert ra.ack_token(sha2) != tok1


def test_apply_without_existing_archive_root_is_hard_stop(tmp_path):
    ppath, _, _, _ = _policy(tmp_path)
    _, sha = ra.load_policy(ppath)
    rc = _run(["--policy", ppath, "--apply", "--ack-token", ra.ack_token(sha),
               "--archive-root", str(tmp_path / "brak"), "--quiet"])
    assert rc == 2
    assert not (tmp_path / "brak").exists()


# --------------------------------------------------------------------------- #
# 5. Archiwum: manifest, sha, odwracalność, idempotencja                        #
# --------------------------------------------------------------------------- #
def test_apply_archives_with_verified_manifest(tmp_path):
    ppath, _, state, _ = _policy(tmp_path)
    _, sha = ra.load_policy(ppath)
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=12)).strftime("%Y%m%d")
    src = _touch(f"{state}/world_record/world_record-{old}.jsonl",
                 b'{"order":1,"pool_feasible":[1,2]}\n' * 50)
    src_bytes = open(src, "rb").read()
    arch = tmp_path / "arch"
    arch.mkdir()

    rc = _run(["--policy", ppath, "--apply", "--ack-token", ra.ack_token(sha),
               "--archive-root", str(arch), "--quiet"])
    assert rc == 0
    assert os.path.exists(src), "archiwizacja NIE kasuje źródła (kasowanie ma innego ownera)"

    man = ra.read_manifest(str(arch))
    assert len(man) == 1
    rec = man[0]
    assert rec["action"] == ra.ACT_ARCHIVE
    assert rec["class"] == "world_record"
    assert rec["source_sha256"] == ra.sha256_bytes(src_bytes)
    assert rec["content_sha256"] == rec["source_sha256"]         # bez maskowania: 1:1
    assert rec["archive_sha256"] and rec["archive_size"] > 0
    assert rec["policy_sha256"] == sha
    # odwracalność: gunzip == oryginał
    with gzip.open(rec["archive_path"], "rb") as fh:
        assert fh.read() == src_bytes
    # struktura <archive-root>/<klasa>/<YYYY-MM>/...
    rel = os.path.relpath(rec["archive_path"], str(arch))
    assert rel.startswith("world_record/")
    assert rel.split("/")[1] == rec["data_month"]


def test_apply_is_idempotent(tmp_path):
    ppath, _, state, _ = _policy(tmp_path)
    _, sha = ra.load_policy(ppath)
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=12)).strftime("%Y%m%d")
    _touch(f"{state}/world_record/world_record-{old}.jsonl", b'{"a":1}\n')
    arch = tmp_path / "arch"
    arch.mkdir()
    args = ["--policy", ppath, "--apply", "--ack-token", ra.ack_token(sha),
            "--archive-root", str(arch), "--quiet"]
    assert _run(args) == 0
    assert _run(args) == 0
    man = ra.read_manifest(str(arch))
    assert len([m for m in man if m["action"] == ra.ACT_ARCHIVE]) == 1


def test_corrupt_manifest_is_fail_closed(tmp_path):
    ppath, _, state, _ = _policy(tmp_path)
    _touch(f"{state}/world_record/world_record-20260101.jsonl")
    arch = tmp_path / "arch"
    arch.mkdir()
    (arch / "MANIFEST.jsonl").write_text("{to nie jest json}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="OD7-MANIFEST"):
        ra.read_manifest(str(arch))
    rc = _run(["--policy", ppath, "--archive-root", str(arch), "--quiet"])
    assert rc == 1                                    # fail-closed, nie „lecimy dalej"


def test_archive_verification_catches_corruption(tmp_path, monkeypatch):
    """Mutacja: gdy zapis do gzipa gubi bajty, weryfikacja MUSI zaczerwienić i nie
    opublikować archiwum (żaden plik docelowy nie zostaje)."""
    src = _touch(str(tmp_path / "src.jsonl"), b"abcdefghij\n" * 100)
    dst = str(tmp_path / "arch" / "src.jsonl.gz")

    class _Lossy(gzip.GzipFile):
        def write(self, data):
            return super().write(data[:-1] if len(data) > 1 else data)

    monkeypatch.setattr(ra.gzip, "GzipFile", _Lossy)
    ra.GATE = ra.WriteGate("apply")
    ra.GATE.allow_root(str(tmp_path / "arch"))
    with pytest.raises(RuntimeError, match="OD7-ARCHIVE-VERIFY"):
        ra.gzip_copy_verified(src, dst, None)
    assert not os.path.exists(dst)
    assert not [f for f in os.listdir(os.path.dirname(dst)) if f.endswith(".tmp")]


def test_archive_expiry_deletes_only_expired_archive(tmp_path):
    ppath, _, _, _ = _policy(tmp_path)
    _, sha = ra.load_policy(ppath)
    arch = tmp_path / "arch"
    (arch / "world_record" / "2025-01" / "dispatch_state").mkdir(parents=True)
    victim = arch / "world_record" / "2025-01" / "dispatch_state" / "wr.jsonl.gz"
    victim.write_bytes(b"x")
    keeper = arch / "world_record" / "2025-01" / "dispatch_state" / "wr2.jsonl.gz"
    keeper.write_bytes(b"x")
    old_ts = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    new_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    for path, ts in ((victim, old_ts), (keeper, new_ts)):
        ra.GATE = ra.WriteGate("apply")
        ra.GATE.allow_root(str(arch))
        ra.append_manifest(str(arch), {"ts": ts, "action": ra.ACT_ARCHIVE, "class": "world_record",
                                       "root": "dispatch_state", "rel_path": os.path.basename(path),
                                       "archive_path": str(path), "archive_size": 1})
    rc = _run(["--policy", ppath, "--apply", "--ack-token", ra.ack_token(sha),
               "--archive-root", str(arch), "--quiet"])
    assert rc == 0
    assert not victim.exists(), "archiwum po terminie OD-7 (365 d) musi zniknąć"
    assert keeper.exists()


def test_events_db_archive_is_indefinite(tmp_path):
    """events.db: archiwum bezterminowe → nigdy nie planujemy wygaszenia."""
    ppath, _, _, _ = _policy(tmp_path)
    arch = tmp_path / "arch"
    arch.mkdir()
    old_ts = (datetime.now(timezone.utc) - timedelta(days=5000)).isoformat()
    ra.GATE = ra.WriteGate("apply")
    ra.GATE.allow_root(str(arch))
    ra.append_manifest(str(arch), {"ts": old_ts, "action": ra.ACT_ARCHIVE, "class": "events_db",
                                   "root": "dispatch_state", "rel_path": "events.db",
                                   "archive_path": str(arch / "events.db.gz"), "archive_size": 1})
    (arch / "events.db.gz").write_bytes(b"x")
    _, _, plan, _ = _plan_for(tmp_path, ppath, archive_root=str(arch))
    assert plan["expiries"] == []


def test_sqlite_snapshot_roundtrip(tmp_path):
    ppath, _, state, _ = _policy(tmp_path)
    _, sha = ra.load_policy(ppath)
    db = os.path.join(state, "events.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY, ts REAL)")
    conn.executemany("INSERT INTO audit_log (ts) VALUES (?)", [(time.time(),) for _ in range(20)])
    conn.commit()
    conn.close()
    arch = tmp_path / "arch"
    arch.mkdir()
    rc = _run(["--policy", ppath, "--apply", "--ack-token", ra.ack_token(sha),
               "--archive-root", str(arch), "--quiet"])
    assert rc == 0
    rec = [m for m in ra.read_manifest(str(arch)) if m["action"] == ra.ACT_SQLITE_SNAPSHOT]
    assert len(rec) == 1
    restored = tmp_path / "restored.db"
    with gzip.open(rec[0]["archive_path"], "rb") as gz, open(restored, "wb") as out:
        out.write(gz.read())
    conn = sqlite3.connect(str(restored))
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 20
    conn.close()
    # baza źródłowa nietknięta (żadnych skasowanych wierszy)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 20
    conn.close()


# --------------------------------------------------------------------------- #
# 6. Maskowanie PII                                                            #
# --------------------------------------------------------------------------- #
def _masker(tmp_path, salt=b"sol"):
    _, pol, _, _ = _policy(tmp_path)
    return ra.Masker(pol["pii"], salt)


def test_masker_redacts_keys_and_values(tmp_path):
    m = _masker(tmp_path)
    line = json.dumps({"adres": "ul. Wesoła 12/3", "customer_name": "Jan Kowalski",
                       "phone": "600100200", "order_id": 4711,
                       "notatka": "napisz na jan@example.com albo 600 100 200"},
                      ensure_ascii=False)
    out = json.loads(m.mask_line(line))
    assert out["adres"] == "[PII-REDACTED]"
    assert out["customer_name"] == "[PII-REDACTED]"
    assert out["phone"] == "[PII-REDACTED]"
    assert out["order_id"] == 4711, "dane nie-PII muszą przeżyć (inaczej korpus bezużyteczny)"
    assert "jan@example.com" not in out["notatka"]
    assert "600 100 200" not in out["notatka"]


def test_masker_never_masks_flags_or_ids(tmp_path):
    """Regresja z biegu 05.08: `ENABLE_ADDRESS_*` i `address_id` wpadały w regułę
    „address" i archiwum traciłoby telemetrię silnika. Wykluczenia mają pierwszeństwo."""
    _, pol, _, _ = _policy(tmp_path)
    pol["pii"]["key_regex_exclude"] = ["^[A-Z0-9_]{4,}$", "^enable_"]
    pol["pii"]["key_patterns_pseudonymize"] = ["courier_name", "address_id"]
    m = ra.Masker(pol["pii"], b"sol")
    out = json.loads(m.mask_line(json.dumps({
        "ENABLE_ADDRESS_COORDS_MISMATCH_SHADOW": True,
        "address_id": 8812,
        "delivery_address": "ul. Lipowa 1",
        "eta": 9,
    }, ensure_ascii=False)))
    assert out["ENABLE_ADDRESS_COORDS_MISMATCH_SHADOW"] is True
    assert out["address_id"].startswith("pii_")          # join zachowany, nie redakcja
    assert out["delivery_address"] == "[PII-REDACTED]"
    assert out["eta"] == 9


def test_real_policy_key_patterns_have_word_boundaries():
    """Regresja z biegu 05.08: podciąg `lokal` trafiał w `lokalka_zamowienia_*.csv`
    (nazwa marki, nie PII) i maskowałby telemetrię."""
    path = os.path.join(os.path.dirname(ra.__file__), "retention_od7_policy.json")
    pol, _ = ra.load_policy(path)
    m = ra.Masker(pol["pii"], b"sol")
    assert m.key_action("lokalka_zamowienia_2025-01_do_2026-06-24.csv") is None
    assert m.key_action("lokal") == "redact"
    assert m.key_action("numer_lokalu") is None or m.key_action("numer_lokalu") == "redact"
    assert m.key_action("delivery_address") == "redact"


def test_detection_flags_dict_keyed_by_pii(tmp_path):
    """Słownik kluczowany adresem/telefonem: maskowanie WARTOŚCI go nie zakrywa —
    detekcja musi to zgłosić osobno, a nazwa klucza NIE MOŻE wyciec do raportu."""
    m = _masker(tmp_path)
    p = _touch(str(tmp_path / "keyed.jsonl"),
               (json.dumps({"600100200": {"n": 1}, "address Lipowa 4": {"n": 2}}) + "\n").encode() * 2)
    det = ra.detect_pii(p, m)
    assert any(k.startswith("KEYNAME_IS_PII:") for k in det["hits"])
    blob = json.dumps(det)
    assert "600100200" not in blob and "Lipowa" not in blob
    assert "sha:" in blob


def test_real_policy_excludes_flag_keys():
    path = os.path.join(os.path.dirname(ra.__file__), "retention_od7_policy.json")
    pol, _ = ra.load_policy(path)
    m = ra.Masker(pol["pii"], b"sol")
    assert m.key_action("ENABLE_ADDRESS_COORDS_MISMATCH_SHADOW") is None
    assert m.key_action("address_id") == "pseudonymize"
    assert m.key_action("delivery_address") == "redact"
    assert m.key_action("pickup_lat") is None


def test_masker_pseudonymizes_stably_and_requires_salt(tmp_path):
    m = _masker(tmp_path)
    a = json.loads(m.mask_line(json.dumps({"courier_name": "Bartek"})))
    b = json.loads(m.mask_line(json.dumps({"courier_name": "Bartek"})))
    c = json.loads(m.mask_line(json.dumps({"courier_name": "Marek"})))
    assert a["courier_name"] == b["courier_name"] != c["courier_name"]
    assert a["courier_name"].startswith("pii_") and "Bartek" not in a["courier_name"]
    nosalt = ra.Masker(_policy(tmp_path)[1]["pii"], None)
    with pytest.raises(RuntimeError, match="OD7-PII"):
        nosalt.mask_line(json.dumps({"courier_name": "Bartek"}))


def test_masker_handles_nested_and_plaintext(tmp_path):
    m = _masker(tmp_path)
    nested = json.loads(m.mask_line(json.dumps(
        {"stops": [{"address": "ul. Lipowa 1", "eta": 12}], "meta": {"phone": "+48600100200"}})))
    assert nested["stops"][0]["address"] == "[PII-REDACTED]"
    assert nested["stops"][0]["eta"] == 12
    assert nested["meta"]["phone"] == "[PII-REDACTED]"
    plain = m.mask_line("2026-01-01 INFO kontakt: jan@example.com tel 600100200\n")
    assert "jan@example.com" not in plain and "600100200" not in plain
    assert "INFO kontakt" in plain


def test_detection_reports_counts_never_values(tmp_path):
    m = _masker(tmp_path)
    p = _touch(str(tmp_path / "sample.jsonl"),
               (json.dumps({"adres": "ul. Lipowa 1", "phone": "600100200"}) + "\n").encode() * 3)
    det = ra.detect_pii(p, m)
    assert det["hits"]["key:adres:redact"] == 3
    assert det["hits"]["value:phone_pl"] >= 3
    blob = json.dumps(det)
    assert "Lipowa" not in blob and "600100200" not in blob


def test_archive_masked_content_has_no_pii(tmp_path):
    ppath, _, state, _ = _policy(tmp_path)
    _, sha = ra.load_policy(ppath)
    payload = (json.dumps({"adres": "ul. Lipowa 1", "courier_name": "Bartek", "eta": 7},
                          ensure_ascii=False) + "\n").encode()
    src = _touch(f"{state}/gps_track.jsonl.1", payload * 5, age_days=200)
    arch = tmp_path / "arch"
    arch.mkdir()
    monkeypatched_salt = os.environ.get("OD7_PII_SALT")
    os.environ["OD7_PII_SALT"] = "sol-testowa"
    try:
        rc = _run(["--policy", ppath, "--apply", "--ack-token", ra.ack_token(sha),
                   "--archive-root", str(arch), "--quiet"])
    finally:
        if monkeypatched_salt is None:
            os.environ.pop("OD7_PII_SALT", None)
        else:
            os.environ["OD7_PII_SALT"] = monkeypatched_salt
    assert rc == 0
    rec = [m for m in ra.read_manifest(str(arch)) if m["action"] == ra.ACT_ARCHIVE_MASKED]
    assert len(rec) == 1
    with gzip.open(rec[0]["archive_path"], "rt", encoding="utf-8") as fh:
        content = fh.read()
    assert "Lipowa" not in content and "Bartek" not in content
    assert '"eta": 7' in content
    assert rec[0]["masked"] is True and rec[0]["mask_stats"]
    assert rec[0]["content_sha256"] != rec[0]["source_sha256"]
    # źródło nietknięte
    assert open(src, "rb").read() == payload * 5


def test_mask_in_place_is_atomic_and_reports_stats(tmp_path):
    p = _touch(str(tmp_path / "old.log"), b"kontakt jan@example.com\ninne\n")
    ra.GATE = ra.WriteGate("apply")
    ra.GATE.allow_root(str(tmp_path))
    rec = ra.mask_in_place(p, _masker(tmp_path))
    assert "jan@example.com" not in open(p, encoding="utf-8").read()
    assert rec["masked_sha256"] != rec["source_sha256"]
    assert rec["mask_stats"]["value:email"] == 1
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]


def test_mask_of_live_log_is_deferred_not_silently_done(tmp_path):
    """Log >3 mies. w ŻYWYM korzeniu: planowany, ale wymaga osobnego ACK ownera."""
    ppath, pol, _, _ = _policy(tmp_path)
    pol["roots"]["logs"] = ra.FORBIDDEN_WRITE_ROOTS[1]
    (tmp_path / "policy.json").write_text(json.dumps(pol, ensure_ascii=False), encoding="utf-8")
    p, _ = ra.load_policy(ppath)
    now = datetime.now(timezone.utc)
    fact = ra.FileFact(root="logs", rel_path="stary.log.1",
                       abs_path=os.path.join(ra.FORBIDDEN_WRITE_ROOTS[1], "stary.log.1"),
                       size=10, mtime=now.isoformat(), data_month="2026-01",
                       age_days=200.0, rule_id="ops.text_logs", cls="ops_logs",
                       granularity="sealed_rotated")
    plan = ra.build_plan(p, [fact], now, None, [], set())
    item = plan["actions"][0]
    assert item["action"] == ra.ACT_MASK_LIVE
    assert item["requires_separate_ack"] is True


# --------------------------------------------------------------------------- #
# 7. Bramka zapisu / fail-closed                                               #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("target", [
    "/root/.openclaw/workspace/dispatch_state/x.jsonl",
    "/root/.openclaw/workspace/dispatch_state/world_record/x.jsonl",
    "/root/.openclaw/workspace/scripts/logs/x.log",
    "/root/.openclaw/workspace/scripts/flags.json",
])
def test_write_gate_blocks_live_roots_in_every_mode(target):
    for mode in ("report", "apply"):
        gate = ra.WriteGate(mode)
        gate.allow_file(target)          # nawet jawna whitelista nie pomaga
        gate.allow_root(os.path.dirname(target))
        with pytest.raises(RuntimeError, match="OD7-WRITE-GATE"):
            gate.check(target)


def test_write_gate_blocks_paths_outside_whitelist(tmp_path):
    gate = ra.WriteGate("report")
    gate.allow_file(str(tmp_path / "raport.json"))
    gate.check(str(tmp_path / "raport.json"))
    with pytest.raises(RuntimeError, match="OD7-WRITE-GATE"):
        gate.check(str(tmp_path / "cokolwiek_innego.json"))


def test_atomic_write_leaves_no_temp_and_is_all_or_nothing(tmp_path):
    ra.GATE = ra.WriteGate("report")
    target = str(tmp_path / "r.json")
    ra.GATE.allow_file(target)
    ra.atomic_write_text(target, "{}")
    assert open(target, encoding="utf-8").read() == "{}"
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]


def test_unreadable_root_is_reported_as_error_exit_3(tmp_path):
    ppath, pol, _, _ = _policy(tmp_path)
    pol["roots"]["dispatch_state"] = str(tmp_path / "nie_ma_takiego")
    (tmp_path / "policy.json").write_text(json.dumps(pol, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "r.json"
    rc = _run(["--policy", ppath, "--out", str(out), "--quiet"])
    assert rc == 3
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["errors"] and "brak katalogu" in rep["errors"][0]["error"]


def test_source_delete_has_no_rule_and_is_fail_closed(tmp_path):
    """Gdyby ktoś dopisał SOURCE_DELETE bez reguły — apply MUSI paść, nie kasować."""
    ppath, _, state, _ = _policy(tmp_path)
    _, sha = ra.load_policy(ppath)
    arch = tmp_path / "arch"
    arch.mkdir()
    p, _ = ra.load_policy(ppath)
    victim = _touch(f"{state}/gps_track.jsonl.1", b"x\n", age_days=200)
    plan = {"actions": [{"action": ra.ACT_SOURCE_DELETE, "abs_path": victim, "cls": "gps",
                         "root": "dispatch_state", "rel_path": "gps_track.jsonl.1",
                         "data_month": "2026-01", "mtime": "x", "size": 2}],
            "expiries": []}
    ra.GATE = ra.WriteGate("apply")
    ra.GATE.allow_root(str(arch))
    with pytest.raises(RuntimeError, match="SOURCE_DELETE"):
        ra.run_apply(p, plan, str(arch), _masker(tmp_path), datetime.now(timezone.utc),
                     "rid", sha, None)
    assert os.path.exists(victim)


def test_report_proposes_archive_root_without_creating_it(tmp_path):
    ppath, pol, state, _ = _policy(tmp_path)
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=12)).strftime("%Y%m%d")
    _touch(f"{state}/world_record/world_record-{old}.jsonl", b'{"a":1}\n' * 2000)
    pol["archive_root_candidates"] = [str(tmp_path / "propozycja_archiwum")]
    (tmp_path / "policy.json").write_text(json.dumps(pol, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "r.json"
    assert _run(["--policy", ppath, "--out", str(out), "--quiet"]) == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    prop = rep["archive_root_proposal"]
    assert prop["kandydaci"][0]["path"] == str(tmp_path / "propozycja_archiwum")
    assert prop["kandydaci"][0]["exists"] is False
    assert not (tmp_path / "propozycja_archiwum").exists()
    assert prop["stan_ustalony_bytes_est"] > 0
    proj = rep["summary"]["archive_projection"]["per_class"]["world_record"]
    assert proj["archive_days"] == 365
    assert proj["steady_state_bytes_est"] >= proj["bytes_per_day_est"]


def test_report_includes_policy_conflicts_and_space_balance(tmp_path):
    ppath, _, state, _ = _policy(tmp_path)
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=12)).strftime("%Y%m%d")
    _touch(f"{state}/world_record/world_record-{old}.jsonl", b'{"a":1}\n' * 500)
    out = tmp_path / "r.json"
    assert _run(["--policy", ppath, "--out", str(out), "--quiet"]) == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert any(c["rule_id"] == "wr.daily" for c in rep["policy_conflicts"])
    bal = rep["summary"]["space_balance"]
    assert bal["freed_live_bytes"] == 0, "archiwizacja sama nie zwalnia żywego dysku"
    assert bal["archive_growth_bytes_est"] > 0
    assert rep["summary"]["archive_now"]["bytes_archive_est"] <= rep["summary"]["archive_now"]["bytes_live"]


# --------------------------------------------------------------------------- #
# 8. ITER2 — defekty z blind-297 (F1 sqlite/WAL · F2 ucięty gzip · F3 lock ·    #
#    F4 glob przez granice katalogów). Każdy test jest negatywnym oraclem:      #
#    po cofnięciu poprawki MUSI zaczerwienić.                                   #
# --------------------------------------------------------------------------- #
def _wal_db(path, rows=20, keep_open=False):
    """Baza w trybie WAL. keep_open=True → dane zostają w niescheckpointowanym `-wal`
    (dokładnie jak żywy events.db pod pracującym silnikiem)."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY, ts REAL)")
    conn.executemany("INSERT INTO audit_log (ts) VALUES (?)", [(float(i),) for i in range(rows)])
    conn.commit()
    if keep_open:
        return conn
    conn.close()
    return None


# --- F1: żadna ścieżka nie tworzy plików obok żywej bazy -------------------- #
def test_report_with_include_sqlite_writes_only_to_out(tmp_path):
    """Oracle recenzenta (blind-297 F1): `mode=ro` materializuje `-wal`/`-shm` obok
    SKANOWANEJ bazy — zapis do żywego korzenia, którego WriteGate nie widzi, bo robi
    go biblioteka sqlite3, nie ten moduł."""
    ppath, _, state, _ = _policy(tmp_path)
    _touch(f"{state}/world_record/world_record-20260101.jsonl", b'{"a":1}\n')
    _wal_db(f"{state}/events.db")
    assert sorted(os.listdir(state)) == ["events.db", "world_record"], "baza domknięta czysto"

    before = _tree_snapshot(tmp_path)
    out = tmp_path / "raport.json"
    rc = _run(["--policy", ppath, "--include-sqlite", "--out", str(out), "--quiet"])
    assert rc == 0
    created = set(_tree_snapshot(tmp_path)) - set(before)
    assert created == {str(out)}, (
        "REPORT stworzył pliki poza --out w SKANOWANYM korzeniu: "
        + repr(sorted(os.path.basename(p) for p in created - {str(out)})))
    stats = _by_rel(json.loads(out.read_text(encoding="utf-8")), "events.db")["sqlite_stats"]
    assert stats["read_mode"] == "immutable"
    assert stats["rows_total"] == 20, "odczyt immutable nadal daje prawdziwe liczby"


def test_apply_snapshot_of_cleanly_closed_wal_db_creates_no_sidecars(tmp_path):
    """Ścieżka APPLY, przypadek z repro recenzenta: baza WAL domknięta czysto (na dysku
    SAM plik bazy). Otwarcie jej `mode=ro` DOKŁADA `-wal`/`-shm` do skanowanego korzenia
    i zostawia je po zamknięciu — tu musi nie przybyć ani jeden bajt.

    (Gdy pliki towarzyszące już istnieją, bo silnik trzyma bazę otwartą, naruszenia nie
    da się zaobserwować z zewnątrz — dlatego oraclem jest właśnie stan czysto domknięty.)"""
    ppath, _, state, _ = _policy(tmp_path)
    _, sha = ra.load_policy(ppath)
    db = os.path.join(state, "events.db")
    _wal_db(db)
    assert sorted(os.listdir(state)) == ["events.db"]
    arch = tmp_path / "arch"
    arch.mkdir()

    before = _tree_snapshot(state)
    rc = _run(["--policy", ppath, "--apply", "--ack-token", ra.ack_token(sha),
               "--archive-root", str(arch), "--quiet"])
    assert rc == 0
    after = _tree_snapshot(state)
    assert set(after) - set(before) == set(), (
        "APPLY dołożył pliki obok żywej bazy: "
        + repr(sorted(os.path.basename(p) for p in set(after) - set(before))))
    assert after == before, "żywy korzeń nietknięty (rozmiar i mtime bez zmian)"

    rec = [m for m in ra.read_manifest(str(arch)) if m["action"] == ra.ACT_SQLITE_SNAPSHOT]
    restored = tmp_path / "restored.db"
    with gzip.open(rec[0]["archive_path"], "rb") as gz:
        restored.write_bytes(gz.read())
    conn = sqlite3.connect(str(restored))
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 20
    conn.close()


def test_apply_snapshot_creates_nothing_next_to_live_db_and_captures_wal(tmp_path):
    """Ścieżka APPLY (bez żadnej flagi opt-in): snapshot pracuje na KOPII w archiwum,
    więc obok żywej bazy nie przybywa nic — a dane z `-wal` i tak trafiają do archiwum."""
    ppath, _, state, _ = _policy(tmp_path)
    _, sha = ra.load_policy(ppath)
    db = os.path.join(state, "events.db")
    live_writer = _wal_db(db, rows=20, keep_open=True)      # żywy pisarz trzyma WAL
    try:
        assert os.path.exists(db + "-wal")
        before = sorted(os.listdir(state))
        db_sha_before = ra.sha256_file(db)
        arch = tmp_path / "arch"
        arch.mkdir()

        rc = _run(["--policy", ppath, "--apply", "--ack-token", ra.ack_token(sha),
                   "--archive-root", str(arch), "--quiet"])
        assert rc == 0
        assert sorted(os.listdir(state)) == before, "obok żywej bazy NIE MOŻE nic przybyć"
        assert ra.sha256_file(db) == db_sha_before, "żywa baza nietknięta bajt w bajt"

        rec = [m for m in ra.read_manifest(str(arch)) if m["action"] == ra.ACT_SQLITE_SNAPSHOT]
        assert len(rec) == 1
        restored = tmp_path / "restored.db"
        with gzip.open(rec[0]["archive_path"], "rb") as gz:
            restored.write_bytes(gz.read())
        conn = sqlite3.connect(str(restored))
        assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 20, \
            "snapshot MUSI zawierać dane z niescheckpointowanego -wal"
        conn.close()
    finally:
        live_writer.close()
    leftovers = [f for f in os.listdir(tmp_path / "arch")
                 if f.startswith(".od7_") and f != ra.APPLY_LOCK_NAME]
    assert leftovers == [], f"żadnych plików roboczych po snapshotcie, zostało: {leftovers}"


def test_sidecar_guard_reddens_if_readonly_open_regresses_to_mode_ro(tmp_path, monkeypatch):
    """RATCHET: gdyby ktoś wrócił do `mode=ro` (albo innego trybu tworzącego `-wal`),
    bezpiecznik ma paść GŁOŚNO, a nie cicho zaśmiecić skanowany korzeń."""
    ppath, pol, state, _ = _policy(tmp_path)
    db = os.path.join(state, "events.db")
    _wal_db(db)
    p, _ = ra.load_policy(ppath)
    rule = next(r for r in p["rules"] if r["id"] == "events.db")

    monkeypatch.setattr(ra, "sqlite_connect_readonly",
                        lambda path: sqlite3.connect(f"file:{path}?mode=ro", uri=True))
    with pytest.raises(RuntimeError, match="OD7-SQLITE-SIDECAR"):
        ra.sqlite_age_stats(db, rule, p, datetime.now(timezone.utc), [])


def test_sqlite_stats_report_uncheckpointed_wal_instead_of_silently_stale_numbers(tmp_path):
    """Cena `immutable=1` jest jawna: dane z `-wal` są niewidoczne, więc raport mówi to
    wprost (`wal_pending` + errors[] + exit 3), zamiast podawać niepełne liczby jako pewne."""
    ppath, _, state, _ = _policy(tmp_path)
    live_writer = _wal_db(os.path.join(state, "events.db"), rows=5, keep_open=True)
    try:
        before = sorted(os.listdir(state))
        out = tmp_path / "r.json"
        rc = _run(["--policy", ppath, "--include-sqlite", "--out", str(out), "--quiet"])
        assert rc == 3, "niepewne liczby = wpis w errors[], a raport i tak powstaje"
        assert sorted(os.listdir(state)) == before
        rep = json.loads(out.read_text(encoding="utf-8"))
        assert _by_rel(rep, "events.db")["sqlite_stats"]["wal_pending"] is True
        assert any("wal" in e["error"] for e in rep["errors"])
    finally:
        live_writer.close()


# --- F2: uszkodzony strumień = błąd jednego pliku, nie koniec biegu --------- #
_PII_LINES = b"".join(
    ('{"adres":"ul. Testowa %d/%d","phone":"6%08d","nota":"k%d@example.com"}\n'
     % (i, i % 90, i, i)).encode("utf-8") for i in range(5000))


def _half_gzip(path, payload=_PII_LINES, age_days=200.0, mode="truncate"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wb") as fh:
        fh.write(payload)
    raw = Path(path).read_bytes()
    if mode == "truncate":                       # logrotate złapany w trakcie kompresji
        broken = raw[: len(raw) // 2]
    else:                                        # zepsuty środek strumienia deflate
        buf = bytearray(raw)
        buf[40:80] = b"\x00" * 40
        broken = bytes(buf)
    Path(path).write_bytes(broken)
    ts = time.time() - age_days * DAY
    os.utime(path, (ts, ts))
    return path


@pytest.mark.parametrize("mode", ["truncate", "corrupt"])
def test_detect_pii_survives_broken_gzip_and_keeps_partial_evidence(tmp_path, mode):
    """EOFError (ucięty ogon) i zlib.error (zepsuty środek) NIE są podklasami OSError —
    stary `except OSError` ich nie łapał i wyjątek leciał aż do końca biegu."""
    p = _half_gzip(str(tmp_path / "gps_track.jsonl.1.gz"), mode=mode)
    det = ra.detect_pii(p, _masker(tmp_path))
    assert det.get("error"), "uszkodzenie MUSI być zaraportowane, nie połknięte"
    if mode == "truncate":
        assert det["hits"], "to, co dało się przeczytać, zostaje dowodem w raporcie"


def test_truncated_gzip_does_not_kill_report(tmp_path):
    """Kontrakt modułu: w REPORCIE błąd pliku → errors[] → exit 3, a raport POWSTAJE."""
    ppath, _, state, _ = _policy(tmp_path)
    _half_gzip(f"{state}/gps_track.jsonl.1.gz")
    _touch(f"{state}/world_record/world_record-20260101.jsonl", b'{"a":1}\n')
    out = tmp_path / "r.json"
    rc = _run(["--policy", ppath, "--out", str(out), "--quiet"])
    assert rc == 3, "błąd pojedynczego pliku nie może dać exit 1 bez raportu"
    assert out.exists(), "raport jest dowodem dla ownera — musi powstać"
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert any("pii-scan" in e["error"] for e in rep["errors"])
    assert _by_rel(rep, "gps_track.jsonl.1.gz")["action"] in (ra.ACT_ARCHIVE, ra.ACT_ARCHIVE_MASKED)


# --- F3: wyłączność APPLY --------------------------------------------------- #
def test_second_apply_is_refused_while_lock_is_held(tmp_path):
    """Idempotencja opiera się na manifeście czytanym RAZ na starcie — bez wykluczenia
    wzajemnego dwa biegi planują ten sam zbiór akcji."""
    ppath, _, state, _ = _policy(tmp_path)
    _, sha = ra.load_policy(ppath)
    old = (datetime.now(timezone.utc) - timedelta(days=12)).strftime("%Y%m%d")
    _touch(f"{state}/world_record/world_record-{old}.jsonl", b'{"a":1}\n')
    arch = tmp_path / "arch"
    arch.mkdir()

    ra.GATE = ra.WriteGate("apply")
    ra.GATE.allow_root(str(arch))
    held = ra.acquire_apply_lock(str(arch))          # udajemy trwający bieg
    try:
        rc = _run(["--policy", ppath, "--apply", "--ack-token", ra.ack_token(sha),
                   "--archive-root", str(arch), "--quiet"])
        assert rc == 4, "drugi bieg MUSI odmówić, nie dublować pracy"
        assert ra.read_manifest(str(arch)) == [], "odmowa = zero wpisów w manifeście"
    finally:
        os.close(held)
    assert os.path.exists(arch / ra.APPLY_LOCK_NAME), "lock leży w ARCHIWUM, nie w żywym korzeniu"
    # po zwolnieniu blokady bieg przechodzi normalnie
    assert _run(["--policy", ppath, "--apply", "--ack-token", ra.ack_token(sha),
                 "--archive-root", str(arch), "--quiet"]) == 0
    assert len([m for m in ra.read_manifest(str(arch)) if m["action"] == ra.ACT_ARCHIVE]) == 1


def test_two_parallel_apply_processes_do_not_duplicate_manifest(tmp_path):
    """Repro recenzenta 1:1 (dwa procesy naraz) — wcześniej: 2× praca, 2× manifest."""
    ppath, _, state, _ = _policy(tmp_path)
    _, sha = ra.load_policy(ppath)
    day = datetime.now(timezone.utc) - timedelta(days=40)
    for i in range(12):
        stamp = (day + timedelta(days=i)).strftime("%Y%m%d")
        _touch(f"{state}/world_record/world_record-{stamp}.jsonl", b'{"a":1}\n' * 200)
    arch = tmp_path / "arch"
    arch.mkdir()

    cmd = [sys.executable, "-B", str(_TOOL_PATH), "--policy", ppath, "--apply",
           "--ack-token", ra.ack_token(sha), "--archive-root", str(arch), "--quiet"]
    procs = [subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
             for _ in range(2)]
    rcs = sorted(p.wait() for p in procs)
    for p in procs:
        p.stderr.close()

    man = [m for m in ra.read_manifest(str(arch)) if m["action"] == ra.ACT_ARCHIVE]
    keys = [(m["root"], m["rel_path"]) for m in man]
    assert len(keys) == len(set(keys)) == 12, f"manifest zdublowany: {len(keys)} wpisów"
    assert rcs[0] == 0, "jeden bieg MUSI wykonać pracę"
    assert rcs[1] in (0, 4), f"drugi bieg: odmowa (4) albo czysty SKIP (0), było {rcs}"
    assert len({m["run_id"] for m in man}) == 1, "całą paczkę archiwizuje JEDEN bieg"


# --- F4: '*' nie przekracza granicy katalogu -------------------------------- #
def test_star_does_not_cross_directory_boundary_in_real_policy():
    """Reguły OD-7 są pisane pod PŁASKIE pliki dobowe; `fnmatch` na całej ścieżce robił
    z nich reguły rekurencyjne (dowolnie zagnieżdżony plik wpadał w klasę archiwizowalną)."""
    pol, _ = ra.load_policy(os.path.join(os.path.dirname(ra.__file__),
                                         "retention_od7_policy.json"))
    assert ra.match_rule(pol, "dispatch_state", "world_record/world_record-x/DEEP/leak.jsonl") is None
    assert ra.match_rule(pol, "dispatch_state", "observability/candidate_decisions_a/b/c.jsonl") is None
    assert ra.match_rule(pol, "logs", "reports/sub/dir/anything.bin") is None
    # …a płaskie trafienia działają dalej
    assert ra.match_rule(pol, "dispatch_state",
                         "world_record/world_record-20260101.jsonl")["id"] == "wr.daily"
    assert ra.match_rule(pol, "dispatch_state",
                         "observability/candidate_decisions_20260101.jsonl")["id"] == "dec.observability_daily"
    assert ra.match_rule(pol, "logs", "reports/dzienny.txt")["id"] == "ops.text_logs"
    assert ra.match_rule(pol, "logs", "watcher.log.1")["id"] == "ops.text_logs"


def test_deep_file_under_rule_prefix_is_unknown_not_archivable(tmp_path):
    ppath, _, state, _ = _policy(tmp_path)
    _touch(f"{state}/world_record/world_record-x/DEEP/leak.jsonl", b'{"a":1}\n', age_days=200)
    _, _, plan, _ = _plan_for(tmp_path, ppath)
    item = _by_rel(plan, "world_record/world_record-x/DEEP/leak.jsonl")
    assert item["cls"] == "unknown"
    assert item["action"] == ra.REPORT_UNKNOWN
    assert item["action"] not in ra.MUTATING_ACTIONS


def test_double_star_is_the_only_recursive_marker():
    assert ra._glob_match("a/b/c.lock", "**/*.lock")
    assert ra._glob_match("c.lock", "**/*.lock")
    assert ra._glob_match("backups/a/b.jsonl", "backups/**")
    assert not ra._glob_match("nie_backups/a.jsonl", "backups/**")
    assert ra._glob_match("x/__pycache__/y.pyc", "**/__pycache__/**")
    assert not ra._glob_match("a/b/c.jsonl", "a/*.jsonl")
    assert ra._glob_match("a/c.jsonl", "a/*.jsonl")
    assert not ra._glob_match("pod/plik.log", "*.log"), "wzorzec bez '/' = tylko korzeń"
