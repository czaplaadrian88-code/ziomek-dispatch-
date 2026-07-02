"""AUDYT 2.0 Blocker-2 (lane auton-blockers) — TOCTOU + idempotencja + dry-first
+ sentinel w auto_assign_executor.

Wszystko ZA `ENABLE_AUTO_ASSIGN` (OFF live → inert). Testy behawioralne (C13):
  - TOCTOU: flaga flip→OFF w trakcie I/O rate-cap/cooldown → wykonanie ANULOWANE;
  - dry-first: pierwszy tick po zmianie flags.json → handshake, ZERO wykonania;
  - idempotencja: ten sam oid nie wykona się 2× (reconcile-lag 15-90 s + 2. event);
  - sentinel: runner ufa ASSIGN_OK: w stdout, nie samemu exit-code.
Mutacje ×2 odwracają guard i wymagają, by wynik się ZEPSUŁ.
"""
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from dispatch_v2 import common as C

# C12(e): auto_assign_executor.py żyje W dispatch_v2, ale konftest pinuje pakiet
# na KANON (/root/.openclaw/workspace/scripts) → `import dispatch_v2.auto_assign_executor`
# NIE widzi edycji worktree. Ładujemy WORKTREE kopię PO ŚCIEŻCE (jej `from dispatch_v2
# import common` i tak rozwiązuje się do współdzielonego kanonu — common poza tym pasem).
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXEC = os.path.join(_REPO, "auto_assign_executor.py")


def _load_worktree_executor():
    spec = importlib.util.spec_from_file_location("auto_assign_executor_wt", _EXEC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["auto_assign_executor_wt"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop("auto_assign_executor_wt", None)
        raise
    return mod


E = _load_worktree_executor()

NOW = datetime(2026, 7, 2, 3, 0, tzinfo=timezone.utc)


def _record(oid="480300", cid="101", name="Kurier Testowy", target_min=12):
    tgt = (NOW + timedelta(minutes=target_min)).isoformat()
    return {"verdict": "PROPOSE", "order_id": oid,
            "best": {"courier_id": cid, "name": name, "score": 55.0, "target_pickup_at": tgt}}


def _result(would=True):
    return SimpleNamespace(would_auto_assign=would)


@pytest.fixture
def runner_spy():
    calls = []
    def runner(oid, name, minutes):
        calls.append((oid, name, minutes))
        return True, "ASSIGN_OK: done"
    runner.calls = calls
    return runner


@pytest.fixture
def notify_spy():
    msgs = []
    def notify(text):
        msgs.append(text)
    notify.msgs = msgs
    return notify


@pytest.fixture
def state_path(tmp_path):
    return str(tmp_path / "auto_assign_state.json")


@pytest.fixture
def isolated_llog(tmp_path, monkeypatch):
    p = tmp_path / "learning_log.jsonl"
    p.write_text("")
    monkeypatch.setattr(E, "LEARNING_LOG_PATH", str(p))
    return p


@pytest.fixture
def flag_always_on(monkeypatch):
    monkeypatch.setattr(C, "decision_flag", lambda name, *a, **k: True)


# ══════════════════════════════════════════════════════════════════════════
# KONTRAKTY ZACHOWANE (worktree E) — moje guardy nie łamią starego zachowania
# (existing test_auto_assign_executor.py testuje KANON; tu weryfikuję WORKTREE).
# ══════════════════════════════════════════════════════════════════════════
def test_preserved_flag_off_returns_none(monkeypatch, runner_spy, notify_spy,
                                          state_path, isolated_llog):
    monkeypatch.setattr(C, "decision_flag", lambda name, *a, **k: False)
    out = E.maybe_execute(_record(), _result(), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert out is None and runner_spy.calls == []
    assert not os.path.exists(state_path)     # OFF = zero-I/O (kontrakt niezmieniony)


def test_preserved_happy_path_executes(flag_always_on, runner_spy, notify_spy,
                                       state_path, isolated_llog):
    out = E.maybe_execute(_record(target_min=12), _result(), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert out["executed"] is True
    assert runner_spy.calls == [("480300", "Kurier Testowy", 12)]


def test_preserved_rate_cap_blocks(flag_always_on, runner_spy, notify_spy,
                                    isolated_llog, monkeypatch, tmp_path):
    monkeypatch.setattr(C, "AUTO_ASSIGN_MAX_PER_HOUR", 2)
    sp = str(tmp_path / "state.json")
    with open(sp, "w") as f:
        json.dump({"executed": [NOW.timestamp() - 100, NOW.timestamp() - 200]}, f)
    out = E.maybe_execute(_record(), _result(), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy, state_path=sp)
    assert out == {"blocked": "rate_cap"} and runner_spy.calls == []


# ══════════════════════════════════════════════════════════════════════════
# TOCTOU — atomowy re-check flagi TUŻ przed wykonaniem
# ══════════════════════════════════════════════════════════════════════════
def test_toctou_flag_flips_off_during_io_aborts_execution(monkeypatch, runner_spy,
                                                          notify_spy, state_path, isolated_llog):
    # decision_flag: True na wejściu, False przy re-checku (4b) → flip w oknie I/O.
    seen = {"n": 0}
    def flip(name, *a, **k):
        seen["n"] += 1
        return seen["n"] <= 1
    monkeypatch.setattr(C, "decision_flag", flip)
    out = E.maybe_execute(_record(), _result(), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert out == {"blocked": "flag_off_at_execution", "order_id": "480300"}
    assert runner_spy.calls == []            # NIE wykonano mimo would_auto=True


def test_toctou_flag_stays_on_executes(flag_always_on, runner_spy, notify_spy,
                                       state_path, isolated_llog):
    # Kontrast (mutacja „brak re-checku" = flaga zawsze True): re-check przepuszcza → exec.
    out = E.maybe_execute(_record(), _result(), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert out["executed"] is True and runner_spy.calls != []


# ══════════════════════════════════════════════════════════════════════════
# DRY-FIRST — pierwszy tick po zmianie flags.json = handshake, ZERO wykonania
# ══════════════════════════════════════════════════════════════════════════
def test_dry_first_blocks_when_flags_recently_changed(monkeypatch, flag_always_on,
                                                      runner_spy, notify_spy,
                                                      state_path, isolated_llog):
    monkeypatch.setattr(E, "_flags_recently_changed", lambda now_ts, arm: True)
    out = E.maybe_execute(_record(), _result(), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert out == {"blocked": "dry_first_handshake", "order_id": "480300"}
    assert runner_spy.calls == []


def test_dry_first_passes_after_arm_window(monkeypatch, flag_always_on, runner_spy,
                                           notify_spy, state_path, isolated_llog):
    monkeypatch.setattr(E, "_flags_recently_changed", lambda now_ts, arm: False)
    out = E.maybe_execute(_record(), _result(), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy, state_path=state_path)
    assert out["executed"] is True


def test_flags_recently_changed_unit_uses_mtime(tmp_path, monkeypatch):
    # Jednostkowo: świeży plik → True (z allow-env); stary mtime → False.
    monkeypatch.setenv("ALLOW_AUTO_ASSIGN_DRYFIRST_IN_TEST", "1")
    f = tmp_path / "flags.json"
    f.write_text("{}")
    monkeypatch.setattr(C, "FLAGS_PATH", str(f))
    now_ts = os.path.getmtime(str(f)) + 5      # 5 s po zapisie
    assert E._flags_recently_changed(now_ts, 45.0) is True
    assert E._flags_recently_changed(os.path.getmtime(str(f)) + 999, 45.0) is False


def test_flags_recently_changed_suppressed_under_pytest_by_default():
    # Bez allow-env pod pytest → False (istniejące testy nie zależą od mtime współdzielonego pliku).
    assert E._flags_recently_changed(1e18, 45.0) is False


# ══════════════════════════════════════════════════════════════════════════
# IDEMPOTENCJA per-order — ten sam oid nie wykona się 2×
# ══════════════════════════════════════════════════════════════════════════
def test_idempotent_blocks_recent_same_order(flag_always_on, runner_spy, notify_spy,
                                             isolated_llog, tmp_path):
    sp = str(tmp_path / "state.json")
    with open(sp, "w") as f:
        json.dump({"executed": [], "assigned_orders": {"480300": NOW.timestamp() - 100}}, f)
    out = E.maybe_execute(_record(oid="480300"), _result(), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy, state_path=sp)
    assert out == {"blocked": "idempotent_recent", "order_id": "480300"}
    assert runner_spy.calls == []


def test_idempotent_allows_expired_entry(flag_always_on, runner_spy, notify_spy,
                                         isolated_llog, tmp_path):
    sp = str(tmp_path / "state.json")
    with open(sp, "w") as f:
        json.dump({"executed": [], "assigned_orders": {"480300": NOW.timestamp() - 5000}}, f)
    out = E.maybe_execute(_record(oid="480300"), _result(), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy, state_path=sp)
    assert out["executed"] is True


def test_idempotent_records_oid_on_success(monkeypatch, flag_always_on, runner_spy,
                                           notify_spy, isolated_llog, tmp_path):
    monkeypatch.setenv("ALLOW_AUTO_ASSIGN_STATE_IN_TEST", "1")
    sp = str(tmp_path / "state.json")
    E.maybe_execute(_record(oid="480777"), _result(), {}, now=NOW,
                    assign_runner=runner_spy, notifier=notify_spy, state_path=sp)
    saved = json.load(open(sp))
    assert "480777" in saved.get("assigned_orders", {})


def test_record_auto_assign_prunes_expired():
    st = {"assigned_orders": {"old": 1.0, "keep": 999.0}}
    E._record_auto_assign(st, "new", 1000.0, ttl_sec=900.0)
    ao = st["assigned_orders"]
    assert "new" in ao and "keep" in ao and "old" not in ao   # 1000-1<900 keep, 1000-1.0>900 prune old


# ══════════════════════════════════════════════════════════════════════════
# SENTINEL — runner ufa ASSIGN_OK:, nie samemu exit-code (Blocker-1 strona executora)
# ══════════════════════════════════════════════════════════════════════════
def _fake_run(returncode, stdout="", stderr=""):
    return lambda *a, **k: SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_runner_requires_sentinel_even_on_exit0(monkeypatch):
    monkeypatch.setenv("ALLOW_AUTO_ASSIGN_SUBPROCESS_IN_TEST", "1")
    monkeypatch.setattr(E.subprocess, "run", _fake_run(0, stdout="[assign] Odpowiedź panelu: {'raw': ''}"))
    ok, msg = E._default_assign_runner("1", "Kurier", 5)
    assert ok is False and "no_confirm" in msg     # exit 0 bez sentinela = PORAŻKA


def test_runner_accepts_exit0_with_sentinel(monkeypatch):
    monkeypatch.setenv("ALLOW_AUTO_ASSIGN_SUBPROCESS_IN_TEST", "1")
    monkeypatch.setattr(E.subprocess, "run", _fake_run(0, stdout="ASSIGN_OK: Bartek → 480300"))
    ok, msg = E._default_assign_runner("1", "Kurier", 5)
    assert ok is True and "ASSIGN_OK" in msg


def test_runner_nonzero_exit_is_failure(monkeypatch):
    monkeypatch.setenv("ALLOW_AUTO_ASSIGN_SUBPROCESS_IN_TEST", "1")
    monkeypatch.setattr(E.subprocess, "run", _fake_run(1, stderr="ASSIGN_ERROR: session_bounce"))
    ok, msg = E._default_assign_runner("1", "Kurier", 5)
    assert ok is False and "exit=1" in msg


def test_runner_passes_verify_flag(monkeypatch):
    monkeypatch.setenv("ALLOW_AUTO_ASSIGN_SUBPROCESS_IN_TEST", "1")
    captured = {}
    def _cap(cmd, **k):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="ASSIGN_OK: x", stderr="")
    monkeypatch.setattr(E.subprocess, "run", _cap)
    E._default_assign_runner("1", "Kurier", 5)
    assert "--verify" in captured["cmd"]


# ══════════════════════════════════════════════════════════════════════════
# MUTATION ×2 — odwróć guard, wynik MUSI się zepsuć
# ══════════════════════════════════════════════════════════════════════════
def test_mutation_remove_idempotency_allows_double_assign(monkeypatch, flag_always_on,
                                                          runner_spy, notify_spy,
                                                          isolated_llog, tmp_path):
    sp = str(tmp_path / "state.json")
    with open(sp, "w") as f:
        json.dump({"executed": [], "assigned_orders": {"480300": NOW.timestamp() - 100}}, f)
    # Prawda: guard blokuje.
    real = E.maybe_execute(_record(oid="480300"), _result(), {}, now=NOW,
                           assign_runner=runner_spy, notifier=notify_spy, state_path=sp)
    assert real.get("blocked") == "idempotent_recent"
    # MUTACJA: guard usunięty (zawsze „nie widziany") → wykonuje 2. raz.
    monkeypatch.setattr(E, "_recent_auto_assign", lambda *a, **k: False)
    mut = E.maybe_execute(_record(oid="480300"), _result(), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy, state_path=sp)
    assert mut.get("executed") is True and mut != real


def test_mutation_empty_sentinel_reintroduces_false_success(monkeypatch):
    monkeypatch.setenv("ALLOW_AUTO_ASSIGN_SUBPROCESS_IN_TEST", "1")
    monkeypatch.setattr(E.subprocess, "run", _fake_run(0, stdout="jakiś śmieciowy output bez OK"))
    # Prawda: brak sentinela → False.
    real_ok, _ = E._default_assign_runner("1", "Kurier", 5)
    assert real_ok is False
    # MUTACJA: sentinel = "" (jest w każdym stringu) → fałszywy sukces wraca.
    monkeypatch.setattr(E, "ASSIGN_OK_SENTINEL", "")
    mut_ok, _ = E._default_assign_runner("1", "Kurier", 5)
    assert mut_ok is True and mut_ok != real_ok
