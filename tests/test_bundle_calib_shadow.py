#!/usr/bin/env python3
"""Testy dla tools/bundle_calib_shadow.py — READ-ONLY shadow z obiektywem
skalibrowanym na GOTOWOŚĆ jedzenia (ready) zamiast symulowanego pickup_at.

Testy syntetyczne (zero realnych danych): regex deadline z `uwagi`, kontrakt
leksykograficznego objektywu, warunek bundle_improved, poprawność permutacji.
"""
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2.tools import bundle_calib_shadow as B  # noqa: E402

WARSAW = ZoneInfo("Europe/Warsaw")
_DAY = datetime(2026, 6, 25, 12, 0, tzinfo=WARSAW)


def _hhmm(dt):
    return dt.astimezone(WARSAW).strftime("%H:%M") if dt else None


# ── REGEX DEADLINE (wymóg zadania: 3-4 testy na realnych uwagach) ───────────
def test_deadline_czasowka_na_14_no_minutes():
    # 'Czasówka na 14, dania...' → 14:00
    dt = B._parse_deadline('Czasówka na 14, dania "zostawić na ochronie"', _DAY)
    assert _hhmm(dt) == "14:00"


def test_deadline_czasowka_na_1430():
    # 'Czasówka na 14:30, ... PIZZA' → 14:30
    dt = B._parse_deadline('Czasówka na 14:30, Pani prosi o telefon - PIZZA', _DAY)
    assert _hhmm(dt) == "14:30"


def test_deadline_none_for_plain_uwagi():
    assert B._parse_deadline('Obiad', _DAY) is None
    assert B._parse_deadline('Dania', _DAY) is None
    assert B._parse_deadline('Pizza 60cm', _DAY) is None
    assert B._parse_deadline(None, _DAY) is None
    assert B._parse_deadline('', _DAY) is None


def test_deadline_dot_separator_and_uppercase():
    # 'na 14.00' i 'CZASOWKA NA 16.30' (bez ogonka, kropka)
    assert _hhmm(B._parse_deadline('czasowka na 14.00', _DAY)) == "14:00"
    assert _hhmm(B._parse_deadline('DOSTAWA CZASOWKA NA 16.30 U KLIENTA', _DAY)) == "16:30"


def test_deadline_czasowka_no_na_prefix():
    # 'Czasówka 14:00' (bez "na") → 14:00
    assert _hhmm(B._parse_deadline('Czasówka 14:00', _DAY)) == "14:00"


def test_deadline_timezone_is_warsaw():
    # 14:00 Warsaw = 12:00 UTC; deadline trzymany w UTC
    dt = B._parse_deadline('Czasówka na 14', _DAY)
    assert dt.tzinfo == timezone.utc
    assert dt.isoformat() == "2026-06-25T12:00:00+00:00"


def test_deadline_rejects_out_of_range_hour():
    # 'czasówka na 99' → odrzuć (hh poza 0-23)
    assert B._parse_deadline('czasówka na 99', _DAY) is None


# ── PERMUTACJE: pickup-before-delivery, niesione=sam dropoff ────────────────
def test_all_valid_perms_respect_pickup_before_delivery():
    mine = {
        "A": {"status": "assigned"},
        "B": {"status": "assigned"},
    }
    perms = B._all_valid_perms(mine)
    assert len(perms) == 6  # 4!/(2*2)=6 poprawnych przeplotów dla 2 zleceń
    for seq in perms:
        seen = set()
        for s in seq:
            if s["type"] == "pickup":
                seen.add(s["order_id"])
            else:
                assert s["order_id"] in seen, f"dropoff przed pickup w {seq}"


def test_all_valid_perms_carried_dropoff_only():
    # niesiony C (picked_up) → tylko dropoff, brak pickup
    mine = {
        "A": {"status": "assigned"},
        "C": {"status": "picked_up"},
    }
    perms = B._all_valid_perms(mine)
    assert perms, "powinny istnieć poprawne sekwencje"
    for seq in perms:
        types_c = [s["type"] for s in seq if s["order_id"] == "C"]
        assert types_c == ["dropoff"], f"niesiony C ma mieć tylko dropoff: {seq}"


# ── OBIEKTYW LEKSYKOGRAFICZNY + bundle_improved ─────────────────────────────
def test_bundle_improved_when_r6_strictly_lower():
    m_served = {"r6_ready": 3, "czas_late": 10.0, "finish_in_min": 90.0}
    m_calib = {"r6_ready": 1, "czas_late": 10.0, "finish_in_min": 91.0}
    assert B._bundle_improved(m_served, m_calib) is True


def test_bundle_improved_when_czas_late_drops_ge_2min_same_r6():
    m_served = {"r6_ready": 2, "czas_late": 20.0, "finish_in_min": 90.0}
    m_calib = {"r6_ready": 2, "czas_late": 5.0, "finish_in_min": 90.0}
    assert B._bundle_improved(m_served, m_calib) is True


def test_not_improved_when_r6_worse():
    # świeższy odbiór ale więcej breachy R6 → NIE improved (c1 fail)
    m_served = {"r6_ready": 1, "czas_late": 20.0, "finish_in_min": 90.0}
    m_calib = {"r6_ready": 2, "czas_late": 0.0, "finish_in_min": 90.0}
    assert B._bundle_improved(m_served, m_calib) is False


def test_not_improved_when_finish_blows_up():
    # mniej spóźnień ale kończy o >2min później → NIE improved (c3 fail)
    m_served = {"r6_ready": 2, "czas_late": 20.0, "finish_in_min": 90.0}
    m_calib = {"r6_ready": 2, "czas_late": 5.0, "finish_in_min": 95.0}
    assert B._bundle_improved(m_served, m_calib) is False


def test_not_improved_when_no_real_gain():
    # identyczne metryki → brak konkretnego zysku (c2 fail)
    m = {"r6_ready": 2, "czas_late": 10.0, "finish_in_min": 90.0}
    assert B._bundle_improved(dict(m), dict(m)) is False


# ── walk: ready = czas_kuriera (NIE pickup_at) ──────────────────────────────
def test_walk_uses_czas_kuriera_as_ready_for_r6():
    # 1 zlecenie assigned. ready=czas_kuriera. Dostawa daleko po ready → R6 breach.
    now = datetime(2026, 6, 25, 10, 0, tzinfo=timezone.utc)
    mine = {
        "A": {
            "status": "assigned",
            "czas_kuriera_warsaw": "2026-06-25T12:00:00+02:00",  # =10:00 UTC ready
            "picked_up_at": None,
            "pickup_coords": [53.13, 23.16],
            "delivery_coords": [53.10, 23.20],
        }
    }
    pos = (53.14, 23.15)
    seq = B._stops_from_mine(mine)
    idx, M = B._osrm_matrix([seq], mine, pos)
    assert idx is not None
    m = B._walk_calib(seq, mine, pos, now, idx, M, {"A": None})
    assert m is not None
    # carry_ready = delivered - ready(=czas_kuriera 10:00 UTC), powinien być dodatni
    assert "A" in m["carry_ready"]
    # r6_ready liczone od ready, nie od symulowanego pickup_at — kontrakt obecny
    assert isinstance(m["r6_ready"], int)


# ── best-under-Z (Opcja 3 Adriana 2026-06-25): cap świeżości carried ────────
def test_zkey_clean_string():
    assert B._zkey(20.0) == "20"
    assert B._zkey(32.0) == "32"
    assert B._zkey(35.0) == "35"


def test_max_carried_age_only_counts_carried():
    mine = {"A": {"status": "assigned"}, "C": {"status": "picked_up"}}
    m = {"carry_ready": {"A": 99.0, "C": 25.0}}
    # liczy TYLKO niesione (C) — ignoruje A (assigned), choć A ma większy wiek
    assert B._max_carried_age(m, mine) == 25.0
    # worek bez niesionego → 0.0 (cap nie wiąże)
    assert B._max_carried_age({"carry_ready": {"A": 50.0}}, {"A": {"status": "assigned"}}) == 0.0
    # brak metryk → 0.0
    assert B._max_carried_age(None, mine) == 0.0
    assert B._max_carried_age({}, mine) == 0.0


def _carried_plus_new_bag(now):
    """Worek: niesiony C (picked_up ~30 min temu) + nowy A (pickup+dropoff)."""
    mine = {
        "C": {
            "status": "picked_up",
            "czas_kuriera_warsaw": "2026-06-25T13:30:00+02:00",  # ready 11:30 UTC
            "picked_up_at": "2026-06-25T11:30:00+00:00",
            "pickup_coords": [53.132, 23.168],
            "delivery_coords": [53.100, 23.210],
        },
        "A": {
            "status": "assigned",
            "czas_kuriera_warsaw": None,
            "picked_up_at": None,
            "pickup_coords": [53.145, 23.150],
            "delivery_coords": [53.120, 23.190],
        },
    }
    pos = (53.140, 23.160)
    seq0 = B._stops_from_mine(mine)
    idx, M = B._osrm_matrix([seq0], mine, pos)
    return mine, pos, idx, M


def test_calib_route_returns_under_z_with_cap_respected():
    now = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)
    mine, pos, idx, M = _carried_plus_new_bag(now)
    assert idx is not None, "OSRM matrix wymagane (localhost:5001)"
    deadlines = {"A": None, "C": None}
    best_seq, best_m, n, mode, under_z = B._calib_route(
        mine, pos, now, idx, M, deadlines, None, None, None)
    # klucze Z obecne dla każdego capa
    assert set(under_z.keys()) == {B._zkey(z) for z in B.Z_CAPS}
    # INWARIANT: każdy niepusty under_z[Z] ma max wiek niesionego ≤ Z
    for zk, v in under_z.items():
        if v is not None:
            assert v["max_carried_age"] <= float(zk) + 1e-6, \
                f"under_z[{zk}] łamie cap: carried={v['max_carried_age']} > {zk}"
            assert "seq" in v and "drive_min" in v and "o2" in v


def test_under_z_unbounded_equals_calib(monkeypatch):
    # cap olbrzymi → under_z[huge] = globalny argmin O2 = CALIB (selekcja niezmieniona)
    monkeypatch.setattr(B, "Z_CAPS", [20.0, 9999.0])
    now = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)
    mine, pos, idx, M = _carried_plus_new_bag(now)
    assert idx is not None
    deadlines = {"A": None, "C": None}
    best_seq, best_m, n, mode, under_z = B._calib_route(
        mine, pos, now, idx, M, deadlines, None, None, None)
    assert under_z["9999"] is not None
    # przeplot pod capem ∞ MUSI być identyczny z CALIB → dowód że dodatkowa
    # pętla under_z nie zmienia selekcji CALIB (parytet)
    assert under_z["9999"]["seq"] == B._oid_seq(best_seq)


def test_build_row_emits_under_z_fields():
    # _build_row musi serializować under_z + max wiek carried served/calib do jsonl
    now = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)
    mine, pos, idx, M = _carried_plus_new_bag(now)
    assert idx is not None
    deadlines = {"A": None, "C": None}
    served = B._stops_from_mine(mine)
    m_served = B._walk_calib(served, mine, pos, now, idx, M, deadlines)
    best_seq, m_calib, n, mode, under_z = B._calib_route(
        mine, pos, now, idx, M, deadlines, None, None, None)
    row = B._build_row("99", ["A", "C"], "sig", mine, pos, now, served, best_seq,
                       m_served, m_calib, deadlines, mode, n, under_z)
    assert "under_z" in row
    assert "served_max_carried_age" in row and "calib_max_carried_age" in row
    assert set(row["under_z"].keys()) == {B._zkey(z) for z in B.Z_CAPS}


# ── B1/L6.B1 (2026-07-01): parytet instrument↔dźwignia O2 ──────────────────
def test_overage_cap_equals_engine_dial():
    """Instrument mierzy DOKŁADNIE ten cap, który flip O2 przełącza.

    R6_MAX_MIN MUSI być importem dialu common.O2_OVERAGE_CAP_MIN, nie literałem —
    literał = dryf przy strojeniu dialu (np. tryb niedoboru 02.07). Misdiagnoza
    „tier-aware 40" obalona pomiarem 01.07: 40 = BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN
    (cap selekcji w eskalacji-3), nie termika worka; termiczna R6 płaska
    (doktryna Adriana 2026-05-10). Łapie tylko dryf W KODZIE — env-frozen
    rozjazd między serwisami łapie checklist drop-inów (wzorzec #9).
    """
    from dispatch_v2 import common as C
    assert B.R6_MAX_MIN == C.O2_OVERAGE_CAP_MIN


def test_overage_recompute_from_carry_ready_matches_walk():
    """Wierność korpusu: overage w wierszu == hinge z logowanego carry_ready.

    Gwarantuje, że przeliczenia post-hoc (np. wariant capu 40 obok primary 35)
    z carry_ready odtwarzają overage bez re-symulacji — fundament reguły
    „nie mutuj kolektora w środku okna, przeliczaj obok".
    """
    now = datetime(2026, 6, 25, 10, 0, tzinfo=timezone.utc)
    mine = {
        "A": {
            "status": "assigned",
            "czas_kuriera_warsaw": "2026-06-25T11:00:00+02:00",  # ready 09:00 UTC
            "picked_up_at": None,
            "pickup_coords": [53.13, 23.16],
            "delivery_coords": [53.10, 23.20],
        },
        "B": {
            "status": "picked_up",
            "czas_kuriera_warsaw": "2026-06-25T10:30:00+02:00",  # ready 08:30 UTC
            "picked_up_at": "2026-06-25T09:40:00+00:00",
            "pickup_coords": [53.12, 23.14],
            "delivery_coords": [53.11, 23.22],
        },
    }
    pos = (53.14, 23.15)
    seq = B._stops_from_mine(mine)
    idx, M = B._osrm_matrix([seq], mine, pos)
    assert idx is not None
    m = B._walk_calib(seq, mine, pos, now, idx, M, {"A": None, "B": None})
    assert m is not None and m["carry_ready"]
    recomputed = sum(max(0.0, age - B.R6_MAX_MIN) for age in m["carry_ready"].values())
    # carry_ready logowane z zaokrągleniem 0.1/oid → tolerancja 0.1*len
    assert abs(recomputed - m["overage"]) <= 0.1 * len(m["carry_ready"]) + 1e-6


# --- re-collect λ=0 (2026-07-03, checklist bug4-logger_raport §4) ---------------

def test_build_row_serializes_lambda_czas(monkeypatch):
    """Provenancja korpusu: każdy wiersz niesie λ, którą liczono selekcję CALIB —
    strażnik skażenia przy mieszaniu plików λ=1.5 / λ=0."""
    monkeypatch.setattr(B, "LAMBDA_CZAS", 0.0)
    mine = {
        "A": {"status": "assigned", "pickup_coords": [53.12, 23.14],
              "delivery_coords": [53.11, 23.22], "czas_kuriera_warsaw": None,
              "uwagi": ""},
        "B": {"status": "assigned", "pickup_coords": [53.13, 23.15],
              "delivery_coords": [53.10, 23.20], "czas_kuriera_warsaw": None,
              "uwagi": ""},
    }
    seq = [{"type": "pickup", "order_id": "A"}, {"type": "dropoff", "order_id": "A"},
           {"type": "pickup", "order_id": "B"}, {"type": "dropoff", "order_id": "B"}]
    m = {"r6_ready": 1.0, "czas_late": 0.0, "finish_in_min": 30.0,
         "overage": 0.0, "drive_min": 20.0}
    now = datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc)
    row = B._build_row("99", ["A", "B"], "sig", mine, (53.14, 23.15), now,
                       seq, seq, dict(m), dict(m), {"A": None, "B": None},
                       "brute", 4, {})
    assert row["lambda_czas"] == 0.0


def test_out_and_state_paths_env_override(monkeypatch):
    """Re-collect λ=0 wymaga ROZŁĄCZNYCH plików (output+state) per λ — env
    BUNDLE_CALIB_OUT_JSONL / BUNDLE_CALIB_STATE_PATH musi przekierować oba."""
    import importlib
    monkeypatch.setenv("BUNDLE_CALIB_OUT_JSONL", "/tmp/test_bc_l0.jsonl")
    monkeypatch.setenv("BUNDLE_CALIB_STATE_PATH", "/tmp/test_bc_state_l0.json")
    try:
        importlib.reload(B)
        assert B.OUT_JSONL == "/tmp/test_bc_l0.jsonl"
        assert B.STATE_PATH == "/tmp/test_bc_state_l0.json"
    finally:
        monkeypatch.delenv("BUNDLE_CALIB_OUT_JSONL")
        monkeypatch.delenv("BUNDLE_CALIB_STATE_PATH")
        importlib.reload(B)
    assert B.OUT_JSONL.endswith("/bundle_calib_shadow.jsonl")
    assert B.STATE_PATH.endswith("/bundle_calib_shadow_state.json")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
