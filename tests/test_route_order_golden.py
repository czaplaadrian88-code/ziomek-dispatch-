"""L6.A1 (Faza 3 audytu, 2026-07-01) — GOLDEN kanonu route-order (strona silnika).

Zamraża PORZĄDEK jazdy (proj = [(typ, sorted(order_ids))]) liczony przez
`route_podjazdy.order_podjazdy` na wspólnym korpusie
`tests/golden/route_order_corpus.json` (syntetyczne edge-case'y + żywe worki).
Druga noga parytetu (KONSOLA == KANON na tym samym korpusie) żyje w repo panelu:
`nadajesz_clone/panel/backend/tests/test_route_order_parity_golden.py`.

Razem ZASTĘPUJĄ wygasający `ziomek_time_route_monitor` (MONITOR_STOP_AFTER=
2026-07-10) siecią parytetu bez daty wygaśnięcia (kontrakt ③ parytet bliźniaków).
SPRINT0 05.07 (A0-ROUTEORDER): +trzecia noga SILNIK==GOLDEN (plan kanonu jako
źródło oczekiwań), +tripwire strukturalny C9, +klasy czasówka/paczka/carried
(korpus ≥20), +strażnik klucza "stops", +żywy następca monitora w
test_route_order_live_parity.py (gated, aktywacja za ACK).

Zmiana semantyki kolejności = ŚWIADOMA decyzja: re-generuj korpus generatorem
`tools/route_order_golden_corpus_gen.py` (panel venv) i skommituj razem ze
zmianą + wpisem dlaczego. Czerwony test bez re-generacji = regres kanonu.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import route_podjazdy as RP  # noqa: E402

CORPUS_PATH = Path(__file__).resolve().parent / "golden" / "route_order_corpus.json"


def _corpus() -> dict:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def _proj(stops) -> list:
    return [[t, sorted(str(i) for i in ids)] for t, ids in stops]


def _bag_objs(bag_dicts):
    return [SimpleNamespace(**d) for d in bag_dicts]


def test_corpus_exists_and_nonempty():
    c = _corpus()
    # SPRINT0 05.07 (handoff A0-ROUTEORDER): prog 10 -> 20 (klasy czasowka/paczka/carried)
    assert len(c["cases"]) >= 20, "korpus podejrzanie maly — regeneruj generatorem"
    # kazdy case ma zamrozone oczekiwanie
    assert all("expected_proj" in case for case in c["cases"])


def test_plan_docs_use_stops_key():
    """Strażnik klasy #17 (SPRINT0 05.07): WSZYSCY konsumenci planu czytają
    plan_doc["stops"] (jak żywe courier_plans.json). Pierwotny korpus (01.07)
    miał w syntetykach klucz "sequence" — case'y planowe cicho testowały
    FALLBACK czasowy zamiast ścieżki trust_canon. Plan w korpusie bez "stops"
    = martwy case planowy."""
    for case in _corpus()["cases"]:
        pd = case.get("plan_doc")
        if pd is None:
            continue
        assert "stops" in pd and "sequence" not in pd, (
            f"{case['id']}: plan_doc musi używać klucza 'stops' "
            f"(ma: {sorted(pd.keys())}) — inaczej konsumenci go nie widzą")


def test_canon_order_matches_golden():
    """order_podjazdy(X) == zamrozony kanon dla KAZDEGO case'u korpusu."""
    c = _corpus()
    flags = c["meta"]["flags"]
    failures = []
    for case in c["cases"]:
        got = _proj(RP.order_podjazdy(
            _bag_objs(case["bag"]), case["plan_doc"],
            plan_aware=flags["plan_aware"], trust_canon=flags["trust_canon"]))
        if got != case["expected_proj"]:
            failures.append((case["id"], case["expected_proj"], got))
    assert not failures, (
        "KANON route-order ROZJECHANY vs golden (zmiana semantyki kolejnosci!):\n"
        + "\n".join(f"  {cid}:\n    golden={exp}\n    teraz ={got}"
                    for cid, exp, got in failures))


def test_pickup_merge_min_pinned_to_corpus():
    """threshold-sprawl (kontrakt ①): prog sklejania w silniku == wartosc korpusu.
    Bliźniacze literaly (fleet_state.py, Ops13Console.tsx) pinuje test panelu."""
    assert RP.PICKUP_MERGE_MIN == _corpus()["meta"]["pickup_merge_min"]


def test_corpus_covers_canon_edge_cases():
    """Siatka nie moze cicho schudnac: obowiazkowe klasy przypadkow kanonu."""
    ids = {c["id"] for c in _corpus()["cases"]}
    required = {
        "syn_empty_bag", "syn_single_order", "syn_carried_first",
        "syn_same_restaurant_bundle", "syn_committed_ascending",
        "syn_plan_covers_bag_trust_canon", "syn_plan_partial_fallback",
        "syn_poisoned_zero_coords", "syn_no_ck_no_plan",
        # SPRINT0 05.07 — klasy z handoffu A0-ROUTEORDER (czasowki/paczki/carried):
        "syn_czasowka_far_ahead", "syn_czasowka_carried_mix",
        "syn_paczka_no_ck_last", "syn_paczka_same_sender_bundle",
        "syn_carried_two_relative_order", "syn_plan_trust_canon_carried_relax",
        "syn_mixed_czasowka_paczka_carried",
    }
    missing = required - ids
    assert not missing, f"korpus stracil obowiazkowe edge-case'y: {sorted(missing)}"
    assert any(c["source"] == "live" for c in _corpus()["cases"]), (
        "korpus bez zywych workow (replay) — regeneruj na zywym stanie")


# ─── SPRINT0 05.07 — trzecia noga parytetu: SILNIK (plan kanonu) ────────────
# Nogi 1-2 (apka==golden wyżej; konsola==golden w repo panelu) pilnują
# RENDERERÓW. Ta sekcja pilnuje, że golden jest wierny SILNIKOWI: dla case'ów,
# gdzie plan Ziomka (courier_plans) pokrywa cały worek, golden MUSI być
# czystą projekcją planu (skip węzła pickup dla niesionych + merge kolejnych
# odbiorów tej samej restauracji + dedup dostaw) — bez re-sortu, bez ETA.
# Razem: SILNIK(plan) == APKA(order_podjazdy) == KONSOLA(_build_route).

def _proj_from_plan_reference(case) -> list | None:
    """Niezależna (testowa) projekcja porządku WPROST z planu silnika.
    Zwraca None gdy plan nie pokrywa całego worka (wtedy golden = fallback,
    nie kanon silnika — poza zakresem tej nogi)."""
    pd = case.get("plan_doc")
    if not isinstance(pd, dict) or not pd.get("stops"):
        return None
    by_oid = {str(d["order_id"]): d for d in case["bag"]}
    out: list = []
    seen_drop: set = set()
    for s in pd["stops"]:
        if not isinstance(s, dict):
            continue
        oid = str(s.get("order_id"))
        o = by_oid.get(oid)
        if o is None:
            continue
        if s.get("type") == "pickup":
            if o.get("status") == "picked_up":
                continue  # niesione = bez odbioru
            if out and out[-1][0] == "pickup" and \
                    by_oid[out[-1][1][-1]].get("restaurant") == o.get("restaurant"):
                out[-1][1].append(oid)
            else:
                out.append(["pickup", [oid]])
        else:
            if oid in seen_drop:
                continue
            seen_drop.add(oid)
            out.append(["dropoff", [oid]])
    need_drop = {str(d["order_id"]) for d in case["bag"]}
    need_pick = {str(d["order_id"]) for d in case["bag"]
                 if d.get("status") != "picked_up"}
    cov_drop = {o for t, oids in out for o in oids if t == "dropoff"}
    cov_pick = {o for t, oids in out for o in oids if t == "pickup"}
    if cov_drop >= need_drop and cov_pick >= need_pick:
        return [[t, sorted(ids)] for t, ids in out]
    return None


def test_engine_plan_is_source_of_golden_for_covering_plans():
    """SILNIK==GOLDEN: gdy plan pokrywa worek (żywe plany i syntetyki
    trust_canon), zamrożone oczekiwanie MUSI być wierną projekcją planu.
    Rozjazd = golden przestał być kanonem silnika (renderer coś dosortował)
    ALBO plan w korpusie zmienił semantykę — obie rzeczy wymagają świadomej
    re-generacji, nie cichego dryfu."""
    c = _corpus()
    if not c["meta"]["flags"]["trust_canon"]:
        import pytest
        pytest.skip("trust_canon OFF w produkcji w chwili generacji korpusu — "
                    "golden legalnie nie renderuje planu verbatim")
    checked = 0
    failures = []
    for case in c["cases"]:
        ref = _proj_from_plan_reference(case)
        if ref is None:
            continue
        checked += 1
        if case["expected_proj"] != ref:
            failures.append((case["id"], ref, case["expected_proj"]))
    assert checked >= 2, "za malo case'ow z planem pokrywajacym worek — regeneruj korpus"
    assert not failures, (
        "GOLDEN niewierny planowi SILNIKA (noga silnik==apka==konsola zerwana):\n"
        + "\n".join(f"  {cid}:\n    plan  ={ref}\n    golden={exp}"
                    for cid, ref, exp in failures))


def test_golden_structural_invariants():
    """Tripwire C9 — strukturalne NIEMOŻLIWOŚCI w golden = kłamiący korpus,
    nie dane: (a) dropoff niecarried zlecenia nigdy PRZED jego pickupem,
    (b) niesione (picked_up) NIE mają stopu pickup, (c) każde zlecenie worka
    ma dokładnie 1 dropoff, (d) pickup tylko dla zleceń z worka."""
    for case in _corpus()["cases"]:
        statuses = {str(d["order_id"]): d.get("status") for d in case["bag"]}
        picked, dropped = set(), []
        for t, oids in case["expected_proj"]:
            (picked.update(oids) if t == "pickup" else dropped.extend(oids))
        for t, oids in case["expected_proj"]:
            for oid in oids:
                assert oid in statuses, f"{case['id']}: stop dla obcego oid {oid}"
        for oid, st in statuses.items():
            assert dropped.count(oid) == 1, (
                f"{case['id']}: {oid} ma {dropped.count(oid)} dropoffow (musi 1)")
            if st == "picked_up":
                assert oid not in picked, (
                    f"{case['id']}: niesione {oid} ma stop pickup (niemozliwe)")
            else:
                assert oid in picked, f"{case['id']}: {oid} bez pickupu"
                seq_flat = [(t, o) for t, oids in case["expected_proj"] for o in oids]
                assert seq_flat.index(("pickup", oid)) < seq_flat.index(("dropoff", oid)), (
                    f"{case['id']}: dropoff {oid} przed pickupem")
