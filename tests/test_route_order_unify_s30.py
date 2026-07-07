"""Sprint 30 (2026-07-07) — strażniki UNIFIKACJI route_order (behawioralne, nie tekstowe).

Kontrakt: `route_order.py` = JEDNO ŹRÓDŁO kolejności trasy; `route_podjazdy`
re-eksportuje z niego; `plan_recheck._repair_dropoffs_after_pickups` deleguje do
`route_order.repair_dropoffs_after_pickups`; apka `courier_orders` konwerguje gałęzie
fallback za flagą `ROUTE_ORDER_UNIFIED`. Testy sprawdzają ZACHOWANIE (wynik funkcji),
nie obecność tokenu (C13: strażnik tekstowy nie łapie inwersji). Twardy dowód 0-diff
(24k fuzz vs oracle @HEAD + obie kopie legacy repair) = `eod_drafts/2026-07-07/
S30A_routeorder_0diff.md` + `scratchpad/proof_0diff.py`.
"""
import json
import random
from pathlib import Path
from types import SimpleNamespace

from dispatch_v2 import route_order as RO
from dispatch_v2 import route_podjazdy as RP

CORPUS = Path(__file__).resolve().parent / "golden" / "route_order_corpus.json"


def _proj(stops):
    return [[t, sorted(str(i) for i in ids)] for t, ids in stops]


# ---------- 1. re-eksport = jedno źródło konstrukcyjnie ----------

def test_reexport_is_same_object():
    """route_podjazdy NIE ma własnej kopii reguły — re-eksportuje route_order."""
    assert RP.order_podjazdy is RO.order_podjazdy
    assert RP.order_route is RO.order_route
    assert RP.repair_dropoffs_after_pickups is RO.repair_dropoffs_after_pickups
    assert RP.build_stop_sequence is RO.build_stop_sequence
    assert RP.PICKUP_MERGE_MIN == RO.PICKUP_MERGE_MIN == 10  # kontrakt cross-język Kotlin


def test_all_public_symbols_reexported():
    for name in ("order_podjazdy", "pickup_runs", "plan_drop_rank",
                 "_canon_order_from_plan", "_plan_pickup_clusters",
                 "PICKUP_MERGE_MIN", "_iso", "_attr", "_pickup_dt"):
        assert getattr(RP, name) is getattr(RO, name), name


# ---------- 2. kolejność == golden (kanon stabilny) ----------

def test_order_matches_golden_corpus():
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    flags = corpus["meta"]["flags"]
    pa, tc = flags["plan_aware"], flags["trust_canon"]
    for c in corpus["cases"]:
        bag = [SimpleNamespace(**d) for d in c["bag"]]
        got = _proj(RO.order_podjazdy(bag, c["plan_doc"], plan_aware=pa, trust_canon=tc))
        assert got == c["expected_proj"], f"{c['id']}: {got} != {c['expected_proj']}"


def test_build_stop_sequence_equals_branch1_transform():
    """build_stop_sequence = dokładnie transformacja gałęzi console_podjazdy apki
    (courier_orders:1145): rozwinięcie [(typ,[ids])] na kroki per-zlecenie."""
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    flags = corpus["meta"]["flags"]
    pa, tc = flags["plan_aware"], flags["trust_canon"]
    for c in corpus["cases"]:
        bag = [SimpleNamespace(**d) for d in c["bag"]]
        order = RO.order_podjazdy(bag, c["plan_doc"], plan_aware=pa, trust_canon=tc)
        branch1 = [{"order_id": str(oid), "kind": typ} for (typ, oids) in order for oid in oids]
        assert RO.build_stop_sequence(bag, c["plan_doc"], plan_aware=pa, trust_canon=tc) == branch1


# ---------- 3. repair: behawioralny parytet vs OBIE kopie legacy + kills ----------

def _legacy_pr_repair(seq):  # plan_recheck.py:1203 VERBATIM (klucz 'type')
    out = list(seq)
    for _ in range(len(out) * len(out) + 1):
        pidx = {str(s.get("order_id")): i for i, s in enumerate(out) if s.get("type") == "pickup"}
        viol = next((i for i, s in enumerate(out)
                     if s.get("type") == "dropoff" and pidx.get(str(s.get("order_id")), -1) > i), None)
        if viol is None:
            return out
        pi = pidx[str(out[viol].get("order_id"))]
        out.insert(pi, out.pop(viol))
    return None


def _legacy_co_repair(seq, kind_key="kind"):  # courier_orders.py:424 VERBATIM (klucz 'kind')
    out = list(seq)
    for _ in range(len(out) * len(out) + 1):
        pidx = {st.get("order_id"): i for i, st in enumerate(out) if st.get(kind_key) == "pickup"}
        viol = next((i for i, st in enumerate(out)
                     if st.get(kind_key) == "dropoff" and pidx.get(st.get("order_id"), -1) > i), None)
        if viol is None:
            return out
        pi = pidx[out[viol].get("order_id")]
        out.insert(pi, out.pop(viol))
    return None


def test_repair_parity_vs_both_legacy_fuzz():
    rnd = random.Random(4242)
    for _ in range(3000):
        n = rnd.randint(0, 5)
        oids = rnd.sample(range(1, 40), n)
        # klucz 'type' (plan_recheck)
        st = []
        for oid in oids:
            if rnd.random() < 0.85:
                st.append({"type": "pickup", "order_id": str(oid)})
            st.append({"type": "dropoff", "order_id": str(oid)})
        rnd.shuffle(st)
        a = RO.repair_dropoffs_after_pickups([dict(s) for s in st], kind_key="type")
        b = _legacy_pr_repair([dict(s) for s in st])
        assert (a is None) == (b is None)
        if a is not None:
            assert [(x["type"], x["order_id"]) for x in a] == [(x["type"], x["order_id"]) for x in b]
        # klucz 'kind' (courier_orders), id str I int
        for idk in (str, int):
            st2 = []
            for oid in oids:
                v = idk(oid)
                if rnd.random() < 0.85:
                    st2.append({"kind": "pickup", "order_id": v})
                st2.append({"kind": "dropoff", "order_id": v})
            rnd.shuffle(st2)
            a2 = RO.repair_dropoffs_after_pickups([dict(s) for s in st2], kind_key="kind")
            b2 = _legacy_co_repair([dict(s) for s in st2])
            assert (a2 is None) == (b2 is None)
            if a2 is not None:
                assert [(x["kind"], x["order_id"]) for x in a2] == [(x["kind"], x["order_id"]) for x in b2]


def test_repair_moves_dropoff_after_its_pickup():
    """Kill behawioralny: dostawa wyprzedzająca odbiór MUSI wylądować za nim."""
    seq = [{"type": "pickup", "order_id": "A"},
           {"type": "dropoff", "order_id": "B"},   # B przed swoim odbiorem
           {"type": "pickup", "order_id": "B"},
           {"type": "dropoff", "order_id": "A"}]
    out = RO.repair_dropoffs_after_pickups(seq, kind_key="type")
    order = [(s["type"], s["order_id"]) for s in out]
    assert order.index(("pickup", "B")) < order.index(("dropoff", "B"))
    assert order.index(("pickup", "A")) < order.index(("dropoff", "A"))


def test_repair_noop_when_already_valid():
    seq = [{"type": "pickup", "order_id": "A"}, {"type": "dropoff", "order_id": "A"}]
    assert RO.repair_dropoffs_after_pickups(seq, kind_key="type") == seq


# ---------- 4. engine plan_recheck deleguje (behawioralnie) ----------

def test_plan_recheck_repair_delegates():
    from dispatch_v2 import plan_recheck as pr
    seq = [{"type": "dropoff", "order_id": "X"}, {"type": "pickup", "order_id": "X"}]
    out = pr._repair_dropoffs_after_pickups(seq)
    assert [(s["type"], s["order_id"]) for s in out] == [("pickup", "X"), ("dropoff", "X")]
    # deleguje do route_order (nie własna kopia)
    assert pr._route_order is RO
