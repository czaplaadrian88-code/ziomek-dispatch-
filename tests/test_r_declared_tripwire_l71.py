"""L7.1 R-DECLARED tripwire (audyt 2026-06-30 root R7-I-E).

Reguła biznesowa R-DECLARED-TIME (HARD): `czas_kuriera >= czas_odbioru_timestamp`
(deklarowany przyjazd kuriera NIE wcześniej niż deklarowany czas odbioru). JEDEN
obserwacyjny tripwire w chokepoincie zapisu (state_machine.upsert_order — jedyny
funnel commitowanego czas_kuriera). Fail-loud LOG + append JSONL, NIGDY reject /
zmiana decyzji (always-propose).

Pokrycie: naruszenie→wpis+log · brak naruszenia→cisza · flaga OFF→zero ścieżki +
bajt-parytet zwracanego rekordu · throttle (edge per oid+sygnatura) · TZ (naive
pickup jako Warsaw, NIE fixed-offset) · brak pola→cisza · mutation-probe kierunku
nierówności · tolerancja env-tunable · rejestracja flagi (obserwacyjna, poza
ETAP4_DECISION_FLAGS).

Izolacja: monkeypatch state_machine.flag / .R_DECLARED_TRIPWIRE_TOLERANCE_MIN
(auto-restore per test — żadnego leaku do innych plików suity, klasa Lekcji #75).
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

_TMP_DIR = tempfile.mkdtemp(prefix="r_declared_tripwire_test_")
os.environ["DISPATCH_STATE_DIR"] = _TMP_DIR

from dispatch_v2 import state_machine  # noqa: E402
from dispatch_v2 import common as C    # noqa: E402


def _setup(monkeypatch, flag_on=True, tolerance=0.0):
    """Świeży stan + throttle + kontrola flagi/tolerancji tripwire (auto-restore)."""
    p = state_machine._state_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write("{}")
    log = os.path.join(os.path.dirname(p), "r_declared_tripwire.jsonl")
    if os.path.exists(log):
        os.remove(log)
    state_machine._R_DECLARED_LOGGED.clear()
    _orig_flag = state_machine.flag
    monkeypatch.setattr(
        state_machine, "flag",
        lambda name, default=False: (
            flag_on if name == "ENABLE_R_DECLARED_TRIPWIRE" else _orig_flag(name, default)))
    monkeypatch.setattr(state_machine, "R_DECLARED_TRIPWIRE_TOLERANCE_MIN", tolerance)
    return log


def _read_log(log):
    if not os.path.exists(log):
        return []
    with open(log) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def _order(ck_hhmm, ck_iso, pickup_iso, **extra):
    """Deklaracje jak persistuje panel_client (aware Warsaw ISO domyślnie)."""
    d = {
        "status": "assigned",
        "courier_id": "515",
        "order_type": "elastic",
        "czas_kuriera_hhmm": ck_hhmm,
        "czas_kuriera_warsaw": ck_iso,
        "pickup_at_warsaw": pickup_iso,
    }
    d.update(extra)
    return d


def test_violation_logs_and_writes_jsonl(monkeypatch):
    log = _setup(monkeypatch, flag_on=True)
    # ck 12:15 < pickup 12:20 → naruszenie Δ=-5
    state_machine.upsert_order(
        "9001",
        _order("12:15", "2026-07-03T12:15:00+02:00", "2026-07-03T12:20:00+02:00"),
        event="COURIER_ASSIGNED")
    rows = _read_log(log)
    assert len(rows) == 1
    r = rows[0]
    assert r["oid"] == "9001"
    assert r["event"] == "COURIER_ASSIGNED"
    assert r["czas_kuriera_hhmm"] == "12:15"
    assert r["czas_odbioru_timestamp"] == "2026-07-03T12:20:00+02:00"
    assert r["delta_min"] == -5.0
    assert r["courier_id"] == "515"


def test_no_violation_silent(monkeypatch):
    log = _setup(monkeypatch, flag_on=True)
    # ck 12:25 >= pickup 12:20 → OK, cisza
    state_machine.upsert_order(
        "9002",
        _order("12:25", "2026-07-03T12:25:00+02:00", "2026-07-03T12:20:00+02:00"),
        event="COURIER_ASSIGNED")
    assert _read_log(log) == []


def test_boundary_equal_is_ok(monkeypatch):
    # Reguła to `>=` — równość NIE jest naruszeniem (mutation-probe: flip na `>`
    # albo `<=` w strażniku wywróci ten albo poprzedni test na RED).
    log = _setup(monkeypatch, flag_on=True)
    state_machine.upsert_order(
        "9003",
        _order("12:20", "2026-07-03T12:20:00+02:00", "2026-07-03T12:20:00+02:00"),
        event="COURIER_ASSIGNED")
    assert _read_log(log) == []


def test_flag_off_zero_path_and_byte_parity(monkeypatch):
    # Flaga OFF: żadnego JSONL + zwracany rekord IDENTYCZNY jak przy ON
    # (tripwire nigdy nie dotyka `merged`). Bajt-parytet ścieżki decyzji.
    payload = _order("12:15", "2026-07-03T12:15:00+02:00", "2026-07-03T12:20:00+02:00")

    log_on = _setup(monkeypatch, flag_on=True)
    rec_on = state_machine.upsert_order("9004", dict(payload), event="COURIER_ASSIGNED")
    assert len(_read_log(log_on)) == 1

    log_off = _setup(monkeypatch, flag_on=False)
    rec_off = state_machine.upsert_order("9004", dict(payload), event="COURIER_ASSIGNED")
    assert _read_log(log_off) == []            # OFF = zero kodu ścieżki

    # Parytet: te same klucze, brak wstrzykniętych pól tripwire, wartości równe
    # poza wolącymi się znacznikami czasu (updated_at / history 'at').
    assert set(rec_on) == set(rec_off)
    volatile = {"updated_at", "history"}
    for k in set(rec_on) - volatile:
        assert rec_on[k] == rec_off[k], f"parytet złamany na {k}"


def test_throttle_same_signature_once(monkeypatch):
    log = _setup(monkeypatch, flag_on=True)
    payload = _order("12:15", "2026-07-03T12:15:00+02:00", "2026-07-03T12:20:00+02:00")
    for _ in range(3):
        state_machine.upsert_order("9005", dict(payload), event="CZAS_KURIERA_UPDATED")
    assert len(_read_log(log)) == 1            # ta sama sygnatura → 1 wpis


def test_throttle_new_signature_relogs(monkeypatch):
    log = _setup(monkeypatch, flag_on=True)
    state_machine.upsert_order(
        "9006",
        _order("12:15", "2026-07-03T12:15:00+02:00", "2026-07-03T12:20:00+02:00"),
        event="COURIER_ASSIGNED")
    # Zmiana pickup (nowy stan naruszenia) → ponowny wpis.
    state_machine.upsert_order(
        "9006",
        {"pickup_at_warsaw": "2026-07-03T12:30:00+02:00"},
        event="PICKUP_TIME_UPDATED")
    assert len(_read_log(log)) == 2


def test_tz_naive_pickup_treated_as_warsaw(monkeypatch):
    # pickup_at_warsaw naive (bez offsetu) MUSI być liczony jako Warsaw-local,
    # NIE UTC/fixed-offset. ck 12:15+02:00 vs pickup naive 12:20 → Δ=-5 (Warsaw
    # axis). Gdyby naive potraktować jako UTC → Δ byłby +115 (brak naruszenia) →
    # test RED = łapie regres TZ (ratchet).
    log = _setup(monkeypatch, flag_on=True)
    state_machine.upsert_order(
        "9007",
        _order("12:15", "2026-07-03T12:15:00+02:00", "2026-07-03 12:20:00"),
        event="NEW_ORDER")
    rows = _read_log(log)
    assert len(rows) == 1
    assert rows[0]["delta_min"] == -5.0


def test_missing_pickup_field_silent(monkeypatch):
    log = _setup(monkeypatch, flag_on=True)
    state_machine.upsert_order(
        "9008",
        {"status": "planned", "czas_kuriera_hhmm": "12:15",
         "czas_kuriera_warsaw": "2026-07-03T12:15:00+02:00"},
        event="NEW_ORDER")
    assert _read_log(log) == []                # brak czas_odbioru → nic do sprawdzenia


def test_tolerance_suppresses_subminute(monkeypatch):
    # Tolerancja 1.0 min: Δ=-0.5 (sub-progowy szum re-stempla) → cisza;
    # Δ=-2.0 (realny) → wpis. Dowodzi env-tunable progu.
    log = _setup(monkeypatch, flag_on=True, tolerance=1.0)
    state_machine.upsert_order(
        "9009",
        _order("12:20", "2026-07-03T12:20:00+02:00", "2026-07-03T12:20:30+02:00"),
        event="COURIER_ASSIGNED")
    assert _read_log(log) == []
    state_machine.upsert_order(
        "9010",
        _order("12:18", "2026-07-03T12:18:00+02:00", "2026-07-03T12:20:00+02:00"),
        event="COURIER_ASSIGNED")
    assert len(_read_log(log)) == 1


def test_flag_registered_observational():
    # Obserwacyjna (log-only) — env-default OFF, POZA ETAP4_DECISION_FLAGS
    # (nie zmienia decyzji → nie wymaga cross-proces determinizmu / conftest strip).
    assert C.ENABLE_R_DECLARED_TRIPWIRE is False
    assert "ENABLE_R_DECLARED_TRIPWIRE" not in C.ETAP4_DECISION_FLAGS
    assert C.R_DECLARED_TRIPWIRE_TOLERANCE_MIN == 0.0
