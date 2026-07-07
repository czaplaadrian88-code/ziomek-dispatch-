"""Regresja B1 (2026-07-07): naprawa kłamiącego przyrządu scheduled_flip_gate.cmd_verify.

Root cause: markery plan_recheck (L3_REGEN / L4_ANCHOR_FLOOR / GC_COURIER_PLANS)
idą przez StreamHandler→stderr, a `dispatch-plan-recheck.service` ma
StandardOutput/StandardError=append:<plik> → lądują w PLIKU
(`logs/plan_recheck.log`), NIE w journalu. Stare cmd_verify skanowało tylko
`journalctl -u dispatch-plan-recheck` → wieczne marker_hits=0 (fałszywy sygnał;
ugryzło przy GC-verify at-206 06.07 — „0 markerów" mimo 22 realnych w pliku).

Dowód ON≠OFF: ta sama treść logu → skan JOURNALA (stare) = 0, skan PLIKU (nowe)
= realna liczba. Plus: err_burst pomija tło defensywne COORD_GUARD.

Standardowy plik pytest (funkcje test_*, brak module-level sys.exit) — kolekcja
normalna. Zero kontaktu z prod (tmp file + monkeypatch subprocess/_now/_log/_tg).
"""
from datetime import datetime, timedelta, timezone

import dispatch_v2.tools.scheduled_flip_gate as sfg


def _line(ts, msg):
    """Wiersz w formacie loggera plan_recheck (formatter datefmt='%Y-%m-%d %H:%M:%S')."""
    return ts.strftime("%Y-%m-%d %H:%M:%S") + " [INFO] plan_recheck: " + msg + "\n"


# ── DOWÓD ON≠OFF: journal (stare) = 0, plik (nowe) = realna liczba ──────────────
def test_markers_from_file_not_journal_before_zero_after_real(tmp_path, monkeypatch):
    now = datetime(2026, 7, 7, 18, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(sfg, "_now", lambda: now)

    log = tmp_path / "plan_recheck.log"
    log.write_text(
        _line(now - timedelta(minutes=5), "GC_COURIER_PLANS {'gc_age_removed': 0}")
        + _line(now - timedelta(minutes=40), "GC_COURIER_PLANS {'gc_age_removed': 1}")
        + _line(now - timedelta(minutes=90), "GC_COURIER_PLANS {'gc_age_removed': 0}")
        + _line(now - timedelta(minutes=180), "GC_COURIER_PLANS {'stale': True}")  # poza 2h
        + _line(now - timedelta(minutes=10), "some unrelated info line"),
        encoding="utf-8",
    )
    monkeypatch.setattr(sfg, "PLAN_RECHECK_LOG", str(log))

    # STARE zachowanie: journalctl -u dispatch-plan-recheck NIE ma markerów
    # (systemd kieruje je do pliku) → 0 (kłamstwo przyrządu).
    journal_lines = [
        "Jul 07 17:55:00 Ziomek python[1]: GET /health 200",
        "Jul 07 17:56:00 Ziomek python[1]: tick ok",
    ]
    assert sfg._count_markers(journal_lines, "GC_COURIER_PLANS") == 0  # PRZED = 0

    # NOWE zachowanie: skan PLIKU, okno 2h → 3 markery (−5, −40, −90 min);
    # −180 min odrzucony (poza oknem).
    win = sfg._read_log_window(sfg.PLAN_RECHECK_LOG, timedelta(hours=2))
    assert sfg._count_markers(win, "GC_COURIER_PLANS") == 3  # PO = realna liczba


# ── err_burst pomija tło defensywne COORD_GUARD (bliźniak gate↔verify) ──────────
def test_err_burst_excludes_coord_guard_background():
    lines = [
        "... [ERROR] osrm_client: COORD_GUARD #14: table 2 invalid coord(s) [(0.0,0.0)] ...",
        "... [ERROR] osrm_client: COORD_GUARD #15: table 2 invalid coord(s) [(0.0,0.0)] ...",
        "... [ERROR] dispatch_pipeline: prawdziwy wybuch",
        "Traceback (most recent call last):",
        "... [INFO] wszystko ok",
    ]
    real, benign = sfg._count_err_burst(lines)
    assert benign == 2  # 2× COORD_GUARD pominięte (nie alarmują)
    assert real == 2    # 1 realny ERROR + 1 Traceback nadal liczone


# ── okno czasowe + dziedziczenie kontynuacji (Traceback multi-line) ─────────────
def test_window_boundary_and_continuation_inherit(tmp_path, monkeypatch):
    now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(sfg, "_now", lambda: now)
    log = tmp_path / "pr.log"
    log.write_text(
        _line(now - timedelta(hours=3), "L3_REGEN_REJECTED cid=1 stary")     # poza oknem
        + _line(now - timedelta(minutes=30), "L3_REGEN_BOTH_BREACH cid=2")   # w oknie
        + "    kontynuacja bez znacznika czasu\n"                             # dziedziczy in_window
        + _line(now - timedelta(hours=5), "L3_REGEN_REJECTED cid=3 starszy"),  # poza oknem
        encoding="utf-8",
    )
    win = sfg._read_log_window(str(log), timedelta(hours=2))
    assert sfg._count_markers(win, "L3_REGEN") == 1  # tylko −30 min
    assert any("kontynuacja" in l for l in win)       # linia bez ts dziedziczy okno
    assert not any("stary" in l for l in win)
    assert not any("starszy" in l for l in win)


def test_read_log_window_missing_file_returns_empty(tmp_path):
    assert sfg._read_log_window(str(tmp_path / "nie_istnieje.log"), timedelta(hours=2)) == []


# ── E2E: cały cmd_verify raportuje realne markery (PRZED fix = 0 z journala) ─────
def test_cmd_verify_reports_file_markers_end_to_end(tmp_path, monkeypatch):
    now = datetime(2026, 7, 7, 18, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(sfg, "_now", lambda: now)

    log = tmp_path / "plan_recheck.log"
    log.write_text(
        _line(now - timedelta(minutes=5), "L4_ANCHOR_FLOOR cid=7 anchor 17:40 → floor")
        + _line(now - timedelta(minutes=50), "L4_ANCHOR_FLOOR cid=8 anchor 17:10 → floor"),
        encoding="utf-8",
    )
    monkeypatch.setattr(sfg, "PLAN_RECHECK_LOG", str(log))

    # journalctl zwraca PUSTKĘ dla markerów plan-recheck (jak w realu) + tło COORD_GUARD
    class _R:
        stdout = ("Jul 07 17:59 Ziomek python[1]: [ERROR] osrm_client: "
                  "COORD_GUARD #1: table 2 invalid coord(s) [(0.0,0.0)] → sentinel\n")

    monkeypatch.setattr(sfg.subprocess, "run", lambda *a, **k: _R())

    captured = {}
    monkeypatch.setattr(sfg, "_log", lambda rec: captured.update(rec))
    monkeypatch.setattr(sfg, "_telegram", lambda msg: None)

    class _A:
        profile = "l4"
        rollback_on_error = False

    rc = sfg.cmd_verify(_A())
    assert rc == 0
    assert captured["marker_hits"] == 2         # PO: 2 markery z PLIKU (stare = 0 z journala)
    assert captured["err_burst"] == 0           # COORD_GUARD odfiltrowany z alarmu
    assert captured["coord_guard_benign"] == 1  # ale policzony osobno (nie zgubiony)
