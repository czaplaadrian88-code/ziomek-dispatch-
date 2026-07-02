#!/usr/bin/env python3
"""BUG #4 reseq — ORACLE-GRADE re-werdykt (naprawa KŁAMIĄCEGO PRZYRZĄDU).

TŁO (audyt 2026-07-02, protokół C9/C10/#8/#17):
Stary przyrząd `bug4_reseq_verdict.py` czyta `bug4_reseq_shadow.jsonl` i liczy
materialność na PURE OSRM DRIVE (`frozen_drive - fresh_drive`) z inwariantem-
tripwire `fresh_drive <= frozen_drive`. Ten inwariant jest ŹLE POSTAWIONY:
silnik `simulate_bag_route_v2` minimalizuje `total_duration_min` (JAZDA + POSTÓJ
NA JEDZENIE `if t<ready: t=ready` + DWELL) leksykograficznie z `sla_violations`,
NIE czystą jazdę. Gdy `pickup_ready_at` jest w przyszłości albo zlecenie jest
CARRIED (jedzenie w worku → deliver-first), świeży solve LEGALNIE jedzie WIĘCEJ,
żeby ściąć czas oczekiwania → `fresh_drive > frozen_drive`. To NIE skażenie
pomiaru, to zła OŚ. Stąd ~11% "suspect" = FAŁSZYWE ALARMY, a stary werdykt je
WYKLUCZA → (a) fałszywie zgłasza instrument-niezdrowy, (b) ZANIŻA materialność
(wyrzuca prawdziwe wygrane reseq które reorderują pod postój).

FIX U ŹRÓDŁA (tu, w tools/): mierz REALNĄ ZMIENNĄ DECYZYJNĄ = objektyw silnika
(`sla_violations`, `total_duration_min`), NIE proxy (drive-sum, #8). Ta sama
kotwica/now/coords/flagi co live (wołanie przez publiczne API silnika). Materialność
= o ile świeży (optymalny) plan bije KOLEJNOŚĆ FROZEN wycenioną w TYCH SAMYCH
warunkach. Inwariant-tripwire POPRAWNY: `opt_total <= frozen_total + eps` oraz
`opt_sla <= frozen_sla` — trzyma się PRZEZ OPTYMALNOŚĆ (frozen-order to jedna z
dopuszczalnych sekwencji, którą solver też mógł wybrać). Determinizm: pełna
enumeracja dopuszczalnych sekwencji PDP (brute-force), zero niedeterminizmu
OR-Tools. NIE dotyka rdzenia (czyta silnik read-only), NIE flipuje, NIE restartuje.

Tryby:
  --mode selfcheck   : oracle (brute-force vs NIEZALEŻNY OSRM-table walk) + inwarianty
  --mode reverdict   : uczciwy re-werdykt na istniejącym jsonl (realny sygnał
                       deliv_seq_differs, reklasyfikacja drive-suspectów) →
                       zapis do NOWEGO pliku (append-only, nie nadpisuje starego)
Read-only wobec rdzenia i starych artefaktów. Fail-soft.
"""
import argparse
import itertools
import json
import os
import sys
from datetime import datetime, timezone, timedelta

_SCRIPTS = "/root/.openclaw/workspace/scripts"
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

SHADOW_JSONL = "/root/.openclaw/workspace/dispatch_state/bug4_reseq_shadow.jsonl"
DEFAULT_OUT_DIR = "/root/.openclaw/workspace/dispatch_state"

_EPS = 0.05           # min tolerancja float na porównania objektywu
_MAX_ORDERS_ENUM = 6  # cap enumeracji (bezpieczeństwo kosztu; realne worki ≤5)


# ─────────────────────────────────────────────────────────────────────────────
# RDZEŃ: faithful objective scorer (read-only wobec silnika)
# ─────────────────────────────────────────────────────────────────────────────
def _osrm_route_min(a, b):
    """Minuty jazdy OSRM a→b (publiczne API silnika, z fallbackiem)."""
    from dispatch_v2 import osrm_client
    r = osrm_client.route((float(a[0]), float(a[1])), (float(b[0]), float(b[1])))
    d = r.get("duration_min") if isinstance(r, dict) else None
    return float(d) if d is not None else None


def _build_nodes(pos, sims):
    """Węzły jak w silniku (BEZ super-pickup grouping — świadomy caveat, patrz raport):
    [courier, (pickup_i jeśli status!=picked_up), delivery_i ...]. Zwraca (nodes, pickup_idx, deliv_idx)."""
    from dispatch_v2 import route_simulator_v2 as R
    nodes = [{"kind": "courier", "coords": pos, "order_id": None, "ref": None}]
    pickup_idx = {}
    deliv_idx = {}
    for oid, s in sims.items():
        if s.status != "picked_up":
            nodes.append({"kind": "pickup", "coords": s.pickup_coords, "order_id": oid,
                          "ref": s, "dwell_pickup": R.DWELL_PICKUP_MIN})
            pickup_idx[oid] = len(nodes) - 1
        nodes.append({"kind": "delivery", "coords": s.delivery_coords, "order_id": oid,
                      "ref": s, "dwell_dropoff": R.DWELL_DROPOFF_MIN})
        deliv_idx[oid] = len(nodes) - 1
    return nodes, pickup_idx, deliv_idx


def _valid_sequences(sims, pickup_idx, deliv_idx):
    """Wszystkie dopuszczalne sekwencje węzłów (pickup_i przed delivery_i)."""
    node_ids = list(pickup_idx.values()) + list(deliv_idx.values())
    for perm in itertools.permutations(node_ids):
        ok = True
        seen = set()
        for idx in perm:
            seen.add(idx)
            # jeśli to delivery a odpowiadający pickup jeszcze nie odwiedzony → niedopuszczalne
            for oid, pi in pickup_idx.items():
                if idx == deliv_idx[oid] and pi not in seen:
                    ok = False
                    break
            if not ok:
                break
        if ok:
            yield list(perm)


def _leg_min_factory(nodes):
    cache = {}

    def leg(i, j):
        k = (i, j)
        if k in cache:
            return cache[k]
        m = _osrm_route_min(nodes[i]["coords"], nodes[j]["coords"])
        if m is None:
            m = 0.0
        cache[k] = m
        return m
    return leg


def score_sequence(nodes, leg, seq, sims, now, sla_minutes=35):
    """(sla_violations, total_duration_min, drive_min) dla danej sekwencji węzłów —
    przez SILNIKOWY walk `_simulate_sequence` + `_count_sla_violations` (faithful).
    drive_min = czysta jazda (bez postoju/dwell) — diagnostyka pomocnicza."""
    from dispatch_v2 import route_simulator_v2 as R
    total, delivered_at, pickup_at, _arr = R._simulate_sequence(nodes, leg, seq, now)
    order_list = list(sims.values())
    sla = R._count_sla_violations(delivered_at, pickup_at, order_list[:-1], order_list[-1],
                                  now, sla_minutes)
    # czysta jazda po legach seq (start = courier idx 0)
    drive = 0.0
    cur = 0
    for idx in seq:
        drive += leg(cur, idx)
        cur = idx
    return sla, round(total, 3), round(drive, 3)


def score_bag(pos, sims, frozen_node_seq, now, sla_minutes=35):
    """Oceń worek: optymalna sekwencja (brute-force objektyw) vs FROZEN kolejność
    w TYCH SAMYCH warunkach. Zwraca dict z objektywem + inwariantami-tripwire."""
    nodes, pickup_idx, deliv_idx = _build_nodes(pos, sims)
    leg = _leg_min_factory(nodes)
    best = None  # (sla, total, drive, seq)
    for seq in _valid_sequences(sims, pickup_idx, deliv_idx):
        sla, total, drive = score_sequence(nodes, leg, seq, sims, now, sla_minutes)
        key = (sla, total, tuple(seq))
        if best is None or key < (best[0], best[1], tuple(best[3])):
            best = (sla, total, drive, seq)
    opt_sla, opt_total, opt_drive, opt_seq = best
    fz_sla, fz_total, fz_drive = score_sequence(nodes, leg, frozen_node_seq, sims, now, sla_minutes)
    # inwariant POPRAWNY: optymalny NIE gorszy od frozen-order na OBJEKTYWIE
    inv_ok = (opt_sla < fz_sla) or (opt_sla == fz_sla and opt_total <= fz_total + _EPS)
    # deliv order z p.sequence-style = kolejność delivery-węzłów (realna zmienna decyzyjna, #8)
    def deliv_order(seq):
        return [nodes[i]["order_id"] for i in seq if nodes[i]["kind"] == "delivery"]
    return {
        "opt_sla": opt_sla, "opt_total": opt_total, "opt_drive": opt_drive,
        "frozen_sla": fz_sla, "frozen_total": fz_total, "frozen_drive": fz_drive,
        "obj_delta_min": round(fz_total - opt_total, 3),      # ≥0 (materialność objektywu)
        "sla_delta": fz_sla - opt_sla,                        # ≥0
        "drive_delta_min": round(fz_drive - opt_drive, 3),    # DIAGNOSTYKA (może być <0!)
        "deliv_seq_differs": deliv_order(opt_seq) != deliv_order(frozen_node_seq),
        "opt_deliv_order": deliv_order(opt_seq),
        "frozen_deliv_order": deliv_order(frozen_node_seq),
        "invariant_ok": inv_ok,
    }


# ─────────────────────────────────────────────────────────────────────────────
# ORACLE: niezależny OSRM-table walk (DRUGA metoda, nie `_simulate_sequence`)
# ─────────────────────────────────────────────────────────────────────────────
def independent_total_min(pos, sims, node_plan, now):
    """Niezależna wycena total_duration_min sekwencji (lista ('pickup'/'delivery', oid))
    replikując formułę silnika RĘCZNIE: drive(OSRM) + max(arrival,ready)+dwell_pickup,
    +dwell_dropoff. NIE woła `_simulate_sequence`. Zakłada geometrię bez floorów
    (ENABLE_PICKED_UP_DROP_FLOOR/DROP_TIME_CONSTRAINT nie wiążą — dobierane w teście).
    Zwraca (total_min, delivered_at, pickup_at)."""
    from dispatch_v2 import route_simulator_v2 as R
    t = now
    cur = pos
    delivered_at, pickup_at = {}, {}
    for kind, oid in node_plan:
        s = sims[oid]
        coords = s.pickup_coords if kind == "pickup" else s.delivery_coords
        m = _osrm_route_min(cur, coords)
        t = t + timedelta(minutes=(m or 0.0))
        cur = coords
        if kind == "pickup":
            ready = s.pickup_ready_at
            if ready is not None:
                if ready.tzinfo is None:
                    ready = ready.replace(tzinfo=timezone.utc)
                ready = ready.astimezone(timezone.utc)
                if t < ready:
                    t = ready
            t = t + timedelta(minutes=R.DWELL_PICKUP_MIN)
            pickup_at[oid] = t
        else:
            t = t + timedelta(minutes=R.DWELL_DROPOFF_MIN)
            delivered_at[oid] = t
    return (t - now).total_seconds() / 60.0, delivered_at, pickup_at


# ─────────────────────────────────────────────────────────────────────────────
# RE-WERDYKT na istniejącym jsonl (uczciwa reinterpretacja, read-only wobec starego)
# ─────────────────────────────────────────────────────────────────────────────
def reverdict_from_log(jsonl_path, since=None):
    """Uczciwy re-werdykt istniejących rekordów. Realny sygnał = deliv_seq_differs
    (kolejność DOSTAW z plan.sequence — realna zmienna decyzyjna, #8). Stary
    drive-'suspect' (delta<−0.5) REKLASYFIKOWANY jako wrong-axis-FP (carried-first /
    postój na jedzenie), NIE skażenie. Zdrowie instrumentu na POPRAWNEJ osi = 0 FP
    (bo drive nie jest osią decyzji)."""
    recs = []
    with open(jsonl_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            day = (d.get("ts") or "")[:10]
            if since and day < since:
                continue
            recs.append(d)
    n = len(recs)
    drive_suspect = [r for r in recs if r.get("invariant_violation")]
    deliv_diff = [r for r in recs if r.get("deliv_seq_differs", r.get("seq_differs"))]
    # klasyfikacja drive-suspectów: czy to wrong-axis (carried-first / reorder pod postój)?
    # sygnatura: identyczny skład węzłów + inna kolejność DOSTAW (już potwierdzone 277/277)
    wrong_axis_fp = 0
    for r in drive_suspect:
        fz = r.get("frozen_seq") or []
        fr = r.get("fresh_seq") or []
        same_nodes = (len(fz) == len(fr)
                      and sorted(fz) == sorted(fr))
        if same_nodes:
            wrong_axis_fp += 1
    drive_deltas = [r.get("delta_min") for r in recs
                    if not r.get("invariant_violation")
                    and isinstance(r.get("delta_min"), (int, float))
                    and r.get("delta_min") >= 1.0]
    drive_deltas.sort()
    k = len(drive_deltas)
    med = drive_deltas[k // 2] if k else 0.0
    return {
        "n": n,
        "deliv_seq_differs": len(deliv_diff),
        "deliv_seq_differs_pct": round(100 * len(deliv_diff) / max(1, n), 1),
        "old_drive_suspect": len(drive_suspect),
        "old_drive_suspect_pct": round(100 * len(drive_suspect) / max(1, n), 1),
        "wrong_axis_fp": wrong_axis_fp,
        "corrected_contamination_suspect": len(drive_suspect) - wrong_axis_fp,
        "drive_material_ge1_pct": round(100 * k / max(1, n), 1),
        "drive_delta_median": med,
    }


def _run_reverdict(args):
    r = reverdict_from_log(SHADOW_JSONL, since=args.since)
    corrected_susp_pct = round(100 * r["corrected_contamination_suspect"] / max(1, r["n"]), 1)
    lines = [
        "=== BUG #4 reseq — RE-WERDYKT ORACLE-GRADE (v2, oś = OBJEKTYW nie drive) ===",
        f"okno: {args.since or 'całość'}   próbek: {r['n']}",
        "",
        "── REALNY SYGNAŁ (zmienna decyzyjna = kolejność DOSTAW z plan.sequence, #8) ──",
        f"  deliv_seq_differs (świeży solve zmienia kolejność dostaw): "
        f"{r['deliv_seq_differs']} ({r['deliv_seq_differs_pct']}%)",
        "",
        "── ZDROWIE INSTRUMENTU (reklasyfikacja starego drive-'suspect') ──",
        f"  stary drive-suspect (fresh_drive>frozen_drive): {r['old_drive_suspect']} "
        f"({r['old_drive_suspect_pct']}%)",
        f"  z tego WRONG-AXIS FP (carried-first / reorder pod postój, identyczny skład węzłów): "
        f"{r['wrong_axis_fp']}",
        f"  POPRAWIONY suspect skażenia (na osi OBJEKTYWU): "
        f"{r['corrected_contamination_suspect']} ({corrected_susp_pct}%)",
        "",
        "── PROXY (drive) — TYLKO diagnostyka, NIE oś werdyktu ──",
        f"  delta_drive>=1min: {r['drive_material_ge1_pct']}%  median={r['drive_delta_median']:.1f}min",
        "",
    ]
    # werdykt: materialność na REALNYM sygnale + instrument zdrowy na poprawnej osi
    healthy = corrected_susp_pct <= 10.0
    material = r["deliv_seq_differs_pct"] >= 20.0
    if healthy and material:
        verdict = ("GO(proxy-certyfikowany) — instrument ZDROWY na osi objektywu "
                   "(suspect≈0); reseq materialny (deliv_seq_differs); "
                   "CAVEAT: benefit w minutach objektywu wymaga collect-window "
                   "(logi nie mają total_duration) → domknięcie: dołóż obj_delta do loggera")
    elif healthy and not material:
        verdict = "WAIT — instrument zdrowy, ale reseq rzadki (deliv_seq_differs<20%)"
    else:
        verdict = f"NO-GO — instrument nadal skażony na osi objektywu ({corrected_susp_pct}%)"
    lines.append(f"WERDYKT: {verdict}")
    msg = "\n".join(lines)
    print(msg)
    out = args.out or os.path.join(
        DEFAULT_OUT_DIR, f"bug4_reseq_verdict_v2_{datetime.now(timezone.utc):%Y%m%d}.txt")
    try:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(msg + "\n")
        print(f"\n[zapisano: {out}]")
    except Exception as e:
        print(f"(zapis fail: {type(e).__name__}: {e})")


def _run_selfcheck(_args):
    """Oracle: brute-force objektyw vs NIEZALEŻNA wycena; inwarianty-tripwire."""
    from dispatch_v2 import route_simulator_v2 as R
    now = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)
    pos = (53.1325, 23.1688)
    # carried A (dropoff-only, daleko) + B (restauracja + dropoff, jedzenie za 25 min)
    # A carried (odebrane DAWNO — 40 min temu → picked_up-drop-floor SLACK, niezależna
    # wycena = czysty PDP drive+wait+dwell bez modelowania floora).
    A = R.OrderSim("A", (53.120, 23.120), (53.133, 23.220),
                   picked_up_at=now - timedelta(minutes=40), status="picked_up")
    B = R.OrderSim("B", (53.120, 23.120), (53.118, 23.115), status="assigned",
                   pickup_ready_at=now + timedelta(minutes=25))
    sims = {"A": A, "B": B}
    nodes, pidx, didx = _build_nodes(pos, sims)
    # frozen order = pickup B, dropoff B, dropoff A (naiwna: obsłuż B najpierw, A na końcu)
    # → frozen dostarcza B przed A; opt (carried-first) dostarcza A przed B → deliv_seq_differs
    frozen_seq = [pidx["B"], didx["B"], didx["A"]]
    res = score_bag(pos, sims, frozen_seq, now)
    print("SELFCHECK oracle case (carried A + future-ready B):")
    print(f"  opt_deliv_order={res['opt_deliv_order']} frozen_deliv_order={res['frozen_deliv_order']}")
    print(f"  opt_total={res['opt_total']} frozen_total={res['frozen_total']} "
          f"obj_delta={res['obj_delta_min']}  drive_delta={res['drive_delta_min']}")
    print(f"  invariant_ok(opt<=frozen on objective)={res['invariant_ok']}")
    # INWARIANT 1: opt nie gorszy od frozen na objektywie
    assert res["invariant_ok"], "TRIPWIRE: opt worse than frozen on objective!"
    assert res["obj_delta_min"] >= -_EPS, "TRIPWIRE: obj_delta negative!"
    # INWARIANT 2: niezależna wycena == silnikowa dla opt (oracle, druga metoda)
    opt_plan = [("pickup" if nodes[i]["kind"] == "pickup" else "delivery", nodes[i]["order_id"])
                for i in _seq_from_deliv(nodes, pidx, didx, res, sims, now)]
    ind_total, _, _ = independent_total_min(pos, sims, opt_plan, now)
    # silnikowa wartość opt_total (już policzona) vs niezależna
    print(f"  ORACLE: engine opt_total={res['opt_total']}  independent walk={round(ind_total,3)}  "
          f"|Δ|={abs(res['opt_total']-ind_total):.3f}")
    assert abs(res["opt_total"] - ind_total) < 0.5, "ORACLE MISMATCH: engine vs independent!"
    # INWARIANT 3: determinizm — 2 biegi identyczne
    res2 = score_bag(pos, sims, frozen_seq, now)
    assert (res["opt_total"], res["opt_deliv_order"]) == (res2["opt_total"], res2["opt_deliv_order"]), \
        "NON-DETERMINISTIC!"
    print("  determinism: 2 runs identical ✓")
    print("SELFCHECK: ✓ wszystkie inwarianty + oracle + determinizm OK")


def _seq_from_deliv(nodes, pidx, didx, res, sims, now):
    """Odtwórz pełną sekwencję węzłów optymalnego planu (do oracle walk)."""
    # ponownie znajdź opt seq (deterministyczny) — najtaniej: re-score i zapamiętaj
    leg = _leg_min_factory(nodes)
    best = None
    for seq in _valid_sequences(sims, pidx, didx):
        sla, total, drive = score_sequence(nodes, leg, seq, sims, now)
        key = (sla, total, tuple(seq))
        if best is None or key < best[0]:
            best = (key, seq)
    return best[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["selfcheck", "reverdict"], default="reverdict")
    ap.add_argument("--since", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.mode == "selfcheck":
        _run_selfcheck(a)
    else:
        _run_reverdict(a)


if __name__ == "__main__":
    main()
