"""EARLYBIRD-01 forward-shadow (2026-06-14) — kontrfaktyk T-30 dla early_bird KOORD.

Cel pomiarowy: domknąć lukę „deferowalności" (early_bird zwiera obwód PRZED pulą
feasibility → nie wiemy czy zlecenie byłoby rozwiązywalne). Shadow re-uruchamia
assess_order z _bypass_early_bird=True i loguje kontrfaktyk. LOG-ONLY — live verdict
POZOSTAJE early_bird KOORD. Decyzja: VERDICT_c_redux_measurement_2026-06-14.md.

Niezmienniki testowane:
  1. _bypass_early_bird=True przepuszcza do feasibility (NIE zwraca early_bird).
  2. Flaga OFF → live KOORD, ZERO shadow-logu (zero rekurencji, zero kosztu).
  3. Flaga ON → live verdict NADAL early_bird KOORD (zero zmiany zachowania)
     + DOKŁADNIE jeden wpis shadow (kontrfaktyk), bez nieskończonej rekurencji.
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import dispatch_pipeline


def _build_order_event(now_utc, raw_mtp_min, pickup_coords=(53.13, 23.17)):
    """Order_event z deklaracją odbioru raw_mtp_min minut w przód (wzór z test_czasowki_fixes)."""
    raw_iso = (now_utc + timedelta(minutes=raw_mtp_min)).astimezone().isoformat()
    return {
        "order_id": "TEST_EB_SHADOW",
        "restaurant": "Test Restauracja",
        "pickup_address": "Pickup 1",
        "pickup_city": "Białystok",
        "delivery_address": "Drop 1",
        "delivery_city": "Białystok",
        "pickup_at_warsaw": raw_iso,
        "pickup_coords": list(pickup_coords),
        "delivery_coords": [53.14, 23.16],
        "status_id": 2,
        "first_seen": (now_utc - timedelta(minutes=5)).isoformat(),
        "address_id": 1,
        "czas_kuriera_warsaw": None,
        "czas_kuriera_hhmm": None,
    }


def test_bypass_skips_early_bird_runs_pipeline():
    """_bypass_early_bird=True: raw=70 (>60) NIE zwraca early_bird — leci do feasibility."""
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    ev = _build_order_event(now_utc, raw_mtp_min=70.0)
    res = dispatch_pipeline.assess_order(ev, {}, now=now_utc, _bypass_early_bird=True)
    assert "early_bird" not in (res.reason or ""), \
        f"bypass powinien przepuścić do feasibility, got reason={res.reason!r}"


def test_flag_off_no_shadow_log(monkeypatch):
    """Flaga OFF: live early_bird KOORD + ZERO wywołań writera (zero rekurencji/kosztu)."""
    calls = []
    monkeypatch.setattr(dispatch_pipeline, "_earlybird_t30_shadow_enabled", lambda: False)
    monkeypatch.setattr(dispatch_pipeline, "_append_earlybird_t30_shadow", lambda e: calls.append(e))
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    ev = _build_order_event(now_utc, raw_mtp_min=70.0)
    res = dispatch_pipeline.assess_order(ev, {}, now=now_utc)
    assert res.verdict == "KOORD" and "early_bird" in (res.reason or "")
    assert calls == [], f"flaga OFF nie powinna logować shadow, got {len(calls)} wpisów"


def test_flag_on_logs_counterfactual_and_keeps_koord(monkeypatch):
    """Flaga ON: live verdict NADAL early_bird KOORD (bez zmiany) + dokładnie 1 wpis shadow."""
    calls = []
    monkeypatch.setattr(dispatch_pipeline, "_earlybird_t30_shadow_enabled", lambda: True)
    monkeypatch.setattr(dispatch_pipeline, "_append_earlybird_t30_shadow", lambda e: calls.append(e))
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    ev = _build_order_event(now_utc, raw_mtp_min=70.0)
    res = dispatch_pipeline.assess_order(ev, {}, now=now_utc)

    # Live verdict NIEZMIENIONY:
    assert res.verdict == "KOORD" and "early_bird" in (res.reason or ""), \
        f"shadow ON nie może zmienić live verdict, got {res.verdict}/{res.reason!r}"
    # DOKŁADNIE jeden wpis (max rekurencja 1 — bypass=True nie loguje ponownie):
    assert len(calls) == 1, f"oczekiwano 1 wpis shadow, got {len(calls)}"
    entry = calls[0]
    assert entry["order_id"] == "TEST_EB_SHADOW"
    assert entry["minutes_ahead"] >= 60.0
    # Kontrfaktyk NIE jest early_bird (bypass) — pusta flota → brak PROPOZYCJI:
    assert "early_bird" not in (entry["cf_verdict"] or "")
    assert entry["would_resolve"] is False, \
        f"pusta flota nie powinna dać would_resolve=True, got {entry}"
    assert entry["cf_pool_feasible"] == 0


def test_no_early_bird_no_shadow(monkeypatch):
    """raw=40 (<60): nie early_bird → shadow nie odpala (writer 0×) nawet z flagą ON."""
    calls = []
    monkeypatch.setattr(dispatch_pipeline, "_earlybird_t30_shadow_enabled", lambda: True)
    monkeypatch.setattr(dispatch_pipeline, "_append_earlybird_t30_shadow", lambda e: calls.append(e))
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    ev = _build_order_event(now_utc, raw_mtp_min=40.0)
    res = dispatch_pipeline.assess_order(ev, {}, now=now_utc)
    assert "early_bird" not in (res.reason or "")
    assert calls == [], "poniżej progu early_bird shadow nie powinien się odpalić"
