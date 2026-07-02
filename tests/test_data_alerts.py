"""Testy monitora DANOWEGO observability/data_alerts.py (audyt 2.0 motyw #1, 2.B).

Zakres (C13 — behawioralne + mutation, NIE tekstowe):
  * 5 ewaluatorów jako CZYSTE funkcje: syntetyczne dane → asercja firing/nie-firing
    (polaryzacja + brzeg progu + bramka czasowa praca/peak).
  * edge-trigger: krawędź not→firing emituje RAZ; wciąż-firing w cooldownie NIE
    dubluje; po cooldownie re-emituje; firing→not = recovery.
  * flaga ON≠OFF: run(enabled=False)=no-op; run(enabled=True) przetwarza; default
    (enabled=None) czyta common.flag → OFF (dowód „default OFF w kodzie").
  * MUTATION-CHECK ×2 (C13): fizyczna mutacja źródła (polaryzacja + próg) MUSI
    wywrócić asercję behawioralną — dowód, że testy są load-bearing.

⚠ C12(e): conftest pinuje `_SCRIPTS_ROOT` na KANON, więc `import dispatch_v2.
observability.data_alerts` ładowałby z kanonu (gdzie modułu NIE MA). Ładujemy
WPROST z repo, w którym leży TEN test (`Path(__file__).parents[1]`) — samo-
lokalizacja, NIGDY hardcode ścieżki worktree; sprzątanie sys.modules w finally.

Run:
    /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/test_data_alerts.py -q
"""
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# --- Samo-lokalizacja modułu (kanon LUB dowolny worktree) --------------------
_REPO = Path(__file__).resolve().parents[1]
_MOD_PATH = _REPO / "observability" / "data_alerts.py"
_QUAL = "dispatch_v2.observability._data_alerts_under_test"

_SCRIPTS = "/root/.openclaw/workspace/scripts"
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)


def _load_module(qual: str, source: str | None = None):
    """Ładuje moduł z pliku (lub z podmienionego źródła dla mutacji).

    sys.modules czyszczone przez try/finally u wywołującego; tu tylko rejestruje.
    """
    if source is None:
        spec = importlib.util.spec_from_file_location(qual, str(_MOD_PATH))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[qual] = mod
        spec.loader.exec_module(mod)
        return mod
    # Wariant zmutowany: kompiluj podmienione źródło pod własną nazwą.
    mod = importlib.util.module_from_spec(
        importlib.util.spec_from_loader(qual, loader=None))
    mod.__file__ = str(_MOD_PATH)
    sys.modules[qual] = mod
    code = compile(source, str(_MOD_PATH), "exec")
    exec(code, mod.__dict__)
    return mod


_SAVED = sys.modules.get(_QUAL)
try:
    da = _load_module(_QUAL)
finally:
    if _SAVED is not None:
        sys.modules[_QUAL] = _SAVED


UTC = timezone.utc


def _now_at(hour_warsaw: int) -> datetime:
    """UTC datetime, którego godzina w Warszawie == hour_warsaw (lato = UTC+2)."""
    # 02.07.2026 = CEST (UTC+2). Konstruujemy z warszawskiej godziny.
    w = datetime(2026, 7, 2, hour_warsaw, 30, tzinfo=da.WARSAW)
    return w.astimezone(UTC)


def _rec(*, pos_source="gps", poison=False, v328=None, pool=3, ts=None):
    b = {"pos_source": pos_source}
    if poison:
        b["coord_poison_new_delivery"] = True
    r = {"best": b, "pool_feasible_count": pool}
    if v328 is not None:
        r["v328_fail_causes"] = v328
    if ts is not None:
        r["ts"] = ts
    return r


# ── 1. sentinel-rate ─────────────────────────────────────────────────────────
def test_sentinel_rate_fires_above_threshold():
    # 30 rekordów, 12 sentinel (None/poison/v328) = 40% > 15%
    recs = [_rec(pos_source="gps") for _ in range(18)]
    recs += [_rec(pos_source=None) for _ in range(6)]
    recs += [_rec(poison=True) for _ in range(3)]
    recs += [_rec(v328={"x": 1}) for _ in range(3)]
    s = da.evaluate_sentinel_rate(recs)
    assert s.firing is True
    assert s.value == pytest.approx(40.0, abs=0.1)


def test_sentinel_rate_no_gps_and_preshift_are_NOT_sentinel():
    # KANON: no_gps / pre_shift = legalna polityka, NIGDY alarm.
    recs = [_rec(pos_source="no_gps") for _ in range(25)]
    recs += [_rec(pos_source="pre_shift") for _ in range(15)]
    s = da.evaluate_sentinel_rate(recs)
    assert s.value == 0.0
    assert s.firing is False


def test_sentinel_rate_below_min_sample_never_fires():
    recs = [_rec(pos_source=None) for _ in range(5)]  # 100% ale n<MIN_SAMPLE
    s = da.evaluate_sentinel_rate(recs)
    assert s.firing is False
    assert s.sample == 5


def test_sentinel_rate_boundary_strict():
    # Dokładnie na progu (15%) → NIE firing (strict >). Tuż powyżej → firing.
    at = [_rec(pos_source=None) for _ in range(3)] + [_rec() for _ in range(17)]
    s_at = da.evaluate_sentinel_rate(at, threshold_pct=15.0, min_sample=20)
    assert s_at.value == pytest.approx(15.0, abs=0.01)
    assert s_at.firing is False
    above = [_rec(pos_source=None) for _ in range(4)] + [_rec() for _ in range(16)]
    s_above = da.evaluate_sentinel_rate(above, threshold_pct=15.0, min_sample=20)
    assert s_above.value == pytest.approx(20.0, abs=0.01)
    assert s_above.firing is True


# ── 2. empty-pool ────────────────────────────────────────────────────────────
def test_empty_pool_fires_above_threshold_in_working_hours():
    recs = [_rec(pool=0) for _ in range(12)] + [_rec(pool=3) for _ in range(8)]  # 60%
    s = da.evaluate_empty_pool(recs, now=_now_at(13))
    assert s.firing is True
    assert s.value == pytest.approx(60.0, abs=0.1)


def test_empty_pool_suppressed_outside_working_hours():
    recs = [_rec(pool=0) for _ in range(20)]  # 100%
    s = da.evaluate_empty_pool(recs, now=_now_at(3))  # 03:00 Warsaw = poza pracą
    assert s.window_open is False
    assert s.firing is False


def test_empty_pool_normal_tail_does_not_fire():
    recs = [_rec(pool=0) for _ in range(2)] + [_rec(pool=3) for _ in range(18)]  # 10%
    s = da.evaluate_empty_pool(recs, now=_now_at(13))
    assert s.firing is False


# ── 3. stale-grafik ──────────────────────────────────────────────────────────
def test_stale_grafik_fires_when_old_in_working_hours():
    now = _now_at(13)
    fetched = now - timedelta(hours=8)
    s = da.evaluate_stale_grafik(fetched, now, threshold_h=6.0)
    assert s.firing is True
    assert s.value == pytest.approx(8.0, abs=0.05)


def test_stale_grafik_fresh_does_not_fire():
    now = _now_at(13)
    s = da.evaluate_stale_grafik(now - timedelta(hours=1), now, threshold_h=6.0)
    assert s.firing is False


def test_stale_grafik_missing_marker_fires_only_in_working_hours():
    assert da.evaluate_stale_grafik(None, _now_at(13)).firing is True
    assert da.evaluate_stale_grafik(None, _now_at(4)).firing is False


# ── 4. stale-pozycje GPS ─────────────────────────────────────────────────────
def _pos(minutes_ago, now):
    return {"ts": (now - timedelta(minutes=minutes_ago)).isoformat()}


def test_stale_gps_fires_when_majority_stale():
    now = _now_at(13)
    positions = {str(i): _pos(40, now) for i in range(6)}      # 6 stare
    positions.update({str(100 + i): _pos(2, now) for i in range(4)})  # 4 świeże
    s = da.evaluate_stale_gps(positions, now)  # 60% > 50%
    assert s.firing is True
    assert s.value == pytest.approx(60.0, abs=0.1)


def test_stale_gps_fresh_fleet_does_not_fire():
    now = _now_at(13)
    positions = {str(i): _pos(2, now) for i in range(10)}
    s = da.evaluate_stale_gps(positions, now)
    assert s.firing is False


def test_stale_gps_below_min_fleet_never_fires():
    now = _now_at(13)
    positions = {"1": _pos(99, now), "2": _pos(99, now)}  # 100% ale n<min_fleet
    s = da.evaluate_stale_gps(positions, now)
    assert s.firing is False


# ── 5. ledger-stall ──────────────────────────────────────────────────────────
def test_ledger_stall_fires_in_peak_when_silent():
    now = _now_at(12)  # 12:00 Warsaw = peak
    latest = now - timedelta(minutes=30)
    s = da.evaluate_ledger_stall(latest, now, threshold_min=20.0)
    assert s.window_open is True
    assert s.firing is True


def test_ledger_stall_suppressed_outside_peak():
    now = _now_at(15)  # 15:00 = poza peakiem (11-14/17-20)
    latest = now - timedelta(minutes=90)
    s = da.evaluate_ledger_stall(latest, now, threshold_min=20.0)
    assert s.window_open is False
    assert s.firing is False


def test_ledger_stall_fresh_does_not_fire():
    now = _now_at(12)
    s = da.evaluate_ledger_stall(now - timedelta(minutes=1), now, threshold_min=20.0)
    assert s.firing is False


# ── Edge-trigger (decide_emissions) ──────────────────────────────────────────
def _firing_signal(name="sentinel_rate"):
    return da.Signal(name, firing=True, value=99.0, threshold=15.0, sample=50,
                     detail="test firing", window_open=True)


def _quiet_signal(name="sentinel_rate"):
    return da.Signal(name, firing=False, value=0.0, threshold=15.0, sample=50,
                     detail="test quiet", window_open=True)


def test_edge_trigger_emits_once_then_dedupes():
    now = _now_at(12)
    state = {"signals": {}}
    emit1, state1 = da.decide_emissions([_firing_signal()], state, now)
    assert [s.name for s in emit1] == ["sentinel_rate"]          # krawędź
    # ten sam firing 5 min później, w cooldownie (60 min) → NIE dubluje
    emit2, state2 = da.decide_emissions([_firing_signal()], state1,
                                        now + timedelta(minutes=5))
    assert emit2 == []
    # po cooldownie → re-emisja
    emit3, _ = da.decide_emissions([_firing_signal()], state2,
                                   now + timedelta(minutes=61))
    assert [s.name for s in emit3] == ["sentinel_rate"]


def test_edge_trigger_recovery_flag():
    now = _now_at(12)
    _, state1 = da.decide_emissions([_firing_signal()], {"signals": {}}, now)
    emit2, state2 = da.decide_emissions([_quiet_signal()], state1,
                                        now + timedelta(minutes=5))
    assert emit2 == []
    assert state2["signals"]["sentinel_rate"]["recovered"] is True
    assert state2["signals"]["sentinel_rate"]["firing"] is False


# ── Flaga ON≠OFF + default OFF ───────────────────────────────────────────────
def test_run_disabled_is_noop(tmp_path):
    res = da.run(now=_now_at(12), enabled=False,
                 state_path=tmp_path / "st.json", log_path=tmp_path / "l.log")
    assert res["enabled"] is False
    assert res["signals"] == []
    assert not (tmp_path / "st.json").exists()  # OFF nie pisze stanu


def test_run_enabled_processes_and_writes_state(tmp_path):
    res = da.run(now=_now_at(12), enabled=True, telegram=False,
                 state_path=tmp_path / "st.json", log_path=tmp_path / "l.log",
                 signals=[_firing_signal()])
    assert res["enabled"] is True
    assert res["emitted"] == ["sentinel_rate"]
    assert (tmp_path / "st.json").exists()          # ON pisze stan
    assert (tmp_path / "l.log").exists()            # ON loguje alert


def test_run_on_vs_off_differ(tmp_path):
    common_kw = dict(now=_now_at(12), telegram=False,
                     state_path=tmp_path / "s.json", log_path=tmp_path / "l.log",
                     signals=[_firing_signal()])
    off = da.run(enabled=False, **common_kw)
    on = da.run(enabled=True, **common_kw)
    assert off["enabled"] is False and on["enabled"] is True
    assert off.get("emitted", []) == [] and on["emitted"] == ["sentinel_rate"]


def test_default_flag_is_off(monkeypatch, tmp_path):
    # enabled=None → _flag("ENABLE_DATA_ALERTS", False); flags.json (izolowany
    # conftestem) nie ma klucza → default False → no-op. Dowód „default OFF".
    res = da.run(now=_now_at(12), enabled=None,
                 state_path=tmp_path / "s.json", log_path=tmp_path / "l.log")
    assert res["enabled"] is False


def test_telegram_gated_off_by_default(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(da, "_emit_telegram", lambda to_emit: sent.append(to_emit))
    da.run(now=_now_at(12), enabled=True, telegram=False,
           state_path=tmp_path / "s.json", log_path=tmp_path / "l.log",
           signals=[_firing_signal()])
    assert sent == []  # telegram flag OFF → brak wysyłki
    da.run(now=_now_at(12), enabled=True, telegram=True,
           state_path=tmp_path / "s2.json", log_path=tmp_path / "l2.log",
           signals=[_firing_signal()])
    assert len(sent) == 1  # telegram ON → wywołane


# ── Atomowy zapis stanu ──────────────────────────────────────────────────────
def test_state_roundtrip_atomic(tmp_path):
    sp = tmp_path / "state.json"
    _, st = da.decide_emissions([_firing_signal()], {"signals": {}}, _now_at(12))
    da._atomic_write_json(sp, st)
    loaded = da._load_state(sp)
    assert loaded["signals"]["sentinel_rate"]["firing"] is True
    # brak plików tmp po zapisie
    assert list(tmp_path.glob("*.tmp")) == []


# ── MUTATION-CHECK ×2 (C13): fizyczna mutacja źródła MUSI wywrócić asercję ────
_SRC = _MOD_PATH.read_text()


def test_mutation_sentinel_polarity_is_caught():
    """Mutacja polaryzacji progu sentinel (`>`→`<`) MUSI zmienić wynik na tym
    samym zestawie danych — dowód, że test_sentinel_rate_fires_above_threshold
    realnie bramkuje polaryzację, nie tylko obecność."""
    assert "rate > threshold_pct" in _SRC, "kotwica mutacji zniknęła — zaktualizuj test"
    mutated = _SRC.replace("rate > threshold_pct", "rate < threshold_pct")
    qual = "dispatch_v2.observability._data_alerts_mut_polarity"
    saved = sys.modules.get(qual)
    try:
        mod = _load_module(qual, source=mutated)
        recs = [_rec(pos_source=None) for _ in range(8)] + [_rec() for _ in range(12)]  # 40%
        real = da.evaluate_sentinel_rate(recs)
        mut = mod.evaluate_sentinel_rate(recs)
        assert real.firing is True          # zdrowy: 40% > 15% → firing
        assert mut.firing is False          # zmutowany: 40% < 15% == False → ZŁAPANE
    finally:
        sys.modules.pop(qual, None)
        if saved is not None:
            sys.modules[qual] = saved


def test_mutation_ledger_stall_threshold_is_caught():
    """Mutacja progu ledger-stall (`gap_min > threshold_min` → `gap_min > threshold_min + 1e9`)
    dezaktywuje detektor stalla — test_ledger_stall_fires_in_peak_when_silent
    MUSI to wychwycić."""
    anchor = "gap_min > threshold_min"
    assert anchor in _SRC, "kotwica mutacji zniknęła — zaktualizuj test"
    mutated = _SRC.replace(anchor, "gap_min > (threshold_min + 1e9)")
    qual = "dispatch_v2.observability._data_alerts_mut_thr"
    saved = sys.modules.get(qual)
    try:
        mod = _load_module(qual, source=mutated)
        now = _now_at(12)
        latest = now - timedelta(minutes=30)
        real = da.evaluate_ledger_stall(latest, now, threshold_min=20.0)
        mut = mod.evaluate_ledger_stall(latest, now, threshold_min=20.0)
        assert real.firing is True          # zdrowy: 30min > 20min → firing
        assert mut.firing is False          # zmutowany próg astronomiczny → ZŁAPANE
    finally:
        sys.modules.pop(qual, None)
        if saved is not None:
            sys.modules[qual] = saved
