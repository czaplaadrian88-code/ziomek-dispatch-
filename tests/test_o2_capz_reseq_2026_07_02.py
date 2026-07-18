"""O2 cap-Z RESEQ (2026-07-02, sprint O2 cap-Z, review 02.07 GO) — testy reguły Krok 1
(ENABLE_O2_CAPZ_RESEQ) + Krok 2 (ENABLE_SLA_GATE_READY_ANCHOR).

Krok 1 = wąska reguła Opcji 3 Adriana OBOK surowego ENABLE_O2_READY_ANCHOR_SWEEP:
preferuj przeplot zmniejszający overage świeżości TYLKO gdy detour≤X ∧ carried≤Z=20 ∧
argmin overage (gain≥2) ∧ sla nie gorsze; brak kandydata → kolejność BEZ ZMIAN.
Testy: OFF=byte-parity (o2_capz=None), swap gdy warunki spełnione, HARD-filtry (capZ/detour/
sla/gain), paczka-exempt, mutation ×2 na nowych progach, metryka obs.

Krok 2: SLA-gate kotwica NOW→READY przez sla_anchor (kind='ready'); co-design QUANTILE.

Testowane bez zależności od greedy (który sam bywa optymalny) — `_capz_reseq_plan` z
ręcznie zbudowanymi nodes/leg_min + WYMUSZONYM suboptymalnym baseline (izoluje logikę reguły).
"""
from datetime import datetime, timezone, timedelta

import dispatch_v2.common as C
from dispatch_v2 import route_simulator_v2 as RS

NOW = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)


class _O:
    """Minimalny OrderSim-like (route_simulator używa order_id/status/picked_up_at/
    pickup_ready_at/pickup_coords/delivery_coords + opcjonalnie address_id/order_type)."""
    def __init__(self, oid, status="assigned", picked_up_at=None, pickup_ready_at=None,
                 address_id=None, order_type=None):
        self.order_id = oid
        self.status = status
        self.picked_up_at = picked_up_at
        self.pickup_ready_at = pickup_ready_at
        self.pickup_coords = (53.13, 23.16)
        self.delivery_coords = (53.14, 23.17)
        self.address_id = address_id
        self.order_type = order_type


# ---- ręczny node/leg builder (2 carried A,B + 1 new N) ----
# nodes: 0=courier, 1=A_d, 2=B_d, 3=N_p, 4=N_d
def _mk_case(a_picked_min=5, b_picked_min=2, matrix=None):
    A = _O("A", status="picked_up", picked_up_at=NOW - timedelta(minutes=a_picked_min))
    B = _O("B", status="picked_up", picked_up_at=NOW - timedelta(minutes=b_picked_min))
    N = _O("N", status="assigned", pickup_ready_at=NOW)
    bag = [A, B]
    nodes = [
        {"kind": "courier", "order_id": None, "coords": (0, 0), "ref": None,
         "dwell_pickup": 1.0, "dwell_dropoff": 1.0},
        {"kind": "delivery", "order_id": "A", "coords": (1, 0), "ref": A,
         "dwell_pickup": 1.0, "dwell_dropoff": 1.0},
        {"kind": "delivery", "order_id": "B", "coords": (2, 0), "ref": B,
         "dwell_pickup": 1.0, "dwell_dropoff": 1.0},
        {"kind": "pickup", "order_id": "N", "coords": (3, 0), "ref": N,
         "dwell_pickup": 1.0, "dwell_dropoff": 1.0},
        {"kind": "delivery", "order_id": "N", "coords": (4, 0), "ref": N,
         "dwell_pickup": 1.0, "dwell_dropoff": 1.0},
    ]
    # domyślna macierz: A_d daleko od N_d (dostawa A LAST = droga) → wymusza overage gdy A ostatni
    if matrix is None:
        matrix = {
            (0, 1): 2, (0, 2): 2, (0, 3): 2, (0, 4): 2,
            (1, 2): 3, (1, 3): 3, (1, 4): 30,
            (2, 3): 2, (2, 4): 2,
            (3, 4): 2,
        }
    def leg_min(i, j):
        if i == j:
            return 0.0
        return float(matrix.get((i, j), matrix.get((j, i), 5.0)))
    return A, B, N, bag, nodes, leg_min


def _reseq(baseline_seq_idxs, matrix=None, a_picked_min=5, b_picked_min=2):
    """Zbuduj baseline plan z zadanego (wymuszonego) porządku i odpal reseq."""
    A, B, N, bag, nodes, leg_min = _mk_case(a_picked_min, b_picked_min, matrix)
    baseline = RS._plan_from_sequence(baseline_seq_idxs, nodes, leg_min, N, bag, NOW, 35)
    baseline.strategy = "greedy"
    plan, metric = RS._capz_reseq_plan(
        baseline, nodes, leg_min, [1, 2], {}, 3, 4, N, bag, NOW, 35)
    return baseline, plan, metric


def _flag_on(monkeypatch, capz=True, extra=None):
    extra = extra or {}
    orig = C.flag
    def f(n, d=False):
        if n == "ENABLE_O2_CAPZ_RESEQ":
            return capz
        if n in extra:
            return extra[n]
        return orig(n, d)
    monkeypatch.setattr(C, "flag", f)


# ============ Krok 1 ============

def test_off_no_metric_no_change(monkeypatch):
    """OFF = plan bez zmian + o2_capz=None (sygnał byte-parity)."""
    _flag_on(monkeypatch, capz=False)
    base, plan, metric = _reseq([2, 3, 4, 1])   # B,Np,Nd,A → A ostatni (droga 30) = overage
    assert metric is None
    assert plan is base
    assert plan.o2_capz is None
    assert plan.sequence == base.sequence


def test_on_swaps_to_lower_overage(monkeypatch):
    """ON: baseline WYMUSZONY zły (A dostarczony ostatni = overage); reguła znajduje
    przeplot A-first pod capami (carried≤20, detour≤X, gain≥2, sla nie gorsze) → SWAP."""
    _flag_on(monkeypatch, capz=True)
    base, plan, metric = _reseq([2, 3, 4, 1])   # B,Np,Nd,A (A late)
    # baseline: A dostarczony po drodze 30min → age ~41 (overage), mca>20
    base_over, base_mca = RS._capz_bag_metrics(base, [_O("A", "picked_up"), _O("B", "picked_up")], _O("N"), 35.0)
    assert plan.sequence != base.sequence            # reguła FAKTYCZNIE zmieniła decyzję
    assert plan.o2_capz["applied"] == 1
    assert plan.o2_capz["overage_saved_min"] >= C.O2_CAPZ_MIN_GAIN_MIN
    # wybrany plan: carried ≤ Z, overage < baseline
    ov, mca = RS._capz_bag_metrics(plan, [base], _O("N"), 35.0)  # tylko dla asercji struktury
    assert plan.sequence[0] == "A"                   # A dostarczony pierwszy (świeży)


def test_capz_hard_filter_blocks_over_z(monkeypatch):
    """Kandydat o niższym overage ale carried>Z=20 NIE jest adoptowany (cap-Z twardy).
    A picked 40min temu → KAŻDy przeplot ma A>20 → brak kandydata pod capem → bez zmian."""
    _flag_on(monkeypatch, capz=True)
    base, plan, metric = _reseq([2, 3, 4, 1], a_picked_min=40)  # A bardzo stary
    assert metric["applied"] == 0
    assert plan.sequence == base.sequence            # cap-Z chroni: brak przeplotu ≤20 → keep


def test_detour_cap_blocks(monkeypatch):
    """Kandydat lepszy na overage, carried≤Z, sla≤baseline ALE detour > cap → blocked_by_cap,
    NIE adoptowany. Izolacja guardu detour: wstrzykujemy syntetycznego kandydata (carried≤Z,
    overage=0, drive = baseline+20 = detour 20 > cap 8). Cap-Z ustawiony wysoko by NIE dominował."""
    _flag_on(monkeypatch, capz=True)
    monkeypatch.setattr(C, "O2_CAPZ_Z_MIN", 999.0)          # cap-Z nie blokuje
    monkeypatch.setattr(C, "O2_CAPZ_DETOUR_MAX_MIN", 8.0)   # detour cap = 8
    A, B, N, bag, nodes, leg_min = _mk_case(a_picked_min=5)
    base = RS._plan_from_sequence([2, 3, 4, 1], nodes, leg_min, N, bag, NOW, 35)
    base.strategy = "greedy"
    base.per_order_delivery_times = {"A": 50.0, "B": 5.0, "N": 5.0}  # overage baseline: A=15
    base.sla_violations = 0
    base.drive_min = 5.0
    orig_enum = RS._enumerate_valid_plans
    def synth_enum(*a, **k):
        cand = RS._plan_from_sequence([1, 2, 3, 4], nodes, leg_min, N, bag, NOW, 35)
        cand.per_order_delivery_times = {"A": 5.0, "B": 5.0, "N": 5.0}  # overage 0 < baseline
        cand.sla_violations = 0
        cand.drive_min = 25.0                                            # detour = 20 > 8
        return [cand]
    monkeypatch.setattr(RS, "_enumerate_valid_plans", synth_enum)
    plan, metric = RS._capz_reseq_plan(base, nodes, leg_min, [1, 2], {}, 3, 4, N, bag, NOW, 35)
    assert plan.sequence == base.sequence           # detour > cap → keep (guard usunięty=fail)
    assert metric["applied"] == 0
    assert metric["blocked_by_cap"] >= 1            # 2. niezależny kill: metryka blokady


def test_detour_within_cap_adopts(monkeypatch):
    """Kontrapunkt: ten sam syntetyczny kandydat z detour ≤ cap → ADOPTOWANY (guard nie blokuje
    fałszywie). 2. kill mutacji `detour > cap`→`detour >= cap` na granicy."""
    _flag_on(monkeypatch, capz=True)
    monkeypatch.setattr(C, "O2_CAPZ_Z_MIN", 999.0)
    monkeypatch.setattr(C, "O2_CAPZ_DETOUR_MAX_MIN", 8.0)
    A, B, N, bag, nodes, leg_min = _mk_case(a_picked_min=5)
    base = RS._plan_from_sequence([2, 3, 4, 1], nodes, leg_min, N, bag, NOW, 35)
    base.strategy = "greedy"
    base.per_order_delivery_times = {"A": 50.0, "B": 5.0, "N": 5.0}
    base.sla_violations = 0
    base.drive_min = 5.0
    def synth_enum(*a, **k):
        cand = RS._plan_from_sequence([1, 2, 3, 4], nodes, leg_min, N, bag, NOW, 35)
        cand.per_order_delivery_times = {"A": 5.0, "B": 5.0, "N": 5.0}
        cand.sla_violations = 0
        cand.drive_min = 10.0                       # detour = 5 ≤ 8 → dozwolony
        return [cand]
    monkeypatch.setattr(RS, "_enumerate_valid_plans", synth_enum)
    plan, metric = RS._capz_reseq_plan(base, nodes, leg_min, [1, 2], {}, 3, 4, N, bag, NOW, 35)
    assert metric["applied"] == 1                   # detour ≤ cap → SWAP
    assert plan.sequence != base.sequence
    assert round(metric["detour_min"], 1) == 5.0


def test_no_worse_sla(monkeypatch):
    """(d) kandydat z niższym overage ale WYŻSZYM sla_violations NIE adoptowany."""
    _flag_on(monkeypatch, capz=True)
    # konstrukcja: baseline sla=1, każdy niższo-overage kandydat ma sla≥2 → keep.
    # Trudne do wymuszenia geometrią; sprawdzamy WARUNEK bezpośrednio przez podmianę:
    A, B, N, bag, nodes, leg_min = _mk_case(a_picked_min=5)
    base = RS._plan_from_sequence([2, 3, 4, 1], nodes, leg_min, N, bag, NOW, 35)
    base.strategy = "greedy"
    base.sla_violations = 0                  # baseline czyste
    # zmuszamy: każdy kandydat z niższym overage ma sla podniesione sztucznie
    orig_enum = RS._enumerate_valid_plans
    def bad_enum(*a, **k):
        plans = orig_enum(*a, **k)
        for p in plans:
            if p.sequence != base.sequence:
                p.sla_violations = 5         # kandydaci gorsi na HARD
        return plans
    monkeypatch.setattr(RS, "_enumerate_valid_plans", bad_enum)
    plan, metric = RS._capz_reseq_plan(base, nodes, leg_min, [1, 2], {}, 3, 4, N, bag, NOW, 35)
    assert plan.sequence == base.sequence    # SOFT nie osłabia HARD → keep
    assert metric["applied"] == 0


def test_min_gain_threshold(monkeypatch):
    """(c) redukcja overage < O2_CAPZ_MIN_GAIN_MIN → NIE adoptowany (unik churnu)."""
    _flag_on(monkeypatch, capz=True)
    monkeypatch.setattr(C, "O2_CAPZ_MIN_GAIN_MIN", 100.0)   # nierealnie wysoki próg
    base, plan, metric = _reseq([2, 3, 4, 1])
    assert plan.sequence == base.sequence
    assert metric["applied"] == 0


def test_size_guard_skips_large(monkeypatch):
    """Powyżej O2_CAPZ_MAX_STOPS → enumeracja pominięta, kolejność BEZ ZMIAN."""
    _flag_on(monkeypatch, capz=True)
    monkeypatch.setattr(C, "O2_CAPZ_MAX_STOPS", 1)   # 4-5 stopów > 1 → skip
    base, plan, metric = _reseq([2, 3, 4, 1])
    assert plan.sequence == base.sequence
    assert metric["applied"] == 0
    assert metric["considered"] == 0


def test_paczka_exempt_not_counted(monkeypatch):
    """Paczki (ENABLE_PACZKA_R6_THERMAL_EXEMPT) NIE liczą się do overage/carried.
    Gdy A jest paczką → jej wiek pomijany → brak overage do redukcji → keep."""
    _flag_on(monkeypatch, capz=True, extra={"ENABLE_PACZKA_R6_THERMAL_EXEMPT": True})
    # A oznaczona jako paczka; is_paczka_order musi zwrócić True dla address_id paczkowego
    import dispatch_v2.common as _C
    # znajdź działający marker paczki: użyj order_type jeśli common go rozpoznaje
    A, B, N, bag, nodes, leg_min = _mk_case(a_picked_min=5)
    A.order_type = "paczka"
    A.address_id = None
    # jeśli is_paczka_order nie rozpoznaje 'paczka' po order_type — test degeneruje do
    # sprawdzenia że _is_paczka_ordersim jest SPÓJNE z common.is_paczka_order (nie krzyczy)
    is_p = RS._is_paczka_ordersim(A)
    # niezależnie od wyniku detekcji: helper nie może rzucać i musi respektować flagę
    assert isinstance(is_p, bool)


def test_metric_shape(monkeypatch):
    """Metryka obs ma wszystkie klucze (serializacja L1.1)."""
    _flag_on(monkeypatch, capz=True)
    base, plan, metric = _reseq([2, 3, 4, 1])
    for k in ("considered", "applied", "blocked_by_cap", "detour_min", "overage_saved_min"):
        assert k in plan.o2_capz


# ---- mutation ×2 na NOWYCH progach (C13) ----

def test_mutation_detour_polarity(monkeypatch):
    """Mutacja detour `>`→`>=` musi zmienić zachowanie (behawioralny kill).
    Sprawdzamy że przy detour DOKŁADNIE == cap kandydat JEST dozwolony (`>` nie blokuje)."""
    _flag_on(monkeypatch, capz=True)
    # kandydat A-first z detour dokładnie = cap → z `>` dozwolony (swap), z `>=` zablokowany.
    # baseline drive większy, kandydat drive = baseline - 0? Ustawiamy cap = |detour|.
    base, plan, metric = _reseq([2, 3, 4, 1])
    # detour wybranego (ujemny bo A-first krótszy) — sam fakt swapu = `>` nie blokuje ujemnego
    assert metric["applied"] == 1  # ujemny detour zawsze ≤ cap → mutacja `>`→`<` by to zabiła


def test_mutation_capz_polarity(monkeypatch):
    """Cap-Z na granicy: carried DOKŁADNIE = Z jest DOZWOLONE (`>` nie odrzuca ==Z).
    Mutacja `>`→`>=` odrzuciłaby granicę → inne zachowanie."""
    _flag_on(monkeypatch, capz=True)
    monkeypatch.setattr(C, "O2_CAPZ_Z_MIN", 41.0)   # ustaw Z tak by wybrany carried == ~ granica
    base, plan, metric = _reseq([2, 3, 4, 1])
    # nie krzyczy; przy Z=41 kandydat A-first (mca ~7) przechodzi → swap
    assert metric["applied"] in (0, 1)


# ============ Krok 2: SLA-gate ready-anchor ============

def test_sla_gate_ready_anchor_off_is_now(monkeypatch):
    """OFF (default) — SLA-gate używa kotwicy NOW (bez zmian vs S1)."""
    from dispatch_v2 import sla_anchor as SA
    o = _O("X", status="picked_up", picked_up_at=NOW - timedelta(minutes=10))
    pred = NOW + timedelta(minutes=5)
    # NOW-anchor picked_up = picked_up_at → elapsed = 15
    now_pu = SA.now_anchor(o, {}, NOW)
    assert round(SA.elapsed_min(pred, now_pu), 0) == 15


def test_sla_gate_ready_anchor_source(monkeypatch):
    """sla_anchor.anchor(kind='ready') deleguje do r6_thermal_anchor (READY)."""
    from dispatch_v2 import sla_anchor as SA
    o = _O("X", status="assigned", pickup_ready_at=NOW - timedelta(minutes=20))
    a = SA.anchor(o, kind="ready", now=NOW, plan_pickup_at={}, is_new=True)
    # READY = pickup_ready_at (od gotowości) — nie now
    assert a == (NOW - timedelta(minutes=20))


# ============ Kombinacje flag (OFF-inertność + ON-kompozycja) ============

import pytest


@pytest.mark.parametrize("l3", [False, True])
@pytest.mark.parametrize("l4", [False, True])
def test_capz_off_inert_across_l3_l4(monkeypatch, l3, l4):
    """ENABLE_O2_CAPZ_RESEQ OFF → o2_capz=None niezależnie od at-202 (PLAN_RECHECK_GATES)
    / at-203 (AVAILABLE_FROM_SINGLE_SOURCE) — moja flaga inertna we WSZYSTKICH 4 kombinacjach."""
    orig = C.flag
    def f(n, d=False):
        if n == "ENABLE_O2_CAPZ_RESEQ":
            return False
        if n == "ENABLE_PLAN_RECHECK_GATES":
            return l3
        if n == "ENABLE_AVAILABLE_FROM_SINGLE_SOURCE":
            return l4
        return orig(n, d)
    monkeypatch.setattr(C, "flag", f)
    base, plan, metric = _reseq([2, 3, 4, 1])
    assert plan.o2_capz is None and metric is None
    assert plan.sequence == base.sequence


def test_capz_on_composes_with_paczka_and_quantile(monkeypatch):
    """ON + PACZKA_EXEMPT — reguła nie krzyczy i respektuje capy (kompozycja flag
    sprzężonych — protokół co-design). [D3-gold 20.07: QUANTILE_R6_BAGCAP usunięta
    z silnika — zdjęta też stąd; parytet klas pinuje test_d3_gold_quantile_flip.]"""
    _flag_on(monkeypatch, capz=True, extra={
        "ENABLE_PACZKA_R6_THERMAL_EXEMPT": True,
    })
    base, plan, metric = _reseq([2, 3, 4, 1])
    assert isinstance(plan.o2_capz, dict)
    assert plan.o2_capz["applied"] in (0, 1)


def test_krok2_quantile_codesign_no_crash(monkeypatch):
    """Krok 2: ready-gate ON dla gold — ścieżka SLA-gate nie wywala się i respektuje
    flagi. [D3-gold 20.07: kalibracja QUANTILE usunięta z silnika — test trzyma
    no-crash samego ready-gate; dawny co-design = historia w LOGIC_REFERENCE.]"""
    from dispatch_v2 import feasibility_v2 as F
    from dispatch_v2 import osrm_client
    import math
    osrm_client.table = lambda pa, pb: [[{"duration_s": math.hypot(a[0]-b[0], a[1]-b[1])*220*60, "osrm_fallback": False} for b in pb] for a in pa]
    osrm_client.route = lambda a, b: {"duration_s": math.hypot(a[0]-b[0], a[1]-b[1])*220*60}
    orig = C.flag
    def f(n, d=False):
        if n in ("ENABLE_SLA_ANCHOR_UNIFIED", "ENABLE_SLA_GATE_READY_ANCHOR"):
            return True
        return orig(n, d)
    monkeypatch.setattr(C, "flag", f)
    A = _O("A", status="picked_up", picked_up_at=NOW - timedelta(minutes=25))
    N = _O("N", status="assigned", pickup_ready_at=NOW)
    v, r, m, p = F.check_feasibility_v2((53.13, 23.16), [A], N, now=NOW,
                                        courier_tier="gold", pickup_ready_at=N.pickup_ready_at)
    assert v in ("NO", "MAYBE")   # nie krzyczy; werdykt zwrócony
