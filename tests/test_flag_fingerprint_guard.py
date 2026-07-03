"""Testy behawioralne flag_fingerprint_guard (STRAŻNIK-timer, READ-ONLY).

Hermetyczne: reconcile/journal/log/telegram mockowane, pliki stanu i JSONL ZAWSZE
pod tmp_path (assert ANTY-PROD — mina 02.07: 60 fejków w żywym pending). Pokrywa:
  (a) realny DRIFT → wpis DRIFT + flaga ON edge-alert RAZ / OFF zero wysyłki,
  (b) cold w logu ale NIEPOTWIERDZONY journalem → UNVERIFIED, zero alertu (anty-§20),
  (c) potwierdzony journalem COLD → alert,
  (d) recovery DRIFT→OK → jeden alert powrotu,
  (e) mutation-probe ×2: edge-dedup i tolerancja okna §20 mają zęby.
"""
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

UTC = timezone.utc

_HERE = os.path.dirname(os.path.abspath(__file__))
_GUARD = os.path.join(os.path.dirname(_HERE), "tools", "flag_fingerprint_guard.py")

# Ładujemy WORKTREE-ową kopię strażnika po ścieżce (nie zainstalowaną w kanonie).
_spec = importlib.util.spec_from_file_location("flag_fingerprint_guard_wt", _GUARD)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)
ffc = guard.ffc


# ── helpers ──────────────────────────────────────────────────────────────────
def _wire_tmp(monkeypatch, tmp_path):
    """Wszystkie zapisy pod tmp + assert że NIE dotykamy PROD."""
    prod_log, prod_state = guard.GUARD_LOG, guard.STATE_PATH
    glog = str(tmp_path / "guard.jsonl")
    gstate = str(tmp_path / "state.json")
    gallow = str(tmp_path / "allow.json")
    assert glog != prod_log and gstate != prod_state
    assert str(tmp_path) not in ("/root/.openclaw/workspace/dispatch_state",)
    monkeypatch.setattr(guard, "GUARD_LOG", glog)
    monkeypatch.setattr(guard, "STATE_PATH", gstate)
    monkeypatch.setattr(guard, "ALLOWLIST_PATH", gallow)
    return glog, gstate


def _fake_res(findings, procs_live=("shadow",)):
    return {"procs_live": list(procs_live), "procs_dead": [],
            "fingerprint_sizes": {p: 100 for p in procs_live}, "findings": findings}


def _capture_telegram(monkeypatch):
    sent = []
    monkeypatch.setattr("dispatch_v2.telegram_utils.send_admin_alert",
                        lambda msg, **kw: (sent.append(msg), True)[1])
    return sent


# 20 flag: fingerprint =0, flags.json chce =1 → drift 20 ≥ COLD_DRIFT_MIN(15) = cold.
_FLAGS20 = [f"ENABLE_TESTFLAG_{i}" for i in range(20)]


def _cold_log_line(ts: datetime) -> str:
    body = " ".join(f"{f}=0" for f in _FLAGS20)
    return (f"{ts.strftime('%Y-%m-%d %H:%M:%S')} [INFO] czasowka_scheduler: "
            f"FLAG_FINGERPRINT proc=czasowka {body}\n")


def _journal_run(start: datetime, end: datetime) -> str:
    return (f"{start.astimezone(UTC).isoformat()} Ziomek systemd[1]: "
            f"Starting dispatch-czasowka.service - Ziomek Czasowka...\n"
            f"{end.astimezone(UTC).isoformat()} Ziomek systemd[1]: "
            f"dispatch-czasowka.service: Deactivated successfully.\n")


def _wire_cold(monkeypatch, cold_ts, journal_text):
    """Reconcile zwraca INTERMITTENT-COLD; log ma cold-linię; journal jak podano."""
    finding = {"klass": "INTERMITTENT-COLD", "flag": "proc:czasowka",
               "detail": {"cold_recent": 12}, "who_wins": "..."}
    monkeypatch.setattr(ffc, "reconcile",
                        lambda: _fake_res([finding], procs_live=("czasowka",)))
    monkeypatch.setattr(ffc, "load_flags_json", lambda path=None: {f: True for f in _FLAGS20})
    monkeypatch.setattr(ffc, "_decision_flags", lambda: (set(_FLAGS20), set()))
    monkeypatch.setattr(guard, "_read_log_lines",
                        lambda proc: [_cold_log_line(cold_ts)] if proc == "czasowka" else [])
    monkeypatch.setattr(guard, "_run_journalctl", lambda unit, since: journal_text)


# ── (a) DRIFT — ON alertuje raz, OFF cisza ───────────────────────────────────
def _wire_drift(monkeypatch, findings_holder):
    monkeypatch.setattr(ffc, "reconcile", lambda: _fake_res(list(findings_holder)))
    monkeypatch.setattr(ffc, "load_flags_json", lambda path=None: {})
    monkeypatch.setattr(ffc, "_decision_flags", lambda: (set(), set()))


def test_drift_flag_on_alerts_once(monkeypatch, tmp_path):
    glog, _ = _wire_tmp(monkeypatch, tmp_path)
    env_dead = {"klass": "ENV-DEAD", "flag": "ENABLE_FOO",
                "detail": {"unit": "x.service"}, "who_wins": "flags.json wygrywa"}
    _wire_drift(monkeypatch, [env_dead])
    sent = _capture_telegram(monkeypatch)
    monkeypatch.setenv(guard.ALERT_FLAG, "1")

    t0 = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
    rec = guard.run(write=True, now=t0)
    assert rec["level"] == "DRIFT"
    assert len(sent) == 1  # edge: pierwszy raz → alert
    # drugi tick, identyczna sygnatura → cisza
    rec2 = guard.run(write=True, now=t0 + timedelta(minutes=5))
    assert rec2["level"] == "DRIFT"
    assert len(sent) == 1  # bez ponownego alertu

    lines = open(glog, encoding="utf-8").read().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["level"] == "DRIFT"
    assert json.loads(lines[0])["drift"][0]["flag"] == "ENABLE_FOO"


def test_drift_flag_off_no_send_but_logged(monkeypatch, tmp_path):
    glog, _ = _wire_tmp(monkeypatch, tmp_path)
    vmm = {"klass": "VALUE-MISMATCH", "flag": "ENABLE_BAR",
           "detail": {"shadow": "1", "czasowka": "0"}, "who_wins": "ujednolić drop-iny"}
    _wire_drift(monkeypatch, [vmm])
    sent = _capture_telegram(monkeypatch)
    monkeypatch.setenv(guard.ALERT_FLAG, "0")  # OFF hermetycznie

    rec = guard.run(write=True, now=datetime(2026, 7, 3, 12, 0, tzinfo=UTC))
    assert rec["level"] == "DRIFT"
    assert rec["alert"]["enabled"] is False
    assert rec["alert"]["would_send"] is True   # chciałby, ale nie uzbrojony
    assert len(sent) == 0                        # zero wysyłki
    assert json.loads(open(glog, encoding="utf-8").readline())["level"] == "DRIFT"


# ── (b) cold NIEPOTWIERDZONY journalem → UNVERIFIED, zero alertu (anty-§20) ───
def test_cold_unverified_when_outside_service_window(monkeypatch, tmp_path):
    _wire_tmp(monkeypatch, tmp_path)
    now = datetime(2026, 7, 3, 12, 35, tzinfo=UTC)
    cold_ts = datetime(2026, 7, 3, 12, 30, 33, tzinfo=UTC)          # „pytest burst"
    run_start = datetime(2026, 7, 3, 12, 0, 16, tzinfo=UTC)          # bieg serwisu 30 min wcześniej
    run_end = datetime(2026, 7, 3, 12, 0, 17, tzinfo=UTC)
    _wire_cold(monkeypatch, cold_ts, _journal_run(run_start, run_end))
    sent = _capture_telegram(monkeypatch)
    monkeypatch.setenv(guard.ALERT_FLAG, "1")  # nawet ON — i tak cisza (anty-§20)

    rec = guard.run(write=True, now=now)
    assert rec["level"] == "OK"                     # cold nie potwierdzony → nie COLD
    assert rec["counts"]["cold_unverified"] == 1
    assert rec["cold"][0]["confirm"]["status"] == "UNVERIFIED"
    assert rec["cold"][0]["confirm"]["reason"] == "cold_outside_service_windows"
    assert len(sent) == 0


def test_cold_unverified_when_journal_unavailable(monkeypatch, tmp_path):
    _wire_tmp(monkeypatch, tmp_path)
    now = datetime(2026, 7, 3, 12, 35, tzinfo=UTC)
    cold_ts = datetime(2026, 7, 3, 12, 30, 33, tzinfo=UTC)
    _wire_cold(monkeypatch, cold_ts, None)  # journal niedostępny
    sent = _capture_telegram(monkeypatch)
    monkeypatch.setenv(guard.ALERT_FLAG, "1")

    rec = guard.run(write=True, now=now)
    assert rec["level"] == "OK"
    assert rec["cold"][0]["confirm"]["status"] == "UNVERIFIED"
    assert rec["cold"][0]["confirm"]["reason"] == "journal_unavailable"
    assert len(sent) == 0


# ── (c) potwierdzony COLD → alert ────────────────────────────────────────────
def test_cold_confirmed_alerts(monkeypatch, tmp_path):
    _wire_tmp(monkeypatch, tmp_path)
    now = datetime(2026, 7, 3, 12, 35, tzinfo=UTC)
    cold_ts = datetime(2026, 7, 3, 12, 30, 17, tzinfo=UTC)
    run_start = datetime(2026, 7, 3, 12, 30, 16, tzinfo=UTC)   # bieg serwisu obejmuje cold
    run_end = datetime(2026, 7, 3, 12, 30, 17, tzinfo=UTC)
    _wire_cold(monkeypatch, cold_ts, _journal_run(run_start, run_end))
    sent = _capture_telegram(monkeypatch)
    monkeypatch.setenv(guard.ALERT_FLAG, "1")

    rec = guard.run(write=True, now=now)
    assert rec["level"] == "COLD"
    assert rec["counts"]["confirmed_cold"] == 1
    assert rec["cold"][0]["confirm"]["status"] == "CONFIRMED"
    assert len(sent) == 1
    assert "COLD" in sent[0]


# ── (d) recovery DRIFT→OK → jeden alert powrotu ──────────────────────────────
def test_recovery_emits_single_ok_alert(monkeypatch, tmp_path):
    _wire_tmp(monkeypatch, tmp_path)
    holder = [{"klass": "ENV-DEAD", "flag": "ENABLE_FOO",
               "detail": {"unit": "x"}, "who_wins": "y"}]
    _wire_drift(monkeypatch, holder)
    sent = _capture_telegram(monkeypatch)
    monkeypatch.setenv(guard.ALERT_FLAG, "1")

    t0 = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
    guard.run(write=True, now=t0)                 # DRIFT → alert #1
    assert len(sent) == 1
    holder.clear()                                # rozjazd znika
    rec = guard.run(write=True, now=t0 + timedelta(minutes=5))
    assert rec["level"] == "OK"
    assert len(sent) == 2                         # powrót do normy → alert #2
    assert "OK" in sent[1]
    # kolejny OK — już cisza
    guard.run(write=True, now=t0 + timedelta(minutes=10))
    assert len(sent) == 2


# ── benign → OK, zero alertu ─────────────────────────────────────────────────
def test_benign_only_is_ok_no_alert(monkeypatch, tmp_path):
    _wire_tmp(monkeypatch, tmp_path)
    benign = [{"klass": "COVERAGE-GAP", "flag": "ENABLE_X", "detail": {}, "who_wins": "restart"},
              {"klass": "JSON-DRIFT", "flag": "ENABLE_Y", "detail": {}, "who_wins": "benign"}]
    _wire_drift(monkeypatch, benign)
    sent = _capture_telegram(monkeypatch)
    monkeypatch.setenv(guard.ALERT_FLAG, "1")
    rec = guard.run(write=True, now=datetime(2026, 7, 3, 12, 0, tzinfo=UTC))
    assert rec["level"] == "OK"
    assert rec["counts"]["benign"] == 2
    assert len(sent) == 0


# ── allowlista wycisza DRIFT ─────────────────────────────────────────────────
def test_allowlist_downgrades_drift(monkeypatch, tmp_path):
    _wire_tmp(monkeypatch, tmp_path)
    with open(guard.ALLOWLIST_PATH, "w", encoding="utf-8") as fh:
        json.dump({"accepted": ["ENV-DEAD:ENABLE_ACCEPTED"]}, fh)
    _wire_drift(monkeypatch, [{"klass": "ENV-DEAD", "flag": "ENABLE_ACCEPTED",
                               "detail": {}, "who_wins": "y"}])
    sent = _capture_telegram(monkeypatch)
    monkeypatch.setenv(guard.ALERT_FLAG, "1")
    rec = guard.run(write=True, now=datetime(2026, 7, 3, 12, 0, tzinfo=UTC))
    assert rec["level"] == "OK"
    assert rec["counts"]["accepted"] == 1
    assert len(sent) == 0


# ── (e) mutation-probe ×2 — dowód że testy mają zęby ─────────────────────────
def test_mutation_probe_edge_dedup_has_teeth():
    """Poprawnie: 2. tick identycznej sygnatury = cisza. Mutacja (utrata prev
    signature) łamie dedup → wysyła ponownie. Gdyby edge-warunek zniknął, test
    (a) `alerts_once` poszedłby RED."""
    now = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
    remind = timedelta(hours=6)
    s1, _, st1 = guard.notify_decision("DRIFT", "DRIFT|A:B", "d", {}, now, remind)
    assert s1 is True
    s2, _, _ = guard.notify_decision("DRIFT", "DRIFT|A:B", "d", st1,
                                     now + timedelta(minutes=1), remind)
    assert s2 is False                       # dedup DZIAŁA
    # MUTACJA: podaj pusty prev (edge zepsuty) → dedup pada
    s2m, _, _ = guard.notify_decision("DRIFT", "DRIFT|A:B", "d", {},
                                      now + timedelta(minutes=1), remind)
    assert s2m is True                       # bez prev-signature dedup NIE działa


def test_mutation_probe_journal_tol_has_teeth(monkeypatch, tmp_path):
    """Anty-§20: mała tolerancja okna trzyma cold POZA biegiem jako UNVERIFIED.
    Mutacja tol→ogromna fałszywie CONFIRMUJE obcą (pytest) cold-linię → gdyby
    ktoś rozluźnił bound, test (b) poszedłby RED."""
    _wire_tmp(monkeypatch, tmp_path)
    now = datetime(2026, 7, 3, 12, 35, tzinfo=UTC)
    cold_ts = datetime(2026, 7, 3, 12, 30, 33, tzinfo=UTC)
    run_start = datetime(2026, 7, 3, 12, 0, 16, tzinfo=UTC)
    run_end = datetime(2026, 7, 3, 12, 0, 17, tzinfo=UTC)
    _wire_cold(monkeypatch, cold_ts, _journal_run(run_start, run_end))
    fjson = {f: True for f in _FLAGS20}
    bf = set(_FLAGS20)
    since = now - timedelta(minutes=guard.COLD_LOOKBACK_MIN)

    # normalny bound → poza oknem → UNVERIFIED
    assert guard.confirm_cold("czasowka", since, fjson, bf)["status"] == "UNVERIFIED"
    # mutacja: ogromna tolerancja → wpada w okno → CONFIRMED (bug demaskowany)
    monkeypatch.setattr(guard, "JOURNAL_WINDOW_TOL_S", 10 ** 9)
    assert guard.confirm_cold("czasowka", since, fjson, bf)["status"] == "CONFIRMED"


# ── live-smoke: reconcile realny, tylko odczyt, bez wysyłki ───────────────────
def test_live_smoke_read_only(monkeypatch, tmp_path):
    _wire_tmp(monkeypatch, tmp_path)
    monkeypatch.setenv(guard.ALERT_FLAG, "0")
    rec = guard.run(write=True, now=None)
    assert rec["level"] in ("OK", "DRIFT", "COLD")
    assert rec["alert"]["sent"] is False
    assert os.path.exists(guard.GUARD_LOG)
