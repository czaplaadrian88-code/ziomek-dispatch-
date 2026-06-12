"""BUNDLE-05 re-test (2026-06-12, zlecenie Adriana): ostrzejsze testy 3 bramek
geometrycznych — V327 cross-quadrant mult, V326 wave veto, intra-rest gap.

KOREKTA STANU vs audyt 03.06: bramki NIE są wyłączone — env-defaulty ON,
zero override'ów w environ shadow (zweryfikowane na PID live 12.06), korpus
2153 PROPOSE: V327 mult strzelił na 724 kandydatach (143 zwycięzców, sign-guard
Z-02 675×), wave veto 1825×, intra-rest reject 1×. Ten plik utwardza kontrakt:

  1. apply_bundle_score_mult — pełna macierz brzegowa (sentinel −1e9, zero,
     dokumentacja inwersji bez guarda = bug, który Z-02 łata).
  2. Wave veto (mirror logiki inline) — granica progu STRICT >, wymaga
     bonus>0, brak koordów / pusty bag-pda = brak veta, zero crashy.
  3. Intra-rest gap (mirror) — tz-naive stringi, nieparsowalne timestampy,
     pickupy out-of-order, równe timestampy.
  4. Kontrakt „bramki uzbrojone domyślnie" — zmiana env-defaultu na OFF
     ma krzyczeć w testach (audyt 03.06 myślał, że są OFF — nie zgadujemy
     drugi raz).
"""
from datetime import datetime, timedelta, timezone

import pytest

from dispatch_v2 import common as C
from dispatch_v2.common import apply_bundle_score_mult
from dispatch_v2.osrm_client import haversine


# ───────────────── 1. apply_bundle_score_mult (V327 ×0.1) ─────────────────

@pytest.mark.parametrize("score,mult,guard,exp_score,exp_guarded", [
    # guard ON: dodatni score → mult działa
    (100.0, 0.1, True, 10.0, False),
    (0.01, 0.1, True, 0.001, False),
    # guard ON: ujemny/zero → mult POMINIĘTY (inwersja kary zablokowana)
    (-80.0, 0.1, True, -80.0, True),
    (0.0, 0.1, True, 0.0, True),
    (-1e9, 0.1, True, -1e9, True),          # sentinel nietykalny
    (-1000000030.0, 0.7, True, -1000000030.0, True),
    # mult=1.0 → no-op niezależnie od wszystkiego
    (-80.0, 1.0, True, -80.0, False),
    (100.0, 1.0, False, 100.0, False),
    # adjacent 0.7 na dodatnim
    (50.0, 0.7, True, 35.0, False),
])
def test_mult_edge_matrix(score, mult, guard, exp_score, exp_guarded):
    out, guarded = apply_bundle_score_mult(score, mult, guard)
    assert out == pytest.approx(exp_score)
    assert guarded is exp_guarded


def test_mult_without_guard_documents_inversion_bug():
    """Bez guarda (stan sprzed Z-02): −80×0.1=−8 BIJE −50 — kara cross-quadrant
    ODWRÓCONA. Ten test dokumentuje, czemu guard musi zostać ON."""
    inverted, _ = apply_bundle_score_mult(-80.0, 0.1, sign_guard_on=False)
    competitor = -50.0
    assert inverted > competitor  # patologia: cross-quadrant wygrywa przez karę
    guarded, was_guarded = apply_bundle_score_mult(-80.0, 0.1, sign_guard_on=True)
    assert guarded < competitor and was_guarded  # guard przywraca porządek


def test_mult_guard_default_from_module():
    """sign_guard_on=None → ENABLE_V327_MULT_SIGN_GUARD (kanon ETAP4)."""
    out, guarded = apply_bundle_score_mult(-10.0, 0.1, sign_guard_on=None)
    if C.decision_flag("ENABLE_V327_MULT_SIGN_GUARD") or C.ENABLE_V327_MULT_SIGN_GUARD:
        assert out == -10.0 and guarded is True
    else:  # pragma: no cover — guard wyłączony = świadoma decyzja, nie default
        assert out == pytest.approx(-1.0)


# ──────────────── 2. V326 wave veto (mirror logiki inline) ────────────────

def _wave_veto(bonus_continuation, plan_pda, bag_raw, new_pickup,
               threshold=None):
    """Mirror bloku V326 STEP 3 z dispatch_pipeline (1:1 semantyka)."""
    thr = threshold if threshold is not None else C.V326_WAVE_VETO_KM_THRESHOLD
    veto = False
    km = None
    if (C.ENABLE_V326_WAVE_GEOMETRIC_VETO and bonus_continuation > 0
            and plan_pda is not None and bag_raw):
        bag_oids = {str(b.get("order_id")) for b in bag_raw if b.get("order_id")}
        bag_pda = [(o, t) for o, t in plan_pda.items() if str(o) in bag_oids]
        if bag_pda:
            last_oid = max(bag_pda, key=lambda x: x[1])[0]
            last_drop = next((b.get("delivery_coords") for b in bag_raw
                              if str(b.get("order_id")) == str(last_oid)), None)
            if last_drop and new_pickup:
                km = haversine(tuple(last_drop), tuple(new_pickup))
                if km > thr:
                    veto = True
                    bonus_continuation = 0.0
    return veto, km, bonus_continuation


_T0 = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)
_CENTRUM = (53.1325, 23.1688)
_DALEKO = (53.1700, 23.2200)  # ~5 km od centrum


def test_wave_veto_fires_over_threshold():
    bag = [{"order_id": "B1", "delivery_coords": _DALEKO}]
    veto, km, bonus = _wave_veto(20.0, {"B1": _T0}, bag, _CENTRUM)
    assert veto is True and bonus == 0.0 and km > 3.0


def test_wave_veto_requires_positive_bonus():
    """Bonus=0 → veto NIE strzela (nie ma czego wetować) — to jest dokładnie
    luka BUNDLE-03 (najgorsze worki bez bonusów przechodzą bez kary), którą
    łata fix_c_additive_pen_shadow z 12.06."""
    bag = [{"order_id": "B1", "delivery_coords": _DALEKO}]
    veto, km, _ = _wave_veto(0.0, {"B1": _T0}, bag, _CENTRUM)
    assert veto is False and km is None


def test_wave_veto_threshold_strict_boundary():
    # punkt dokładnie na progu: km == thr → NIE veto (strict >)
    bag = [{"order_id": "B1", "delivery_coords": _DALEKO}]
    km_real = haversine(_DALEKO, _CENTRUM)
    veto_eq, _, _ = _wave_veto(20.0, {"B1": _T0}, bag, _CENTRUM,
                               threshold=km_real)
    veto_below, _, _ = _wave_veto(20.0, {"B1": _T0}, bag, _CENTRUM,
                                  threshold=km_real - 0.01)
    assert veto_eq is False and veto_below is True


def test_wave_veto_missing_coords_and_empty_pda_safe():
    bag_nocoords = [{"order_id": "B1", "delivery_coords": None}]
    veto, km, bonus = _wave_veto(20.0, {"B1": _T0}, bag_nocoords, _CENTRUM)
    assert veto is False and bonus == 20.0
    # pda nie zawiera oidów bagu → bag_pda puste → brak veta, zero crash
    bag = [{"order_id": "B1", "delivery_coords": _DALEKO}]
    veto2, _, _ = _wave_veto(20.0, {"INNY": _T0}, bag, _CENTRUM)
    assert veto2 is False


def test_wave_veto_picks_chronologically_last_drop():
    """Veto liczy km od OSTATNIEGO dropu wg predicted_delivered_at —
    nie od pierwszego z listy."""
    blisko = (53.1330, 23.1690)  # ~60 m od centrum
    bag = [
        {"order_id": "B_dalej", "delivery_coords": _DALEKO},
        {"order_id": "B_blisko", "delivery_coords": blisko},
    ]
    pda = {"B_dalej": _T0, "B_blisko": _T0 + timedelta(minutes=9)}
    veto, km, _ = _wave_veto(20.0, pda, bag, _CENTRUM)
    assert veto is False and km < 0.2  # ostatni = blisko → brak veta


# ─────────────── 3. intra-rest gap (mirror — przypadki wrogie) ───────────────

def _intra_check(plan_pickup_at, new_oid, restaurant, bag_raw):
    """Mirror logiki inline (jak w test_intra_restaurant_gap, skrócony)."""
    rest_by_oid = {new_oid: restaurant} if new_oid else {}
    for b in bag_raw or []:
        oid = str(b.get("order_id") or "")
        if oid:
            rest_by_oid[oid] = b.get("restaurant")
    pickups = []
    for oid, pat in (plan_pickup_at or {}).items():
        try:
            dt = datetime.fromisoformat(str(pat)) if isinstance(pat, str) else pat
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            pickups.append((dt, str(oid)))
        except Exception:
            continue
    pickups.sort(key=lambda x: x[0])
    reject = False
    gap_max = 0.0
    for i in range(len(pickups) - 1):
        t1, o1 = pickups[i]
        t2, o2 = pickups[i + 1]
        r1, r2 = rest_by_oid.get(o1), rest_by_oid.get(o2)
        if r1 is None or r2 is None or r1 != r2:
            continue
        gap = (t2 - t1).total_seconds() / 60.0
        gap_max = max(gap_max, gap)
        if gap > C.MAX_INTRA_RESTAURANT_GAP_MIN:
            reject = True
    return gap_max, reject


def test_intra_tz_naive_strings_normalized():
    """Panel/plan potrafi oddać naive-ISO — kod normalizuje do UTC."""
    pa = {"O1": "2026-06-12T14:02:00", "O2": "2026-06-12T14:15:00"}
    gap, reject = _intra_check(pa, "O2", "Raj", [{"order_id": "O1", "restaurant": "Raj"}])
    assert gap == 13.0 and reject is True


def test_intra_unparsable_timestamp_skipped_not_crash():
    pa = {"O1": _T0, "O2": "GARBAGE-NOT-A-DATE", "O3": _T0 + timedelta(minutes=2)}
    gap, reject = _intra_check(pa, "O3", "Raj", [
        {"order_id": "O1", "restaurant": "Raj"},
        {"order_id": "O2", "restaurant": "Raj"},
    ])
    assert gap == 2.0 and reject is False  # O2 wypada, O1→O3 = 2 min


def test_intra_out_of_order_dict_is_sorted_chronologically():
    """Kolejność insertu w dict NIE jest kolejnością czasową — sort decyduje."""
    pa = {"LATE": _T0 + timedelta(minutes=20), "EARLY": _T0,
          "MID": _T0 + timedelta(minutes=4)}
    bag = [{"order_id": "LATE", "restaurant": "Raj"},
           {"order_id": "EARLY", "restaurant": "Raj"}]
    gap, reject = _intra_check(pa, "MID", "Raj", bag)
    # pary chronologiczne: EARLY→MID 4 min (OK), MID→LATE 16 min (REJECT)
    assert gap == 16.0 and reject is True


def test_intra_equal_timestamps_zero_gap_no_reject():
    pa = {"O1": _T0, "O2": _T0}
    gap, reject = _intra_check(pa, "O2", "Raj", [{"order_id": "O1", "restaurant": "Raj"}])
    assert gap == 0.0 and reject is False


def test_intra_different_restaurants_interleaved_not_paired():
    """Raj → Pierożek → Raj: sąsiednie pary chronologiczne mają różne
    restauracje → bramka NIE strzela (łapie tylko SĄSIEDNIE pary tej samej —
    znana, świadoma granica mechanizmu; dokumentujemy, nie 'naprawiamy')."""
    pa = {"R1": _T0, "P1": _T0 + timedelta(minutes=6),
          "R2": _T0 + timedelta(minutes=12)}
    bag = [{"order_id": "R1", "restaurant": "Raj"},
           {"order_id": "P1", "restaurant": "Pierożek"}]
    gap, reject = _intra_check(pa, "R2", "Raj", bag)
    assert reject is False  # R1→R2 12 min NIE jest parą sąsiednią


# ──────────── 4. kontrakt: bramki uzbrojone domyślnie (BUNDLE-05) ────────────

def test_gates_armed_by_default():
    """Audyt 03.06 twierdził 'bramki OFF' — stan realny 12.06: env-defaulty ON,
    zero override w environ shadow. Zmiana defaultu na OFF = świadoma decyzja
    z ACK, nie cichy edit — ten test ma wtedy krzyczeć."""
    assert C.ENABLE_V327_BUG_FIXES_BUNDLE is True
    assert C.ENABLE_V326_WAVE_GEOMETRIC_VETO is True
    assert C.ENABLE_INTRA_RESTAURANT_GAP_LIMIT is True
    assert C.MAX_INTRA_RESTAURANT_GAP_MIN == 5.0
    assert C.V326_WAVE_VETO_KM_THRESHOLD == 3.0
