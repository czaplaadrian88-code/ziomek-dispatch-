"""A2 + A3 (2026-06-20): % on-time (≤35 min) + normalizacja override/KOORD na rate.

Testuje czysto-raportowe dodatki do daily_briefing:
  A2  _ontime_sla_lines — agregat % on-time z dispatch_state/sla_log.jsonl,
      rozbicie peak/off-peak, fail-soft „BRAK DANYCH" gdy plik brak/pusty.
  A3  _action_rate / _acceptance_line — override + KOORD jako % wszystkich
      propozycji (mianownik = total decyzji), nie surowy licznik.

Syntetyczne dane w tmp_path; ŻADNYCH żywych plików nie dotykamy (klasa #180).
Path do sla_log podmieniany przez monkeypatch ONTIME_SLA_LOG_PATH.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import daily_briefing as db


# Okno: „dziś" Warsaw w UTC, stały punkt referencyjny niezależny od pory uruchomienia.
_TODAY_W = datetime.now(db.WARSAW).replace(hour=0, minute=0, second=0, microsecond=0)
START = _TODAY_W.astimezone(timezone.utc)
END = (_TODAY_W + timedelta(days=1)).astimezone(timezone.utc)


def _at_hour(hour: int, minute: int = 30) -> str:
    """logged_at ISO dla danej godziny Warsaw dzisiaj (peak gdy hour ∈ 11-13/17-19)."""
    dt = _TODAY_W.replace(hour=hour, minute=minute)
    return dt.astimezone(timezone.utc).isoformat()


def _rec(oid, *, on_time=None, dmin=None, peak=None, hour=12):
    """Rekord on-time sla_log. on_time/peak jako pola wprost (gdy podane),
    inaczej fallback wyznaczany z dmin / godziny logged_at."""
    r = {"order_id": str(oid), "logged_at": _at_hour(hour)}
    if on_time is not None:
        r["on_time"] = on_time
    if dmin is not None:
        r["delivery_time_minutes"] = dmin
    if peak is not None:
        r["peak"] = peak
    return r


def _write_log(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _join(lines):
    return "\n".join(lines)


# ─────────────────────────── A2: % on-time ───────────────────────────

def test_ontime_brak_pliku_fail_soft(tmp_path, monkeypatch):
    """Plik nie istnieje → „BRAK DANYCH" bez wyjątku (worker jeszcze nie zapisał)."""
    missing = tmp_path / "nie_ma_sla_log.jsonl"
    monkeypatch.setattr(db, "ONTIME_SLA_LOG_PATH", str(missing))
    lines = db._ontime_sla_lines(START, END)
    assert lines, "sekcja musi zawsze coś zwrócić (fail-soft, nie [])"
    assert "BRAK DANYCH" in _join(lines)
    assert "worker jeszcze nie zapisał" in _join(lines)


def test_ontime_pusty_plik_brak_danych(tmp_path, monkeypatch):
    """Plik istnieje ale pusty → BRAK DANYCH, nie 0/0 = ZeroDivision."""
    p = tmp_path / "sla_log.jsonl"
    p.write_text("", encoding="utf-8")
    monkeypatch.setattr(db, "ONTIME_SLA_LOG_PATH", str(p))
    assert "BRAK DANYCH" in _join(db._ontime_sla_lines(START, END))


def test_ontime_agregat_z_pola_on_time(tmp_path, monkeypatch):
    """% on-time liczony z pola on_time (bool). 3/4 = 75%."""
    p = tmp_path / "sla_log.jsonl"
    _write_log(p, [
        _rec(1, on_time=True, hour=12),
        _rec(2, on_time=True, hour=12),
        _rec(3, on_time=True, hour=18),
        _rec(4, on_time=False, hour=18),
    ])
    monkeypatch.setattr(db, "ONTIME_SLA_LOG_PATH", str(p))
    out = _join(db._ontime_sla_lines(START, END))
    assert "3/4 = 75.0%" in out


def test_ontime_fallback_z_delivery_time_minutes(tmp_path, monkeypatch):
    """Brak pola on_time → fallback delivery_time_minutes ≤ 35. 34.9 OK, 35.1 NIE,
    35.0 = brzeg włącznie OK. 2/3 = 66.7%."""
    p = tmp_path / "sla_log.jsonl"
    _write_log(p, [
        _rec(1, dmin=34.9, hour=12),   # on-time
        _rec(2, dmin=35.0, hour=12),   # brzeg ≤35 → on-time
        _rec(3, dmin=35.1, hour=12),   # late
    ])
    monkeypatch.setattr(db, "ONTIME_SLA_LOG_PATH", str(p))
    out = _join(db._ontime_sla_lines(START, END))
    assert "2/3 = 66.7%" in out


def test_ontime_peak_off_z_pola_peak(tmp_path, monkeypatch):
    """Rozbicie peak/off z jawnego pola peak."""
    p = tmp_path / "sla_log.jsonl"
    _write_log(p, [
        _rec(1, on_time=True, peak=True),
        _rec(2, on_time=False, peak=True),    # peak: 1/2 = 50%
        _rec(3, on_time=True, peak=False),
        _rec(4, on_time=True, peak=False),    # off: 2/2 = 100%
    ])
    monkeypatch.setattr(db, "ONTIME_SLA_LOG_PATH", str(p))
    out = _join(db._ontime_sla_lines(START, END))
    assert "3/4 = 75.0%" in out          # total: 3 on-time z 4
    assert "peak 50% (1/2)" in out
    assert "off 100% (2/2)" in out


def test_ontime_peak_off_fallback_po_godzinie(tmp_path, monkeypatch):
    """Brak pola peak → peak wyznaczony po godzinie logged_at (Warsaw).
    12:30 i 18:30 = peak (11-14/17-20); 16:30 i 21:30 = off."""
    p = tmp_path / "sla_log.jsonl"
    _write_log(p, [
        _rec(1, on_time=True, hour=12),   # peak
        _rec(2, on_time=False, hour=18),  # peak  → peak 1/2 = 50%
        _rec(3, on_time=True, hour=16),   # off
        _rec(4, on_time=True, hour=21),   # off   → off 2/2 = 100%
    ])
    monkeypatch.setattr(db, "ONTIME_SLA_LOG_PATH", str(p))
    out = _join(db._ontime_sla_lines(START, END))
    assert "peak 50% (1/2)" in out
    assert "off 100% (2/2)" in out


def test_ontime_filtr_okna_czasu(tmp_path, monkeypatch):
    """Rekordy spoza okna [START, END) odrzucone (po delivered_at/logged_at)."""
    p = tmp_path / "sla_log.jsonl"
    yest = (_TODAY_W - timedelta(hours=2)).astimezone(timezone.utc).isoformat()
    in_win = {"order_id": "in", "on_time": True, "logged_at": _at_hour(12)}
    out_win = {"order_id": "out", "on_time": False, "logged_at": yest}
    _write_log(p, [in_win, out_win])
    monkeypatch.setattr(db, "ONTIME_SLA_LOG_PATH", str(p))
    out = _join(db._ontime_sla_lines(START, END))
    assert "1/1 = 100.0%" in out  # tylko in-window policzony


def test_ontime_rekord_bez_rozstrzygniecia_pomijany(tmp_path, monkeypatch):
    """Rekord bez on_time i bez delivery_time_minutes → pomijany (nie liczony),
    ale nie wywraca sekcji."""
    p = tmp_path / "sla_log.jsonl"
    _write_log(p, [
        _rec(1, on_time=True, hour=12),
        {"order_id": "x", "logged_at": _at_hour(12)},  # brak on_time i dmin
    ])
    monkeypatch.setattr(db, "ONTIME_SLA_LOG_PATH", str(p))
    out = _join(db._ontime_sla_lines(START, END))
    assert "1/1 = 100.0%" in out


def test_ontime_smieciowa_linia_nie_wywraca(tmp_path, monkeypatch):
    """Niepełny/niepoprawny JSON w pliku → pomijany, sekcja nadal liczy resztę."""
    p = tmp_path / "sla_log.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        f.write("to nie jest json\n")
        f.write(json.dumps(_rec(1, on_time=True, hour=12)) + "\n")
        f.write("{niepełny\n")
    monkeypatch.setattr(db, "ONTIME_SLA_LOG_PATH", str(p))
    out = _join(db._ontime_sla_lines(START, END))
    assert "1/1 = 100.0%" in out


# ─────────────────────── A3: override/KOORD jako rate ───────────────────────

def test_action_rate_liczy_procent():
    """_action_rate: count + % mianownika total."""
    assert db._action_rate("KOORD", 12, 200) == "KOORD:12 (6.0%)"
    assert db._action_rate("NIE", 5, 100) == "NIE:5 (5.0%)"


def test_action_rate_total_zero_sam_licznik():
    """total=0 → brak mianownika, sam licznik (bez ZeroDivision)."""
    assert db._action_rate("KOORD", 3, 0) == "KOORD:3"


def test_acceptance_line_override_jako_rate():
    """A3: OVERRIDE w linii acceptance pokazany jako % (mianownik AGREE+OVERRIDE).
    Audyt: ~76% override → linia musi nieść rate, nie tylko count."""
    lc = Counter({"PANEL_AGREE": 24, "PANEL_OVERRIDE": 76})
    line = db._acceptance_line(lc)
    assert line is not None
    assert "24/100 = 24.0%" in line          # agreement
    assert "OVERRIDE: 76/100 = 76.0%" in line  # override jako rate


def test_acceptance_line_brak_danych_none():
    """Brak AGREE/OVERRIDE → None (sekcja się nie pojawia)."""
    assert db._acceptance_line(Counter()) is None


def test_koord_rate_w_evening_details(tmp_path, monkeypatch):
    """KOORD w wieczornym briefingu = % wszystkich decyzji, nie surowy licznik.
    Próbka decyzji: TAK×80, NIE×10, KOORD×8, INNY×2 → total 100 → KOORD 8.0%."""
    # learning_log w tmp_path; sla_log brak (evening pokaże BRAK DANYCH on-time).
    ll = tmp_path / "learning_log.jsonl"
    now = datetime.now(timezone.utc)
    recs = []
    plan = [("TAK", 80), ("NIE", 10), ("KOORD", 8), ("INNY", 2)]
    for action, n in plan:
        for i in range(n):
            recs.append({
                "ts": now.isoformat(),
                "order_id": f"{action}-{i}",
                "action": action,
                "decision": {"restaurant": "Testowa"},
            })
    with open(ll, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    monkeypatch.setattr(db, "LEARNING_LOG_PATH", str(ll))
    monkeypatch.setattr(db, "ONTIME_SLA_LOG_PATH", str(tmp_path / "brak.jsonl"))
    # Odetnij sekcje dotykające żywych plików/usług — testujemy tylko details + on-time.
    monkeypatch.setattr(db, "_count_delivered_in_range", lambda *a, **k: 0)
    monkeypatch.setattr(db, "_top_nie_restaurants", lambda *a, **k: [])
    monkeypatch.setattr(db, "_demand_forecast_lines", lambda *a, **k: [])
    monkeypatch.setattr(db, "_systemd_status_block", lambda: "")

    out = db.format_evening()
    assert "KOORD:8 (8.0%)" in out
    assert "NIE:10 (10.0%)" in out
    assert "INNY:2 (2.0%)" in out
    # surowy licznik bez % nie powinien już występować dla KOORD
    assert "KOORD:8 |" not in out and "KOORD:8\n" not in out
    # on-time fail-soft też obecny w evening
    assert "BRAK DANYCH" in out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
