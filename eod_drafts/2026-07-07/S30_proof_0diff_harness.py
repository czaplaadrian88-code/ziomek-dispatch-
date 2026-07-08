#!/usr/bin/env python3
"""Sprint 30 — DOWÓD 0-DIFF unifikacji route_order (READ-ONLY, deterministyczny).

Uruchamiać: ZIOMEK_SCRIPTS_ROOT=<pkgroot> PYTHONPATH=<pkgroot> venv/python proof_0diff.py
Wyjście: linie [OK]/[FAIL] + podsumowanie; exit 0 = wszystko 0-diff."""
import importlib.util
import json
import random
import sys
from pathlib import Path
from types import SimpleNamespace

SP = Path("/tmp/claude-0/-root--openclaw-workspace-scripts-dispatch-v2/"
          "1a7eef68-cffc-4df8-9a13-a834575795c4/scratchpad")
WT = Path("/root/.openclaw/workspace/scripts/wt-routeorder")
STATE = Path("/root/.openclaw/workspace/dispatch_state")

import dispatch_v2.route_order as RO  # z worktree (pkgroot)

# --- oracle: route_podjazdy @ HEAD (przed promocją) ---
spec = importlib.util.spec_from_file_location("oracle_rp", SP / "oracle_route_podjazdy_HEAD.py")
ORC = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ORC)


def proj(stops):
    return [[t, sorted(str(i) for i in ids)] for t, ids in stops]


fails = []


def check(name, cond, detail=""):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        fails.append((name, detail))


# ========== 1. order_podjazdy: bajt-identyczność vs oracle ==========
# 1a. golden corpus (+ zgodność z zamrożonym expected_proj)
corpus = json.loads((WT / "tests/golden/route_order_corpus.json").read_text())
mflags = corpus["meta"]["flags"]
pa0, tc0 = mflags["plan_aware"], mflags["trust_canon"]
g_diff = 0
for c in corpus["cases"]:
    bag = [SimpleNamespace(**d) for d in c["bag"]]
    a = proj(RO.order_podjazdy(bag, c["plan_doc"], plan_aware=pa0, trust_canon=tc0))
    b = proj(ORC.order_podjazdy(bag, c["plan_doc"], plan_aware=pa0, trust_canon=tc0))
    if a != b or a != c["expected_proj"]:
        g_diff += 1
        print(f"    corpus DIFF {c['id']}: new={a} oracle={b} golden={c['expected_proj']}")
check(f"golden corpus 0-diff (25 case, new==oracle==expected)", g_diff == 0,
      f"{g_diff} rozjazdów")

# 1b. żywe worki z orders_state (WSZYSTKIE 4 kombinacje flag)
try:
    raw = json.loads((STATE / "orders_state.json").read_text())
    orders = raw if isinstance(raw, list) else list((raw.get("orders", raw) or {}).values())
    plans = json.loads((STATE / "courier_plans.json").read_text())
except Exception as e:
    orders, plans = [], {}
    print(f"    (live state niedostępny: {type(e).__name__})")

ACTIVE = {"assigned", "picked_up", "en_route"}
BAG_FIELDS = ("order_id", "status", "restaurant", "delivery_address", "czas_kuriera_warsaw",
              "pickup_address", "pickup_coords", "delivery_coords", "picked_up_at",
              "assigned_at", "created_at_utc", "pickup_at_warsaw")
bags = {}
for o in orders:
    if o.get("status") in ACTIVE and o.get("courier_id"):
        cid = str(o["courier_id"])
        bags.setdefault(cid, []).append(
            {f: o.get(f) for f in BAG_FIELDS} | {"order_id": str(o.get("order_id"))})

live_diff = live_checked = 0
for cid, bag in sorted(bags.items()):
    plan = plans.get(cid) if isinstance(plans, dict) else None
    bo = [SimpleNamespace(**d) for d in bag]
    for pa in (False, True):
        for tc in (False, True):
            a = proj(RO.order_podjazdy(bo, plan, plan_aware=pa, trust_canon=tc))
            b = proj(ORC.order_podjazdy(bo, plan, plan_aware=pa, trust_canon=tc))
            live_checked += 1
            if a != b:
                live_diff += 1
                print(f"    live DIFF cid={cid} pa={pa} tc={tc}")
check(f"żywe worki 0-diff ({len(bags)} kurierów × 4 kombinacje flag = {live_checked} porównań)",
      live_diff == 0, f"{live_diff} rozjazdów")

# 1c. fuzz — losowe worki+plany, WSZYSTKIE kombinacje flag
rnd = random.Random(30073007)  # deterministyczny seed (Sprint 30)
RESTS = ["R1", "R2", "R3", "Punkt Nadan Sienkiewicza", None]
CKS = [None, "2026-07-07T12:00:00+02:00", "2026-07-07T12:05:00+02:00",
       "2026-07-07T12:20:00+02:00", "2026-07-07T14:00:00+02:00", "bad-iso"]
fuzz_diff = fuzz_n = 0
for _ in range(6000):
    n = rnd.randint(0, 5)
    oids = rnd.sample(range(900000, 900050), n)
    bag = []
    for oid in oids:
        st = rnd.choice(["assigned", "assigned", "picked_up", "en_route"])
        bag.append(SimpleNamespace(
            order_id=str(oid), status=st, restaurant=rnd.choice(RESTS),
            czas_kuriera_warsaw=rnd.choice(CKS),
            picked_up_at="2026-07-07T10:00:00+00:00" if st == "picked_up" else None))
    # losowy plan pokrywający podzbiór (czasem cały) worka
    plan = None
    if bag and rnd.random() < 0.6:
        chosen = [b for b in bag if rnd.random() < 0.85]
        stops = []
        for b in chosen:
            if b.status != "picked_up":
                stops.append({"type": "pickup", "order_id": b.order_id})
        for b in chosen:
            stops.append({"type": "dropoff", "order_id": b.order_id})
        rnd.shuffle(stops)  # plan może mieć dowolną kolejność (kanon)
        plan = {"stops": stops}
    for pa in (False, True):
        for tc in (False, True):
            a = proj(RO.order_podjazdy(bag, plan, plan_aware=pa, trust_canon=tc))
            b = proj(ORC.order_podjazdy(bag, plan, plan_aware=pa, trust_canon=tc))
            fuzz_n += 1
            if a != b:
                fuzz_diff += 1
                if fuzz_diff <= 3:
                    print(f"    fuzz DIFF pa={pa} tc={tc} bag={[(o.order_id,o.status,o.restaurant,o.czas_kuriera_warsaw) for o in bag]} plan={plan}")
check(f"fuzz order_podjazdy 0-diff ({fuzz_n} porównań, seed 30073007)", fuzz_diff == 0,
      f"{fuzz_diff} rozjazdów")


# ========== 2. repair_dropoffs_after_pickups: parytet vs OBIE zamrożone kopie legacy ==========
def legacy_pr_repair(seq):  # plan_recheck.py:1203 (klucz 'type', str-cast) — VERBATIM
    out = list(seq)
    for _ in range(len(out) * len(out) + 1):
        pidx = {str(s.get("order_id")): i for i, s in enumerate(out) if s.get("type") == "pickup"}
        viol = next((i for i, s in enumerate(out)
                     if s.get("type") == "dropoff" and pidx.get(str(s.get("order_id")), -1) > i), None)
        if viol is None:
            return out
        pi = pidx[str(out[viol].get("order_id"))]
        s = out.pop(viol)
        out.insert(pi, s)
    return None


def legacy_co_repair(seq, kind_key="kind"):  # courier_orders.py:424 (klucz 'kind', raw id) — VERBATIM
    out = list(seq)
    for _ in range(len(out) * len(out) + 1):
        pidx = {st.get("order_id"): i for i, st in enumerate(out) if st.get(kind_key) == "pickup"}
        viol = next((i for i, st in enumerate(out)
                     if st.get(kind_key) == "dropoff" and pidx.get(st.get("order_id"), -1) > i), None)
        if viol is None:
            return out
        pi = pidx[out[viol].get("order_id")]
        st = out.pop(viol)
        out.insert(pi, st)
    return None


def rand_seq(rnd, kind_key, id_kind):
    n = rnd.randint(0, 5)
    oids = rnd.sample(range(1, 40), n)
    steps = []
    for oid in oids:
        val = str(oid) if id_kind == "str" else oid
        if rnd.random() < 0.85:
            steps.append({kind_key: "pickup", "order_id": val})
        steps.append({kind_key: "dropoff", "order_id": val})
    rnd.shuffle(steps)
    return steps


rnd2 = random.Random(4242)
pr_diff = co_diff = rep_n = 0
for _ in range(8000):
    # vs plan_recheck legacy (klucz 'type', id str)
    s1 = rand_seq(rnd2, "type", "str")
    a = RO.repair_dropoffs_after_pickups([dict(s) for s in s1], kind_key="type")
    b = legacy_pr_repair([dict(s) for s in s1])
    if [None if a is None else [(x.get("type"), x.get("order_id")) for x in a]] != \
       [None if b is None else [(x.get("type"), x.get("order_id")) for x in b]]:
        pr_diff += 1
    # vs courier_orders legacy (klucz 'kind', id str I int)
    for idk in ("str", "int"):
        s2 = rand_seq(rnd2, "kind", idk)
        a2 = RO.repair_dropoffs_after_pickups([dict(s) for s in s2], kind_key="kind")
        b2 = legacy_co_repair([dict(s) for s in s2])
        pa2 = None if a2 is None else [(x.get("kind"), x.get("order_id")) for x in a2]
        pb2 = None if b2 is None else [(x.get("kind"), x.get("order_id")) for x in b2]
        if pa2 != pb2:
            co_diff += 1
            if co_diff <= 3:
                print(f"    repair CO DIFF idk={idk} seq={s2}")
    rep_n += 1
check(f"repair == plan_recheck legacy (klucz 'type', {rep_n} seq)", pr_diff == 0, f"{pr_diff} rozjazdów")
check(f"repair == courier_orders legacy (klucz 'kind', str+int, {rep_n*2} seq)", co_diff == 0, f"{co_diff} rozjazdów")


# ========== 3. build_stop_sequence == transformacja gałęzi 1 (console_podjazdy) ==========
bss_diff = bss_n = 0
for c in corpus["cases"]:
    bag = [SimpleNamespace(**d) for d in c["bag"]]
    order = RO.order_podjazdy(bag, c["plan_doc"], plan_aware=pa0, trust_canon=tc0)
    branch1 = [{"order_id": str(oid), "kind": typ} for (typ, oids) in order for oid in oids]
    bss = RO.build_stop_sequence(bag, c["plan_doc"], plan_aware=pa0, trust_canon=tc0)
    bss_n += 1
    if bss != branch1:
        bss_diff += 1
check(f"build_stop_sequence == transformacja gałęzi 1 ({bss_n} case)", bss_diff == 0, f"{bss_diff} rozjazdów")

# ========== 4. re-export: ta sama funkcja (jedno źródło konstrukcyjnie) ==========
import dispatch_v2.route_podjazdy as RP
check("route_podjazdy.order_podjazdy IS route_order.order_podjazdy (jedno źródło)",
      RP.order_podjazdy is RO.order_podjazdy)
check("route_podjazdy.repair IS route_order.repair", RP.repair_dropoffs_after_pickups is RO.repair_dropoffs_after_pickups)
check("route_podjazdy.PICKUP_MERGE_MIN == 10 (kontrakt cross-język Kotlin)", RP.PICKUP_MERGE_MIN == 10)

print("\n==== PODSUMOWANIE ====")
print(f"golden: 25 | live: {live_checked} | fuzz order: {fuzz_n} | repair: {rep_n*3} | bss: {bss_n}")
print("WYNIK:", "✅ WSZYSTKO 0-DIFF" if not fails else f"❌ {len(fails)} FAIL: {[f[0] for f in fails]}")
sys.exit(0 if not fails else 1)
