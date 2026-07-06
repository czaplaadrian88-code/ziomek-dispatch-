"""K10 (refaktor, 2026-07-06): bramki wejściowe w core.gates — parytet 1:1 z inline.

Charakteryzujące zachowanie sprzed przenosin (zielone także na starym kodzie —
golden literały): SKIP/no_pickup_geocode, KOORD/early_bird z formatem reason,
RAW-anchor (fix 2026-05-07), bypass, kontrfaktyk głębokości 1.
"""
from datetime import datetime, timedelta, timezone

import dispatch_v2.dispatch_pipeline as dp
from dispatch_v2.core import gates

_NOW = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)


def _ev(**over):
    ev = {
        "order_id": "K10T",
        "restaurant": "Testownia",
        "delivery_address": "Testowa 1",
        "pickup_coords": [53.13, 23.16],
        "delivery_coords": [53.14, 23.17],
    }
    ev.update(over)
    return ev


# ── geokod-defense ───────────────────────────────────────────────────────────

def test_geocode_defense_skip_on_missing_and_sentinel():
    for bad in (None, [0.0, 0.0], (0.0, 0.0)):
        r = gates.geocode_defense(_ev(pickup_coords=bad), order_id="K10T",
                                  restaurant="Testownia", delivery_address="Testowa 1")
        assert r is not None, f"coords={bad!r} MUSI dać SKIP"
        assert (r.verdict, r.reason) == ("SKIP", "no_pickup_geocode")
        assert r.pool_total_count == 0 and r.pool_feasible_count == 0
        assert r.best is None and r.candidates == []


def test_geocode_defense_pass_on_good_coords():
    assert gates.geocode_defense(_ev(), order_id="K10T", restaurant="T",
                                 delivery_address="A") is None


def test_geocode_defense_parity_przez_wrapper():
    # pełna ścieżka wrappera: identyczny kształt jak przed przenosinami
    r = dp.assess_order(_ev(pickup_coords=None), {}, None, _NOW)
    assert (r.verdict, r.reason) == ("SKIP", "no_pickup_geocode")


# ── early-bird ───────────────────────────────────────────────────────────────

def _eb_args(**over):
    kw = dict(pickup_at=None, order_id="K10T", restaurant="Testownia",
              delivery_address="Testowa 1", pending_queue=None,
              demand_context=None, bypass=False)
    kw.update(over)
    return kw


def test_early_bird_koord_reason_format(monkeypatch):
    monkeypatch.setattr(dp, "_early_bird_threshold_min", lambda: 60.0)
    pu = (_NOW + timedelta(minutes=101)).astimezone(timezone.utc)
    ev = _ev(pickup_at_warsaw=pu.isoformat())
    r = gates.early_bird(ev, {}, None, _NOW, **_eb_args())
    assert r is not None and r.verdict == "KOORD"
    assert r.reason == "early_bird (101 min ahead)", r.reason


def test_early_bird_below_threshold_passes(monkeypatch):
    monkeypatch.setattr(dp, "_early_bird_threshold_min", lambda: 60.0)
    pu = (_NOW + timedelta(minutes=30)).astimezone(timezone.utc)
    ev = _ev(pickup_at_warsaw=pu.isoformat())
    assert gates.early_bird(ev, {}, None, _NOW, **_eb_args()) is None


def test_early_bird_bypass_passes(monkeypatch):
    monkeypatch.setattr(dp, "_early_bird_threshold_min", lambda: 60.0)
    pu = (_NOW + timedelta(minutes=200)).astimezone(timezone.utc)
    ev = _ev(pickup_at_warsaw=pu.isoformat())
    assert gates.early_bird(ev, {}, None, _NOW, **_eb_args(bypass=True)) is None


def test_early_bird_raw_anchor_wins_over_extended(monkeypatch):
    """Fix 2026-05-07: próg z RAW pickup_at_warsaw, NIE z extended pickup_at."""
    monkeypatch.setattr(dp, "_early_bird_threshold_min", lambda: 60.0)
    raw = (_NOW + timedelta(minutes=20)).astimezone(timezone.utc)      # blisko → NIE early
    extended = _NOW + timedelta(minutes=120)                            # daleko (czas_kuriera)
    ev = _ev(pickup_at_warsaw=raw.isoformat())
    r = gates.early_bird(ev, {}, None, _NOW, **_eb_args(pickup_at=extended))
    assert r is None, "RAW anchor 20 min < 60 → bramka NIE może KOORD-ować po extended"


def test_early_bird_kontrfaktyk_glebokosc_1(monkeypatch):
    """Shadow ON → dokładnie 1 wpis i dokładnie 1 wywołanie kontrfaktyczne impl
    (bypass=True w środku wyłącza bramkę — brak rekurencji w głąb)."""
    monkeypatch.setattr(dp, "_early_bird_threshold_min", lambda: 60.0)
    monkeypatch.setattr(dp, "_earlybird_t30_shadow_enabled", lambda: True)
    appended = []
    monkeypatch.setattr(dp, "_append_earlybird_t30_shadow", lambda rec: appended.append(rec))
    calls = {"n": 0}
    real_impl = dp._assess_order_impl

    def counting_impl(*a, **kw):
        calls["n"] += 1
        assert kw.get("_bypass_early_bird") is True, "kontrfaktyk MUSI iść z bypass=True"
        return real_impl(*a, **kw)

    monkeypatch.setattr(dp, "_assess_order_impl", counting_impl)
    pu = (_NOW + timedelta(minutes=90)).astimezone(timezone.utc)
    ev = _ev(pickup_at_warsaw=pu.isoformat())
    r = gates.early_bird(ev, {}, None, _NOW, **_eb_args())
    assert r is not None and r.verdict == "KOORD"
    assert calls["n"] == 1, "dokładnie 1 kontrfaktyk (głębokość 1)"
    assert len(appended) == 1
    rec = appended[0]
    assert rec["order_id"] == "K10T" and "would_resolve" in rec
    assert rec["minutes_ahead"] == 90.0


def test_early_bird_parity_przez_wrapper(monkeypatch):
    """Pełna ścieżka wrappera daje KOORD/early_bird jak przed przenosinami."""
    pu = (_NOW + timedelta(minutes=180)).astimezone(timezone.utc)
    ev = _ev(pickup_at_warsaw=pu.isoformat())
    r = dp.assess_order(ev, {}, None, _NOW)
    assert r.verdict == "KOORD" and r.reason.startswith("early_bird (")
