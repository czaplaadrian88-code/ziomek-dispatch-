"""BEHAWIORALNE strażniki HARD-bramek `check_feasibility_v2` (Lane C — guard-teatr,
audyt 2.0 L09, protokół C13).

Kontekst: audyt L09 („siła strażników") pokazał, że część strażników HARD-bramek
feasibility to TEATR — pinują TEKST źródła (`inspect.getsource` + `"token" in src`),
a nie ZACHOWANIE. Skutek: refaktor semantyko-zachowawczy fałszywie alarmuje, a
realna inwersja przechodzi zielona. Konkrety z L09:
  - `test_feasibility_bag_filter_honors_override` = string-match → mutacja bag-cap
    `>=`→`>` PRZEŻYŁA wszystkie testy behawioralne (łapał ją tylko string-match).
  - R6 per-order HARD reject strzeżony CIENKO (1 test), realnie maskowany przez
    bramkę SLA (ten sam próg 35 min) — patrz L-TEATR-1.

Ten plik = strażniki BEHAWIORALNE: wołają `check_feasibility_v2` z realnie
skonstruowanym workiem/kandydatem i asertują WERDYKT (nie tekst źródła). Cel
C13: ≥2 niezależne „kills" per bramka (mutacja bramki → co najmniej 2 testy PADAJĄ).

Flagi czytane EFEKTYWNIE i PINOWANE monkeypatchem (wzorzec #9 + stabilność na
flipy at-202/203 04.07): każdy test ustawia stan flag przez `common.load_flags`
i atrybuty `common`, więc nie zależy od globalnego stanu procesu.

Luki, których obecne HARD-bramki NIE egzekwują behawioralnie, są markowane
`xfail(strict=False)` z tagiem `L-TEATR-<n>` (wzór xfail-ratchet z L0) — regresja
zostaje ZIELONA (xfail ≠ fail); zdjęcie xfail = zadanie fali SERIAL (fix U ŹRÓDŁA).

Standalone-safe: proper pytest module (bez module-level sys.exit).
"""
from datetime import datetime, timezone, timedelta

import pytest

from dispatch_v2.feasibility_v2 import check_feasibility_v2
from dispatch_v2 import feasibility_v2 as F
from dispatch_v2.route_simulator_v2 import OrderSim
from dispatch_v2 import route_simulator_v2 as rs
from dispatch_v2 import common as C


# ──────────────────────────────────────────────────────────────────────────────
# Infrastruktura: mock OSRM + pinowanie flag (deterministyczne, flip-odporne)
# ──────────────────────────────────────────────────────────────────────────────
class _FakeMatrix:
    """osrm_client.table stub — każda komórka duration_s konfigurowalna."""
    def __init__(self, duration_s):
        self.duration_s = duration_s

    def __call__(self, points_a, points_b):
        n = len(points_a)
        row = lambda: [{"duration_s": self.duration_s, "osrm_fallback": False}
                       for _ in range(n)]
        return [row() for _ in range(n)]


class _FakeHaversine:
    """osrm_client.haversine stub — stały dystans (km)."""
    def __init__(self, km):
        self.km = km

    def __call__(self, a, b):
        return self.km


def _mock_osrm(duration_s=60, haversine_km=2.0):
    # feasibility_v2.osrm_client i route_simulator_v2.osrm_client = ten sam singleton
    rs.osrm_client.table = _FakeMatrix(duration_s)
    rs.osrm_client.haversine = _FakeHaversine(haversine_km)


@pytest.fixture(autouse=True)
def _pin_flags(monkeypatch):
    """PIN stanu flag/konfiguracji istotnych dla HARD-bramek — test niezależny od
    globalnego procesu i przyszłych flipów (at-202/203 04.07):
      - V3.25 schedule-hardening OFF: bez shift_end nie chcemy `v325_NO_ACTIVE_SHIFT`
        maskującego testowaną bramkę (jak w test_feasibility_c3),
      - flags.json → kontrolowany dict (bag-cap + tier-cap sterowane per test).
    """
    monkeypatch.setattr(C, "ENABLE_V325_SCHEDULE_HARDENING", False, raising=False)
    # Domyślny bagaż flag; poszczególne testy nadpisują wpisy przez _set_flags.
    _base = {}
    monkeypatch.setattr(C, "load_flags", lambda: dict(_base))
    # udostępnij mutowalny dict testom
    _pin_flags.base = _base
    return _base


def _set_flags(monkeypatch, **kv):
    """Ustaw efektywny stan flags.json na czas testu (pinowany, hot-read)."""
    d = dict(kv)
    monkeypatch.setattr(C, "load_flags", lambda: dict(d))


def _new_order(oid="NEW", pickup=(53.13, 23.15), delivery=(53.14, 23.16),
               ready=None, status="assigned"):
    return OrderSim(
        order_id=oid,
        pickup_coords=pickup,
        delivery_coords=delivery,
        status=status,
        pickup_ready_at=ready,
    )


def _bag_item(oid, picked_up_ago_min=None, now=None, status="assigned"):
    pu = None
    if picked_up_ago_min is not None and now is not None:
        pu = now - timedelta(minutes=picked_up_ago_min)
        status = "picked_up"
    return OrderSim(
        order_id=oid,
        pickup_coords=(53.12, 23.14),
        delivery_coords=(53.15, 23.17),
        status=status,
        picked_up_at=pu,
        pickup_ready_at=(now if now is not None else None),
    )


_NOW = datetime(2026, 4, 18, 17, 0, tzinfo=timezone.utc)


# ══════════════════════════════════════════════════════════════════════════════
# BRAMKA 1 — bag sanity cap (`if len(bag) >= _bag_cap`) — off-by-one
# L09: mutacja `>=`→`>` PRZEŻYŁA wszystkie testy behawioralne. Tu ją ZABIJAMY.
# ══════════════════════════════════════════════════════════════════════════════
def test_bagcap_exactly_at_cap_rejects(monkeypatch):
    """KILL #1 dla `>=`→`>`: worek DOKŁADNIE na progu → NO bag_full.

    Mutacja `>` daje `cap > cap == False` → PRZEPUSZCZA → verdict != NO/bag_full
    → test PADA (mutant zabity)."""
    _mock_osrm()
    _set_flags(monkeypatch, MAX_BAG_SANITY_CAP=3)
    bag = [_bag_item(f"B{i}", now=_NOW) for i in range(3)]  # len == cap
    verdict, reason, metrics, _ = check_feasibility_v2(
        courier_pos=(53.13, 23.15), bag=bag, new_order=_new_order(ready=_NOW), now=_NOW,
    )
    assert verdict == "NO", f"bag==cap musi być NO; got {verdict}/{reason}"
    assert reason.startswith("bag_full"), f"oczekiwano bag_full; got {reason}"
    assert metrics.get("bag_size_before") == 3


def test_bagcap_over_cap_rejects(monkeypatch):
    """KILL #2 dla `>=`→`>` (i innych osłabień progu): worek PONAD cap → NO."""
    _mock_osrm()
    _set_flags(monkeypatch, MAX_BAG_SANITY_CAP=2)
    bag = [_bag_item(f"B{i}", now=_NOW) for i in range(3)]  # 3 > cap 2
    verdict, reason, _, _ = check_feasibility_v2(
        courier_pos=(53.13, 23.15), bag=bag, new_order=_new_order(ready=_NOW), now=_NOW,
    )
    assert verdict == "NO" and reason.startswith("bag_full"), (
        f"bag>cap musi być bag_full NO; got {verdict}/{reason}")


def test_bagcap_below_cap_not_bagfull(monkeypatch):
    """KILL dla mutacji rozluźniającej w drugą stronę (`>=`→`==`/stała True):
    worek PONIŻEJ cap NIE może być odrzucony jako bag_full."""
    _mock_osrm()
    _set_flags(monkeypatch, MAX_BAG_SANITY_CAP=5)
    bag = [_bag_item(f"B{i}", now=_NOW) for i in range(2)]  # 2 < cap 5
    _, reason, _, _ = check_feasibility_v2(
        courier_pos=(53.13, 23.15), bag=bag, new_order=_new_order(ready=_NOW), now=_NOW,
    )
    assert not reason.startswith("bag_full"), (
        f"bag<cap NIE może być bag_full; got {reason}")


def test_bagcap_honors_flag_override(monkeypatch):
    """Behawioralny odpowiednik `test_feasibility_bag_filter_honors_override`
    (który był czystym string-matchem): TEN SAM worek (len==8) przechodzi filtr
    bag_full przy cap=12, a NIE przechodzi przy cap=8 — asercja WERDYKTU."""
    _mock_osrm()
    bag = [_bag_item(f"B{i}", now=_NOW) for i in range(8)]
    no = _new_order(ready=_NOW)
    # cap 8: len(bag)==8 >= 8 → bag_full
    _set_flags(monkeypatch, MAX_BAG_SANITY_CAP=8)
    v8, r8, _, _ = check_feasibility_v2(courier_pos=(53.13, 23.15), bag=bag,
                                        new_order=no, now=_NOW)
    assert v8 == "NO" and r8.startswith("bag_full"), f"cap8: {v8}/{r8}"
    # cap 12: len(bag)==8 < 12 → NIE bag_full (może paść z innego powodu, ale nie tego)
    _set_flags(monkeypatch, MAX_BAG_SANITY_CAP=12)
    _, r12, _, _ = check_feasibility_v2(courier_pos=(53.13, 23.15), bag=bag,
                                        new_order=no, now=_NOW)
    assert not r12.startswith("bag_full"), f"cap12 nie powinien bag_full; got {r12}"


# ══════════════════════════════════════════════════════════════════════════════
# BRAMKA 2 — pickup_too_far (`if pickup_dist_km > _pickup_reach_km()`)
# L09: mutacja kierunku killed hard. Dwa NIEZALEŻNE kille (far→NO, close→NOT NO).
# ══════════════════════════════════════════════════════════════════════════════
def test_pickup_far_rejects(monkeypatch):
    """KILL #1 dla `>`→`<`: odbiór 99 km → NO pickup_too_far."""
    _mock_osrm(haversine_km=99.0)
    _set_flags(monkeypatch, MAX_BAG_SANITY_CAP=8)
    verdict, reason, _, _ = check_feasibility_v2(
        courier_pos=(50.0, 20.0), bag=[], new_order=_new_order(ready=_NOW), now=_NOW,
    )
    assert verdict == "NO" and reason.startswith("pickup_too_far"), (
        f"99km musi być pickup_too_far NO; got {verdict}/{reason}")


def test_pickup_close_not_rejected(monkeypatch):
    """KILL #2 dla `>`→`<`: odbiór 2 km NIE może być pickup_too_far.

    Pod mutacją `<` warunek `2 < 15 == True` → BŁĘDNIE odrzuca → test PADA."""
    _mock_osrm(haversine_km=2.0)
    _set_flags(monkeypatch, MAX_BAG_SANITY_CAP=8)
    _, reason, _, _ = check_feasibility_v2(
        courier_pos=(53.13, 23.15), bag=[], new_order=_new_order(ready=_NOW), now=_NOW,
    )
    assert not reason.startswith("pickup_too_far"), (
        f"2km NIE może być pickup_too_far; got {reason}")


def test_pickup_reach_honors_flag(monkeypatch):
    """Reach cap z flags.json steruje bramką (nie zahardkodowane 15):
    12 km przechodzi przy domyślnym 15, a NIE przy zaostrzeniu do 10."""
    _mock_osrm(haversine_km=12.0)
    _set_flags(monkeypatch, MAX_BAG_SANITY_CAP=8)  # reach domyślny 15
    _, r_default, _, _ = check_feasibility_v2(
        courier_pos=(53.0, 23.0), bag=[], new_order=_new_order(ready=_NOW), now=_NOW)
    assert not r_default.startswith("pickup_too_far"), f"12<15: {r_default}"
    _set_flags(monkeypatch, MAX_BAG_SANITY_CAP=8, MAX_PICKUP_REACH_KM=10.0)
    v, r_strict, _, _ = check_feasibility_v2(
        courier_pos=(53.0, 23.0), bag=[], new_order=_new_order(ready=_NOW), now=_NOW)
    assert v == "NO" and r_strict.startswith("pickup_too_far"), f"12>10: {v}/{r_strict}"


# ══════════════════════════════════════════════════════════════════════════════
# BRAMKA 3 — hard_tier_bag_cap (`bag_after > _hard_cap`, flaga ON)
# ══════════════════════════════════════════════════════════════════════════════
def test_hard_tier_cap_rejects_when_flag_on(monkeypatch):
    """KILL #1: flaga ON + bag_after > tier cap → NO hard_tier_bag_cap."""
    _mock_osrm()
    # tier 'new' cap default = 4 (HARD_TIER_BAG_CAP_DEFAULT / mapa); ustaw jawnie
    monkeypatch.setattr(C, "HARD_TIER_BAG_CAP", {"gold": 6, "std": 5, "slow": 4, "new": 4},
                        raising=False)
    monkeypatch.setattr(C, "HARD_TIER_BAG_CAP_DEFAULT", 6, raising=False)
    _set_flags(monkeypatch, MAX_BAG_SANITY_CAP=12, ENABLE_HARD_TIER_BAG_CAP=True)
    bag = [_bag_item(f"B{i}", now=_NOW) for i in range(4)]  # bag_after = 5 > 4 (slow)
    verdict, reason, metrics, _ = check_feasibility_v2(
        courier_pos=(53.13, 23.15), bag=bag, new_order=_new_order(ready=_NOW),
        now=_NOW, courier_tier="slow",
    )
    assert verdict == "NO" and reason.startswith("hard_tier_bag_cap"), (
        f"slow bag_after 5>4 z flagą ON → NO; got {verdict}/{reason}")
    assert metrics.get("would_hard_cap") is True


def test_hard_tier_cap_inert_when_flag_off(monkeypatch):
    """KILL #2 (kill-switch): flaga OFF → BRAK odrzucenia hard_tier_bag_cap
    (metryka `would_hard_cap` liczona ZAWSZE, reject warunkowy)."""
    _mock_osrm()
    monkeypatch.setattr(C, "HARD_TIER_BAG_CAP", {"gold": 6, "std": 5, "slow": 4, "new": 4},
                        raising=False)
    monkeypatch.setattr(C, "HARD_TIER_BAG_CAP_DEFAULT", 6, raising=False)
    _set_flags(monkeypatch, MAX_BAG_SANITY_CAP=12, ENABLE_HARD_TIER_BAG_CAP=False)
    bag = [_bag_item(f"B{i}", now=_NOW) for i in range(4)]  # bag_after 5 > 4 (slow)
    _, reason, metrics, _ = check_feasibility_v2(
        courier_pos=(53.13, 23.15), bag=bag, new_order=_new_order(ready=_NOW),
        now=_NOW, courier_tier="slow",
    )
    assert not reason.startswith("hard_tier_bag_cap"), (
        f"flaga OFF nie może odrzucać; got {reason}")
    assert metrics.get("would_hard_cap") is True, "shadow metryka liczona nawet OFF"


# ══════════════════════════════════════════════════════════════════════════════
# BRAMKA 4 — R6 per-order carried-age HARD (thermal anchor = ready_at)
# ══════════════════════════════════════════════════════════════════════════════
def test_r6_carried_age_isolated_hard_reject(monkeypatch):
    """KILL dla `if r6_per_order_violations`→`if False and ...`:
    kotwica termiczna = `pickup_ready_at`. Jedzenie gotowe 30 min temu + krótkie
    legi → bag_time (od gotowości) > 35, ALE elapsed-od-teraz < 35 (SLA fine).
    → R6 per-order odrzuca SAM (SLA nie maskuje). Reason MUSI zawierać R6_per_order.
    """
    _mock_osrm(duration_s=60)  # 1-min legi
    _set_flags(monkeypatch, MAX_BAG_SANITY_CAP=8)
    ready_past = _NOW - timedelta(minutes=40)  # jedzenie czeka od 40 min
    new = _new_order(oid="NEW", ready=ready_past, status="assigned")
    verdict, reason, metrics, _ = check_feasibility_v2(
        courier_pos=(53.13, 23.15), bag=[], new_order=new, now=_NOW,
    )
    assert verdict == "NO", f"R6 carried-age >35 od gotowości → NO; got {verdict}/{reason}"
    assert "R6_per_order" in reason, (
        f"oczekiwano R6-isolated reject (nie SLA); got {reason} "
        f"[r6_bt={metrics.get('r6_max_bag_time_min')}]")


def test_r6_fresh_food_passes(monkeypatch):
    """KILL #2 dla mutacji zawsze-odrzucaj: świeże jedzenie (ready≈now) + krótkie
    legi → R6 NIE odrzuca (bag_time < 35). Verdict nie może być R6 NO."""
    _mock_osrm(duration_s=60)
    _set_flags(monkeypatch, MAX_BAG_SANITY_CAP=8)
    new = _new_order(oid="NEW", ready=_NOW, status="assigned")
    _, reason, _, _ = check_feasibility_v2(
        courier_pos=(53.13, 23.15), bag=[], new_order=new, now=_NOW,
    )
    assert "R6_per_order" not in reason, f"świeże jedzenie nie może być R6 reject; got {reason}"


# ══════════════════════════════════════════════════════════════════════════════
# BRAMKA 5 — SLA violation (elapsed > sla_minutes)
# ══════════════════════════════════════════════════════════════════════════════
def test_sla_violation_carried_reject(monkeypatch):
    """KILL dla osłabienia progu SLA: worek niesiony 40 min + długie legi →
    elapsed > 35 → NO (SLA lub R6, oba na 35). Dwa niezależne testy 35-progu."""
    _mock_osrm(duration_s=600)  # 10-min legi → długa trasa
    _set_flags(monkeypatch, MAX_BAG_SANITY_CAP=8)
    bag = [_bag_item("B1", picked_up_ago_min=40, now=_NOW)]
    new = _new_order(oid="NEW", ready=_NOW)
    verdict, reason, _, _ = check_feasibility_v2(
        courier_pos=(53.0, 23.0), bag=bag, new_order=new, now=_NOW,
    )
    assert verdict == "NO", f"carried 40min + długie legi → NO; got {verdict}/{reason}"
    assert ("sla_violation" in reason) or ("R6" in reason), (
        f"oczekiwano SLA/R6 rejection; got {reason}")


def test_sla_short_route_passes(monkeypatch):
    """KILL #2: krótka trasa świeżego solo → brak SLA violation (nie NO z SLA)."""
    _mock_osrm(duration_s=60)
    _set_flags(monkeypatch, MAX_BAG_SANITY_CAP=8)
    new = _new_order(oid="NEW", ready=_NOW)
    _, reason, _, _ = check_feasibility_v2(
        courier_pos=(53.13, 23.15), bag=[], new_order=new, now=_NOW,
    )
    assert "sla_violation" not in reason, f"krótka świeża trasa nie SLA; got {reason}"


# ══════════════════════════════════════════════════════════════════════════════
# LUKI (xfail-ratchet) — HARD-bramki bez EGZEKWOWANIA behawioralnego u źródła.
# Zdjęcie xfail = zadanie fali SERIAL (fix U ŹRÓDŁA). Regresja ZOSTAJE ZIELONA.
# ══════════════════════════════════════════════════════════════════════════════
# S1 (2026-07-02) ZDJĘŁO xfail L-TEATR-1/2. Konsolidacja 35-min HARD do jednego
# źródła (sla_anchor.py) z JAWNĄ kotwicą + metryka obs `sla_anchor_source` (flaga
# ENABLE_SLA_ANCHOR_UNIFIED). De-maskowanie robimy OBSERWABILNOŚCIĄ, NIE zmianą
# reason: reason bramki SLA/R6 KARMI decyzję downstream (dispatch_pipeline:1247/1324
# `r.startswith("sla_violation"/"R6_per_order")` → _feas_carry_readmit/_blind_shadow),
# więc reorder R6↔SLA byłby zmianą DECYZJI (niedozwolone bez replayu+ACK). Zamiast
# tego: naruszenie kotwicy READY (R6) i NOW (SLA) jest NIEZALEŻNIE widoczne w metryce
# → każda bramka killable osobnym testem (patrz też tests/test_sla_anchor_unified.py).
def test_r6_ready_breach_visible_under_unified_even_when_sla_masks_reason(monkeypatch):
    """L-TEATR-1 ZDJĘTE (obserwabilność). Assigned-not-picked, ready 40 min temu,
    długie legi: łamie OBIE kotwice. Reason zostaje SLA (nie zmieniamy decyzji), ALE
    `sla_anchor_source.ready_breach_oids` niesie R6/ready-breach = R6 NIE zamaskowany
    w obserwabilności. Flaga ON pinowana."""
    _mock_osrm(duration_s=600)
    _set_flags(monkeypatch, MAX_BAG_SANITY_CAP=8, ENABLE_SLA_ANCHOR_UNIFIED=True)
    ready_past = _NOW - timedelta(minutes=40)
    new = _new_order(oid="NEW", ready=ready_past, status="assigned")
    verdict, reason, metrics, _ = check_feasibility_v2(
        courier_pos=(53.0, 23.0), bag=[], new_order=new, now=_NOW,
    )
    assert verdict == "NO", f"{reason}"
    src = metrics.get("sla_anchor_source")
    assert isinstance(src, dict), "metryka sla_anchor_source pod ON obowiązkowa"
    assert "NEW" in src["ready_breach_oids"], (
        f"R6/ready-breach zamaskowany w obserwabilności; got {src}")


def test_sla_only_boundary_kills_threshold_under_unified(monkeypatch):
    """L-TEATR-2 ZDJĘTE. SLA-only: picked_up 40 min temu, R6 per-order NIE dotyczy
    niesionego, bypass pre-existing WYŁĄCZONY → reason MUSI być czyste sla_violation.
    To izoluje próg SLA (mutacja DEFAULT_SLA_MINUTES 35→999 → reason≠sla_violation →
    test PADA → mutant KILLED niezależnie od R6). Flaga ON pinowana."""
    _mock_osrm(duration_s=60)
    _set_flags(monkeypatch, MAX_BAG_SANITY_CAP=8, ENABLE_SLA_ANCHOR_UNIFIED=True)
    monkeypatch.setattr(C, "ENABLE_SLA_PREEXISTING_BYPASS", False, raising=False)
    bag = [_bag_item("B1", picked_up_ago_min=40, now=_NOW)]
    new = _new_order(oid="NEW", ready=_NOW)
    _, reason, metrics, _ = check_feasibility_v2(
        courier_pos=(53.13, 23.15), bag=bag, new_order=new, now=_NOW,
    )
    assert reason.startswith("sla_violation"), f"oczekiwano czystego SLA; got {reason}"
    src = metrics.get("sla_anchor_source")
    assert isinstance(src, dict) and src["now_breach_oids"], f"SLA/now-breach obs; {src}"
