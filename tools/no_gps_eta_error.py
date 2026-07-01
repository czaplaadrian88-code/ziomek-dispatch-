#!/usr/bin/env python3
"""B3 FAZA 1 — POMIAR BEZPIECZEŃSTWA: realny błąd ETA fikcji no_gps.

Pytanie: gdy no_gps kandydat był UŻYWANY (PROPOSE), o ile fikcja pozycji
(BIALYSTOK_CENTER + fleet-avg km, travel=max(15,prep)) ZANIŻA przewidziany
odbiór vs REALNY `picked_up_at`? „Kilka minut" czy 15+?

TYLKO ODCZYT. Join: shadow_decisions (no_gps best, PROPOSE, `new_pickup_eta_iso`
= przewidziany odbiór) × sla_log.jsonl (`picked_up_at` = realny odbiór) po oid.

error_min = realny_picked_up − przewidziany_pickup  (dodatni = fikcja ZANIŻYŁA,
kurier przyjechał PÓŹNIEJ niż obiecano → ryzyko zimnej potrawy).

Także delivery-error: przewidziany dowóz nieznany dla no_gps wprost, więc
mierzymy gałąź pickup (to tam pozycja-fikcja uderza) + on-time dowozu z sla_log
(czy te no_gps faktycznie dowiozły na czas).

Rozkład: mediana / p80 / p95 / max. Rozbity po dystansie restauracji od centrum
NIE jest możliwy bez coords restauracji (NIE logowane — patrz OGRANICZENIE), więc
proxy: rozbicie po travel_min fikcji (15 floor vs prep-driven) i po on-time.

⚠️ OGRANICZENIE: log NIE ma coords restauracji/kuriera, więc „kurier z brzegu"
vs „z centrum" nie do rozbicia po realnym dystansie. Proxy = czy realny odbiór
przekroczył fikcję. Co dologować dla pełni: restaurant lat/lng + realna pozycja.

Fail-soft.

L1.2 (2026-07-01): odczyt przepięty na kanon `ledger_io` — (a) shadow_decisions
rotation-aware (stara lista [.1, live] gubiła .2.gz po rotacji), (b) sla_log =
ŻYWY scripts/logs/sla_log.jsonl przez `iter_sla` (stary odczyt celował w MARTWY
dispatch_state/sla_log.jsonl, zamrożony 2026-06-20 → join pusty dla świeżych dni).
Różnice SCHEMATU żywego loga obsłużone jawnie, semantyka metryki pickup BEZ ZMIAN:
  * stemple naive = czas WARSZAWSKI (writer sla_tracker) → `ledger_io.parse_sla_ts`
    (parsowanie naive-jako-UTC dawało +2h błędu joinu — near-miss L1.2);
  * żywy log NIE niesie `on_time` (ready-anchored, liczył go martwy sla_join_worker);
    ma `sla_ok` = delivered−picked_up ≤35 (kotwica ODBIÓR). Metryka dowozu czyta
    on_time, gdy brak → sla_ok, i JAWNIE raportuje którą kotwicę widziała.
"""
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_PARENT = os.path.dirname(os.path.dirname(_HERE))  # .../scripts
if _PKG_PARENT not in sys.path:
    sys.path.insert(0, _PKG_PARENT)

from dispatch_v2.tools import ledger_io  # noqa: E402

WARSAW = ZoneInfo("Europe/Warsaw")


def _parse(ts):
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None


def _peak(dt):
    if dt is None:
        return None
    h = dt.astimezone(WARSAW).hour
    return (11 <= h < 14) or (17 <= h < 20)


def _pctile(sorted_vals, q):
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, int(q * len(sorted_vals)))
    return sorted_vals[i]


def _build_sla_index(records=None):
    """oid -> rekord z picked_up_at (kanon: ledger_io.iter_sla, ŻYWY sla_log)."""
    idx = {}
    if records is None:
        records = ledger_io.iter_sla(None)
    for d in records:
        oid = str(d.get("order_id"))
        if d.get("picked_up_at"):
            idx[oid] = d
    return idx


def analyze(decision_records=None, sla_records=None):
    sla = _build_sla_index(sla_records)
    s = {
        "lines": 0, "parse_fail": 0,
        "nogps_propose": 0, "joined": 0,
        "pickup_errors": [],          # actual_pickup - predicted_pickup (min)
        "pickup_err_peak": [], "pickup_err_off": [],
        "fiction_floor15": 0,         # travel_min == 15 (floor)
        "fiction_prep_driven": 0,     # travel_min > 15
        "ontime_delivered": 0, "late_delivered": 0, "no_outcome": 0,
        "ontime_anchor_ready": 0, "ontime_anchor_pickup": 0,
        "examples": [],
    }
    if decision_records is None:
        decision_records = ledger_io.iter_shadow_decisions(None)
    # `lines` = rekordy JSON (kanon parsuje u siebie; linie puste/nie-JSON
    # pomijane w ledger_io → parse_fail zostaje 0). Semantyka metryk BEZ ZMIAN.
    for d in decision_records:
        s["lines"] += 1
        b = d.get("best") or {}
        if b.get("pos_source") != "no_gps":
            continue
        if d.get("verdict") != "PROPOSE":
            continue
        s["nogps_propose"] += 1
        tmin = b.get("travel_min")
        if isinstance(tmin, (int, float)):
            if tmin <= 15.0:
                s["fiction_floor15"] += 1
            else:
                s["fiction_prep_driven"] += 1
        oid = str(d.get("order_id"))
        rec = sla.get(oid)
        if not rec:
            s["no_outcome"] += 1
            continue
        pred_pickup = _parse(b.get("new_pickup_eta_iso"))
        actual_pickup = ledger_io.parse_sla_ts(rec.get("picked_up_at"))
        if pred_pickup is None or actual_pickup is None:
            continue
        s["joined"] += 1
        err = (actual_pickup - pred_pickup).total_seconds() / 60.0
        s["pickup_errors"].append(err)
        pk = _peak(actual_pickup)
        (s["pickup_err_peak"] if pk else s["pickup_err_off"]).append(err)
        # delivery on-time from sla_log: on_time (ready-anchor, martwy log) albo
        # fallback sla_ok (pickup-anchor, żywy log) — kotwica raportowana jawnie.
        flag = rec.get("on_time")
        if flag is None and "sla_ok" in rec:
            flag = rec.get("sla_ok")
            if flag is not None:
                s["ontime_anchor_pickup"] += 1
        elif flag is not None:
            s["ontime_anchor_ready"] += 1
        if flag is True:
            s["ontime_delivered"] += 1
        elif flag is False:
            s["late_delivered"] += 1
        if len(s["examples"]) < 8:
            s["examples"].append({
                "oid": oid,
                "travel_min_fiction": tmin,
                "pred_pickup": b.get("new_pickup_eta_iso"),
                "actual_pickup": rec.get("picked_up_at"),
                "pickup_err_min": round(err, 1),
                "delivery_on_time": rec.get("on_time"),
                "delivery_min": rec.get("delivery_time_minutes"),
            })
    return s


def _summ(vals):
    if not vals:
        return "brak danych"
    sv = sorted(vals)
    import statistics as st
    return (f"n={len(sv)} median={st.median(sv):.1f} "
            f"p80={_pctile(sv, 0.8):.1f} p95={_pctile(sv, 0.95):.1f} "
            f"max={sv[-1]:.1f} min={sv[0]:.1f}")


def main():
    s = analyze()
    print("=== no_gps_eta_error — B3 FAZA 1 (błąd ETA fikcji) ===")
    print(f"linie: {s['lines']}  parse_fail: {s['parse_fail']}")
    print(f"no_gps best PROPOSE (kandydat UŻYTY): {s['nogps_propose']}")
    print(f"  fikcja travel floor=15 min: {s['fiction_floor15']}  "
          f"prep-driven >15: {s['fiction_prep_driven']}")
    print(f"  bez outcome w sla_log: {s['no_outcome']}")
    print(f"JOINED (pred pickup × realny picked_up): {s['joined']}")
    print()
    print(">>> BŁĄD ODBIORU (realny − przewidziany, min; dodatni = fikcja ZANIŻYŁA):")
    print(f"    OGÓŁEM: {_summ(s['pickup_errors'])}")
    print(f"    peak:   {_summ(s['pickup_err_peak'])}")
    print(f"    off:    {_summ(s['pickup_err_off'])}")
    print()
    # ile w granicach "kilka min" vs 15+
    errs = s["pickup_errors"]
    if errs:
        within5 = sum(1 for e in errs if e <= 5)
        within10 = sum(1 for e in errs if e <= 10)
        over15 = sum(1 for e in errs if e > 15)
        n = len(errs)
        print(f"    |err|: ≤5min {within5}/{n} ({100*within5/n:.0f}%)  "
              f"≤10min {within10}/{n} ({100*within10/n:.0f}%)  "
              f">15min {over15}/{n} ({100*over15/n:.0f}%)")
    print()
    print(f"DOWÓZ on-time (no_gps proposed, z sla_log): on_time={s['ontime_delivered']} "
          f"late={s['late_delivered']}")
    if s["ontime_anchor_pickup"]:
        print(f"    ⚠ kotwica: {s['ontime_anchor_pickup']} rekordów liczone z sla_ok "
              f"(≤35 min od ODBIORU — żywy sla_log nie niesie ready-anchored on_time); "
              f"ready-anchored: {s['ontime_anchor_ready']}")
    print()
    print("przykłady:")
    for e in s["examples"]:
        print(f"  oid={e['oid']} fiction_travel={e['travel_min_fiction']} "
              f"pred={e['pred_pickup']} actual={e['actual_pickup']} "
              f"err={e['pickup_err_min']}min on_time={e['delivery_on_time']} "
              f"deliv={e['delivery_min']}")
    return s


if __name__ == "__main__":
    main()
