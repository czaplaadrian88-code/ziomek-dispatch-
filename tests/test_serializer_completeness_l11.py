"""L1.1 (Faza 3 audytu spójności, 2026-07-01) — kompletność serializera.

ROOT R3-E4 (audyt 30.06, backing B07, ground-truth 858 decyzji):
_AUTO_PROP_PREFIXES = allowlist 35 prefiksów BEZ kontroli kompletności ->
38 kluczy `metrics` ginelo z shadow_decisions.jsonl (0/858), w tym 14 HARD
(sla_violations detail / eta_source / pickup_dist_km / r6_* / c2_* / d2_*)
-> kalibracja O2 / replay / oracle slepe na wewnetrzne decyzje HARD-bramek.

KONTRAKT (ZIOMEK_ARCHITECTURE kontrakt 5 "prawda przyrzadow" +
ZIOMEK_INVARIANTS): kazdy klucz `metrics` serializowany ALBO jawnie
wykluczony z powodem w _METRICS_EXCLUDE. Ten plik = runtime-inwariant
kontraktu (czerwony test przy probie powrotu do cichych dziur).
"""
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import shadow_dispatcher as SD  # noqa: E402


@dataclass
class _MockCand:
    metrics: dict = field(default_factory=dict)
    plan: object = None
    courier_id: int = 123
    name: str = "Test"
    score: float = 100.0
    feasibility_verdict: str = "FEASIBLE"
    feasibility_reason: str = ""
    best_effort: bool = False


# 14 kluczy HARD z audytu B07 §4 (ginely 0/858 przed L1.1) + 5 PROV + próbka
# DIAG/SOFT — wszystkie MUSZA docierac do ledgera po odwroceniu na deny-liste.
_AUDIT_VANISHED_KEYS = {
    # HARD — rodzina SLA-detail
    "sla_violations": [{"order_id": "1", "elapsed_min": 40.0, "over_sla_by_min": 5.0}],
    "sla_violations_blocking_count": 1,
    "sla_violations_pre_existing": [],
    # HARD — prowenancja ETA / feasibility-internal
    "eta_source": "no_gps_fallback",
    "pickup_dist_km": 3.7,
    "r6_gold4_gate_recovered": True,
    "r6_paczka_exempt_oids": ["483001"],
    "r6_soft_zone_active": True,
    "d2_stale_schedule_soft": True,
    "d2_soft_penalty": -10.0,
    "c2_passes": True,
    "c2_violations_count": 0,
    "c2_max_elapsed_min": 12.0,
    "c2_per_order_data_available": True,
    # PROV — progi/tier
    "sla_minutes_used": 35,
    "cs_tier_label": "gold",
    "cs_tier_bag": "gold",
    "shift_start_min": 0.0,
    "shift_remaining_min": 240.0,
    # DIAG — scarcity V328 / inwariant
    "fallback_strategy": "v328_heuristic",
    "mass_fail_ratio": 0.8,
    "inv_feasibility_first_violation": False,
    # SOFT — telemetria
    "wave_bonus": 2.0,
    "r1_violation_km": 0.4,
}


def test_audit_vanished_keys_now_reach_location_a():
    """Kazdy klucz z listy audytu (ginacy przed L1.1) trafia do LOCATION A."""
    cand = _MockCand(metrics=dict(_AUDIT_VANISHED_KEYS))
    out = SD._serialize_candidate(cand)
    missing = [k for k in _AUDIT_VANISHED_KEYS if k not in out]
    assert not missing, f"klucze nadal gina z LOCATION A: {missing}"


def test_arbitrary_future_key_reaches_ledger():
    """Kontrakt 5: NOWA metryka widoczna od urodzenia — bez rejestracji
    prefiksu/klucza (koniec mechanizmu 36. prefiksu)."""
    base: dict = {}
    SD._propagate_prefixed_metrics(base, {"totally_new_metric_2099": 42})
    assert base.get("totally_new_metric_2099") == 42


def test_exclude_entries_documented_and_skipped():
    """Kazdy wpis deny-listy ma niepusty POWOD i faktycznie nie przechodzi."""
    assert SD._METRICS_EXCLUDE, "deny-lista nie moze byc pusta-placeholderem"
    for k, reason in SD._METRICS_EXCLUDE.items():
        assert isinstance(reason, str) and reason.strip(), (
            f"wykluczenie {k} bez powodu — kontrakt 5 wymaga jawnego powodu")
    base: dict = {}
    SD._propagate_prefixed_metrics(base, {k: 1 for k in SD._METRICS_EXCLUDE})
    assert not base, f"wykluczone klucze przeszly: {sorted(base)}"


def test_explicit_read_still_wins():
    """Klucz juz obecny w base (explicit-read serializera) nie jest nadpisany."""
    base = {"km_to_pickup": 9.9}
    SD._propagate_prefixed_metrics(base, {"km_to_pickup": 1.1})
    assert base["km_to_pickup"] == 9.9


def test_json_safety_no_crash_on_exotic_values():
    """append_jsonl propaguje TypeError — mechanizm MUSI sanityzowac
    datetime/set/obiekty, inaczej przyszly writer wywali zapis decyzji."""
    class _Obj:
        def __repr__(self):
            return "OBJ"

    cand = _MockCand(metrics={
        "future_dt": datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        "future_set": {"a", "b"},
        "future_obj": _Obj(),
        "future_nested": {"dt": datetime(2026, 7, 1, tzinfo=timezone.utc),
                          "tup": (1, 2)},
    })
    out = SD._serialize_candidate(cand)
    encoded = json.dumps(out, ensure_ascii=False)  # nie moze rzucic
    assert "2026-07-01T12:00:00+00:00" in encoded
    assert out["future_obj"] == "OBJ"
    assert sorted(out["future_set"]) == ["a", "b"]


def test_twin_a_b_share_single_mechanism():
    """Parytet bliznakow z konstrukcji (kontrakt 3): LOCATION A i B woluja
    TEN SAM helper — 1 def + >=2 call-site'y w zrodle."""
    import inspect
    src = inspect.getsource(SD)
    assert src.count("_propagate_prefixed_metrics(") >= 3
