#!/usr/bin/env python3
"""Strażnik carried-first — READ-ONLY detektor (zero wpływu na decyzje).

Po co: carried-first „naprawiane 11×" wracał, bo nie było DETEKTORA — łapało go
oko Adriana, za późno. Ten strażnik liczy IDENTYCZNIE jak silnik (reużywa
`plan_recheck._start_anchor` + `_apply_canon_order_invariants` — żaden drugi
algorytm) i raz na uruchomienie klasyfikuje KAŻDEGO aktywnego wielozleceniowego
kuriera do REŻIMU. Carried-first wyłazi tylko w reżimach ryzyka (brak pozycji /
plan unieważniony / plan nie pokrywa worka / brak planu) — wtedy konsola/apka
spadają we własny fallback. Strażnik liczy ich częstość → jak wróci, to ALERT w
minutę (plik+log), nie łapanie okiem.

Kanał (wybór Adriana 29.06): plik `dispatch_state/carried_first_guard.jsonl` + log.
NIGDY nie fabrykuje carried-first przy braku pozycji (lekcja served_synthetic):
brak pozycji = jawny `no_position`, nie zmyślona sekwencja.

Uruchomienie: `python3 -m dispatch_v2.tools.carried_first_guard [--dry]`
(--dry = nie zapisuj do jsonl, tylko wypisz).
"""
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2 import plan_recheck as PR  # noqa: E402

GUARD_LOG = "/root/.openclaw/workspace/dispatch_state/carried_first_guard.jsonl"
COURIER_PLANS_PATH = "/root/.openclaw/workspace/dispatch_state/courier_plans.json"

# Reżimy, w których konsumenci (konsola/apka) spadają we własny fallback
# carried-first/raw-OSRM (patrz plan-invalidation-oscillation + r6-ready-anchor).
RISK_KINDS = frozenset({"no_position", "no_plan", "plan_invalidated", "coverage_gap"})


def _seq(stops: List[Dict[str, Any]]) -> List[List[str]]:
    return [[str(s.get("order_id")), s.get("type")] for s in stops]


def _carried_first_smell(saved_seq, canon_seq) -> bool:
    """True gdy ZAPISANY plan dowozi X przed ODBIOREM innego Y, a kanon-z-pozycją
    odbiera Y PRZED dowiezieniem X (= dokładnie „Skandynawska przed odbiorem
    Rukoli"). To jedyny rzetelny test: oracle = sam silnik z realną pozycją."""
    for i, (oid, typ) in enumerate(saved_seq):
        if typ != "dropoff":
            continue
        for oid2, typ2 in saved_seq[i + 1:]:
            if typ2 == "pickup" and oid2 != oid:
                try:
                    ci_drop = canon_seq.index([oid, "dropoff"])
                    ci_pick = canon_seq.index([oid2, "pickup"])
                except ValueError:
                    return True  # struktura się rozjechała → traktuj jak rozjazd
                if ci_pick < ci_drop:
                    return True
    return False


def _load_plans() -> Dict[str, Any]:
    try:
        with open(COURIER_PLANS_PATH) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def evaluate(orders_state: Optional[Dict[str, Any]] = None,
             gps_positions: Optional[Dict[str, Any]] = None,
             plans: Optional[Dict[str, Any]] = None,
             now: Optional[datetime] = None,
             write: bool = True) -> List[Dict[str, Any]]:
    """Zwraca listę rekordów (1/kurier wielozleceniowy). Deps wstrzykiwalne (test)."""
    if orders_state is None:
        orders_state = PR._load_orders_state()
    if gps_positions is None:
        gps_positions = PR._load_gps_positions()
    if plans is None:
        plans = _load_plans()
    if now is None:
        now = datetime.now(timezone.utc)

    by_courier: Dict[str, List[str]] = {}
    for oid, rec in orders_state.items():
        if not isinstance(rec, dict):
            continue
        cid = str(rec.get("courier_id") or "")
        if cid and rec.get("status") in PR.ACTIVE_STATUSES:
            by_courier.setdefault(cid, []).append(str(oid))

    results: List[Dict[str, Any]] = []
    for cid, oids in by_courier.items():
        if len(oids) < 2:
            continue
        rec: Dict[str, Any] = {"ts": now.isoformat(), "cid": cid,
                               "n_oids": len(oids), "oids": sorted(oids)}
        anchor = PR._start_anchor(cid, oids, orders_state, gps_positions, now)
        plan = plans.get(cid) or plans.get(str(cid))
        if anchor is None:
            rec["kind"] = "no_position"
        elif not plan or not plan.get("stops"):
            rec["kind"] = "no_plan"
            rec["pos_source"] = anchor[2]
        elif plan.get("invalidated_at"):
            rec["kind"] = "plan_invalidated"
            rec["pos_source"] = anchor[2]
            rec["invalidated_reason"] = plan.get("invalidated_reason")
        else:
            pos = anchor[0]
            rec["pos_source"] = anchor[2]
            stops = plan.get("stops")
            covered = {str(s.get("order_id")) for s in stops}
            missing = sorted(set(oids) - covered)
            if missing:
                rec["kind"] = "coverage_gap"
                rec["missing"] = missing
            else:
                saved_seq = _seq(stops)
                try:
                    canon = PR._apply_canon_order_invariants(stops, orders_state, pos, now)
                    canon_seq = _seq(canon)
                except Exception as e:
                    rec["kind"] = "canon_error"
                    rec["err"] = f"{type(e).__name__}: {e}"
                    results.append(rec)
                    continue
                if canon_seq == saved_seq:
                    rec["kind"] = "ok"
                elif _carried_first_smell(saved_seq, canon_seq):
                    rec["kind"] = "carried_first"
                    rec["saved_seq"] = saved_seq
                    rec["canon_seq"] = canon_seq
                else:
                    rec["kind"] = "canon_divergence"  # plan ≠ kanon-teraz, ale nie carried-first
                    rec["saved_seq"] = saved_seq
                    rec["canon_seq"] = canon_seq
        rec["risk"] = rec["kind"] in RISK_KINDS or rec["kind"] == "carried_first"
        results.append(rec)

    if write and results:
        tmp = GUARD_LOG + ".tmp"
        with open(tmp, "a") as fh:
            for r in results:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        # append atomowo: dopnij tmp do głównego i usuń (proste, bez utraty)
        with open(tmp) as src, open(GUARD_LOG, "a") as dst:
            dst.write(src.read())
        os.remove(tmp)
    return results


def main() -> int:
    dry = "--dry" in sys.argv
    res = evaluate(write=not dry)
    risk = [r for r in res if r.get("risk")]
    cf = [r for r in res if r.get("kind") == "carried_first"]
    by_kind: Dict[str, int] = {}
    for r in res:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    print(f"[carried_first_guard] couriers_multibag={len(res)} "
          f"risk={len(risk)} carried_first={len(cf)} kinds={by_kind}")
    for r in res:
        extra = ""
        if r["kind"] == "coverage_gap":
            extra = f" missing={r.get('missing')}"
        elif r["kind"] in ("carried_first", "canon_divergence"):
            extra = f" saved={r.get('saved_seq')} canon={r.get('canon_seq')}"
        print(f"  cid={r['cid']} n={r['n_oids']} kind={r['kind']} "
              f"risk={r.get('risk')} pos={r.get('pos_source')}{extra}")
    if cf:
        print("⚠ CARRIED-FIRST WYKRYTY (silnik-z-pozycją by tak NIE ułożył):")
        for r in cf:
            print("   " + json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
