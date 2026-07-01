"""L6.A1 (Faza 3 audytu, 2026-07-01) — GOLDEN kanonu route-order (strona silnika).

Zamraża PORZĄDEK jazdy (proj = [(typ, sorted(order_ids))]) liczony przez
`route_podjazdy.order_podjazdy` na wspólnym korpusie
`tests/golden/route_order_corpus.json` (syntetyczne edge-case'y + żywe worki).
Druga noga parytetu (KONSOLA == KANON na tym samym korpusie) żyje w repo panelu:
`nadajesz_clone/panel/backend/tests/test_route_order_parity_golden.py`.

Razem ZASTĘPUJĄ wygasający `ziomek_time_route_monitor` (MONITOR_STOP_AFTER=
2026-07-10) siecią parytetu bez daty wygaśnięcia (kontrakt ③ parytet bliźniaków).

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
    assert len(c["cases"]) >= 10, "korpus podejrzanie maly — regeneruj generatorem"
    # kazdy case ma zamrozone oczekiwanie
    assert all("expected_proj" in case for case in c["cases"])


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
    }
    missing = required - ids
    assert not missing, f"korpus stracil obowiazkowe edge-case'y: {sorted(missing)}"
    assert any(c["source"] == "live" for c in _corpus()["cases"]), (
        "korpus bez zywych workow (replay) — regeneruj na zywym stanie")
