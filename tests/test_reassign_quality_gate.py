"""Krok 1 (2026-06-28): gate JAKOŚCI przerzutu (gradient) w reassignment_forward_shadow.
Duch-przerzut TYLKO gdy „na pewno lepiej": ramię1 ratunek (obecny po czasie, nowy na czas),
ramię2 oszczędność (obecny na czas, nowy ≥BIG_SAVE min wcześniej). Pozycja A i B usable (nie fikcja)."""
import os
from datetime import datetime, timezone, timedelta
from dispatch_v2.tools import reassignment_forward_shadow as R

BASE = datetime(2026, 6, 28, 14, 0, tzinfo=timezone.utc)


class _Plan:
    def __init__(self, pred, pick):
        self.predicted_delivered_at = pred
        self.pickup_at = pick


class _Cand:
    def __init__(self, cid, deliver_min, pick_min=0):
        self.courier_id = cid
        self.plan = _Plan({"O1": BASE + timedelta(minutes=deliver_min)},
                          {"O1": BASE + timedelta(minutes=pick_min)})


def _gate(a, b, a_pos, b_pos, holder="123", bcid="370", b_bag=None):
    return R._quality_gate(a, b, "O1", a_pos, b_pos, holder, bcid, b_bag=b_bag)


import contextlib


@contextlib.contextmanager
def _env(k, v):
    old = os.environ.get(k)
    os.environ[k] = v
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = old


# ---- _usable_pos: prefiks last_, gps, store/interp = OK; fikcja = NIE ----
def test_usable_pos_prefix_and_fiction():
    for ok in ("gps", "last_picked_up_pickup", "last_assigned_pickup",
               "last_picked_up_interp", "last_delivered", "store", "interp"):
        assert R._usable_pos(ok) is True, ok
    for bad in ("no_gps", "none", "pre_shift", "pin", "", None):
        assert R._usable_pos(bad) is False, bad


# ---- RAMIĘ 1: obecny po czasie (R6 40>35), nowy na czas (25) → przerzut ----
def test_arm1_rescue_late_holder_ontime_new():
    a = _Cand("123", 40); b = _Cand("370", 25)
    g = _gate(a, b, "gps", "last_picked_up_interp")
    assert g["quality_reassign"] is True
    assert g["a_late"] is True and g["b_late"] is False
    assert "ratunek" in g["quality_reason"]


# ---- RAMIĘ 1: obecny INFEASIBLE (a_cand=None) + nowy na czas → ratunek ----
def test_arm1_infeasible_holder():
    b = _Cand("370", 25)
    g = _gate(None, b, "gps", "gps")
    assert g["quality_reassign"] is True and g["a_late"] is True


# ---- RAMIĘ 2: oba na czas, nowy ≥8 min wcześniej → oszczędność ----
def test_arm2_savings_big():
    a = _Cand("123", 20); b = _Cand("370", 10)   # save=10 ≥ 8
    g = _gate(a, b, "gps", "last_assigned_pickup")
    assert g["quality_reassign"] is True
    assert "oszczędność" in g["quality_reason"]
    assert g["save_min"] == 10.0


# ---- BRAK: oba na czas, oszczędność za mała (<8) → bez przerzutu ----
def test_no_fire_small_saving():
    a = _Cand("123", 20); b = _Cand("370", 15)   # save=5 < 8
    g = _gate(a, b, "gps", "gps")
    assert g["quality_reassign"] is False


# ---- BRAK: pozycja nowego = fikcja (no_gps) → bez przerzutu mimo ratunku ----
def test_no_fire_fiction_position():
    a = _Cand("123", 40); b = _Cand("370", 25)
    g = _gate(a, b, "gps", "no_gps")
    assert g["quality_pos_ok"] is False
    assert g["quality_reassign"] is False


# ---- BRAK: obaj po czasie (nowy też spóźniony) → ratunek nie odpala ----
def test_no_fire_both_late():
    a = _Cand("123", 40); b = _Cand("370", 38)
    g = _gate(a, b, "gps", "gps")
    assert g["a_late"] is True and g["b_late"] is True
    assert g["quality_reassign"] is False


# ---- BRAK: nowy == obecny (ten sam cid) → bez przerzutu ----
def test_no_fire_same_courier():
    a = _Cand("123", 40); b = _Cand("123", 25)
    g = _gate(a, b, "gps", "gps", holder="123", bcid="123")
    assert g["quality_reassign"] is False


# ---- flaga steruje obliczaniem (ON≠OFF) ----
def test_quality_flag_env_toggle():
    old = os.environ.get(R.QUALITY_FLAG)
    try:
        os.environ[R.QUALITY_FLAG] = "0"
        assert R._quality_on() is False
        os.environ[R.QUALITY_FLAG] = "1"
        assert R._quality_on() is True
    finally:
        if old is None:
            os.environ.pop(R.QUALITY_FLAG, None)
        else:
            os.environ[R.QUALITY_FLAG] = old


# ---- BIG_SAVE_MIN env-tunable ----
def test_big_save_env_tunable():
    a = _Cand("123", 20); b = _Cand("370", 14)   # save=6
    old = os.environ.get(R.QUALITY_BIG_SAVE_KEY)
    try:
        os.environ[R.QUALITY_BIG_SAVE_KEY] = "5"   # próg 5 → 6 wystarczy
        g = _gate(a, b, "gps", "gps")
        assert g["quality_reassign"] is True
        os.environ[R.QUALITY_BIG_SAVE_KEY] = "8"   # próg 8 → 6 za mało
        g = _gate(a, b, "gps", "gps")
        assert g["quality_reassign"] is False
    finally:
        if old is None:
            os.environ.pop(R.QUALITY_BIG_SAVE_KEY, None)
        else:
            os.environ[R.QUALITY_BIG_SAVE_KEY] = old


# ---- RESERVE-AWARE: oszczędność = TYLKO bundling (B busy); wolnego nie palimy (Adrian 28.06) ----
def test_oszcz_bundling_only_suppresses_free_courier():
    a = _Cand("123", 20); b = _Cand("370", 10)   # oszczędność save=10 (oba na czas)
    with _env(R.OSZCZ_BUNDLING_ONLY_FLAG, "1"):
        g_free = _gate(a, b, "gps", "gps", b_bag=0)      # B WOLNY → wygaszone
        assert g_free["quality_reassign"] is False
        assert "WOLNY" in g_free["quality_reason"]
        g_busy = _gate(a, b, "gps", "gps", b_bag=2)      # B ZAJĘTY (po drodze) → odpala
        assert g_busy["quality_reassign"] is True


def test_oszcz_bundling_only_off_fires_free():
    a = _Cand("123", 20); b = _Cand("370", 10)
    with _env(R.OSZCZ_BUNDLING_ONLY_FLAG, "0"):
        g = _gate(a, b, "gps", "gps", b_bag=0)           # flaga OFF → stare zachowanie (odpala)
        assert g["quality_reassign"] is True


def test_ratunek_free_courier_fires_even_under_bundling_only():
    a = _Cand("123", 40); b = _Cand("370", 25)           # RATUNEK (A spóźniony)
    with _env(R.OSZCZ_BUNDLING_ONLY_FLAG, "1"):
        g = _gate(a, b, "gps", "gps", b_bag=0)           # B wolny ALE ratunek → odpala
        assert g["quality_reassign"] is True
        assert "ratunek" in g["quality_reason"]
