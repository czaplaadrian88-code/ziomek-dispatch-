#!/usr/bin/env python3
"""[C2 replay] Offline oszacowanie wpływu korekty prep-bias na kotwicę R6.

NIE dotyka żywego systemu. Łączy:
  * ready_at_log.jsonl  — deklarowana gotowość (declared_ready_iso) + restauracja
                          per order (źródło punktu odniesienia kuchni),
  * sla_log.jsonl       — realny delivered_at per order (przez tools/ontime_lib),
  * prep_bias_table.json— przesunięcie kotwicy per restauracja (prep_bias_anchor).

Logika: korekta prep-bias przesuwa kotwicę termiczną R6 WCZEŚNIEJ o |shift|
(shift = -bias_p80 dla biasu dodatniego), więc EFEKTYWNY bag_time R6 rośnie
o |shift|. Jako najlepszy dostępny offline-proxy „o ile R6 jest ostrzejsza"
używamy realnego wieku termicznego dostawy (delivered_at − declared_ready),
liczonego kontraktem A4 `ontime_lib.compute_on_time`.

Raportujemy per próbka i agregatem:
  * realny on-time (delivered − ready ≤ 35) — PRAWDA niezależna od decyzji,
  * realne breache R6 (>35) — ile,
  * NEAR-MISS band: orders, których baseline bag_time ∈ (35−|shift|, 35]
    → korekta NEWLY-GATE (zmienia decyzję z PASS na REJECT),
  * z tych newly-gated: ile było realnie breachem (korekta SŁUSZNA — chroni
    świeżość) vs realnie on-time (potencjalny FALSE-REJECT),
  * kierunek: korekta NIGDY nie rozluźnia (shift ≤ 0 → bag_time tylko rośnie),
    więc wszystkie flipy są w stronę REJECT (ochrona świeżości).

Fail-soft. python3. Tworzy tylko ten plik (tools/). Nic nie zapisuje na dysk.
"""

import os
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_PARENT = os.path.dirname(os.path.dirname(_HERE))  # .../scripts
if _PKG_PARENT not in sys.path:
    sys.path.insert(0, _PKG_PARENT)

from dispatch_v2.tools import ontime_lib  # noqa: E402
from dispatch_v2 import prep_bias_anchor as pba  # noqa: E402

DISPATCH_STATE = "/root/.openclaw/workspace/dispatch_state"
READY_LOG = os.path.join(DISPATCH_STATE, "ready_at_log.jsonl")
SLA_LOG = os.path.join(DISPATCH_STATE, "sla_log.jsonl")
HARD_MAX = ontime_lib.ON_TIME_THRESHOLD_MIN  # 35.0 (kontrakt A4 = R6 hard)


def _median(xs):
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    m = n // 2
    return float(s[m]) if n % 2 else (s[m - 1] + s[m]) / 2.0


def _build_decisions_and_rest(path=READY_LOG):
    """Z ready_at_log: (decisions_index dla compute_on_time, restaurant_of).

    ready_at_log używa pola `declared_ready_iso` jako deklarowanej gotowości —
    mapujemy je na `pickup_ready_at` (klucz, którego oczekuje ontime_lib.
    compute_on_time). Najświeższy wpis per order wygrywa.
    """
    dec_idx = {}
    rest_of = {}
    for rec in ontime_lib._iter_jsonl(path):
        oid = rec.get("order_id")
        if oid is None:
            continue
        oid = str(oid)
        ready = rec.get("declared_ready_iso") or rec.get("pickup_ready_at")
        if ready:
            dec_idx[oid] = {"pickup_ready_at": ready, "ts": rec.get("ts")}
        r = rec.get("restaurant")
        if r:
            rest_of[oid] = r
    return dec_idx, rest_of


def _build_deliveries(path=SLA_LOG):
    """Z sla_log (już płaski wynik compute_on_time): order_id -> rekord dostawy
    z kluczami delivered_at/picked_up_at/status/courier_id (kontrakt A4)."""
    deliv = {}
    for rec in ontime_lib._iter_jsonl(path):
        oid = rec.get("order_id")
        if oid is None:
            continue
        oid = str(oid)
        d = rec.get("delivered_at")
        if not d:
            continue
        prev = deliv.get(oid)
        if prev is not None:
            pdt = ontime_lib.parse_ts(prev.get("delivered_at"))
            cdt = ontime_lib.parse_ts(d)
            if pdt is not None and cdt is not None and cdt <= pdt:
                continue
        deliv[oid] = {
            "delivered_at": d,
            "picked_up_at": rec.get("picked_up_at"),
            "status": rec.get("status"),
            "courier_id": rec.get("courier_id"),
        }
    return deliv


def run(ready_log=READY_LOG, sla_log=SLA_LOG):
    # indeksy do kontraktu A4 ontime_lib.compute_on_time (mapowanie nazw pól
    # na granicy: declared_ready_iso -> pickup_ready_at).
    dec_idx, rest_of = _build_decisions_and_rest(ready_log)
    deliv_idx = _build_deliveries(sla_log)

    # zbiór orderów do oceny = mają i ready (restaurant) i delivery
    oids = [o for o in rest_of.keys() if o in deliv_idx]

    n_eval = 0
    n_real_breach = 0
    n_real_ontime = 0
    n_grace = 0
    n_newly_gated = 0
    n_newly_gated_correct = 0   # newly-gated AND realny breach (słuszna ochrona)
    n_newly_gated_false = 0     # newly-gated AND realnie on-time (false reject)
    n_already_breach_gated = 0  # baseline już >35 (R6 i tak by odrzuciło)
    n_zero_shift = 0            # restauracja bez dodatniego biasu → korekta no-op

    per_rest = defaultdict(lambda: {
        "n": 0, "real_breach": 0, "newly_gated": 0,
        "newly_gated_correct": 0, "newly_gated_false": 0,
        "shift_min": 0.0, "age_samples": [],
    })

    for oid in oids:
        ot = ontime_lib.compute_on_time(oid, dec_idx, deliv_idx)
        if ot.get("grace") or ot.get("on_time") is None:
            n_grace += 1
            continue
        age = ot.get("delivery_time_minutes")
        if age is None:
            continue
        n_eval += 1
        restaurant = rest_of[oid]
        shift, _src = pba.anchor_shift_min(restaurant)  # <= 0 (lub 0)
        absshift = -shift  # ile minut R6 staje się ostrzejsza (>=0)

        real_breach = age > HARD_MAX
        if real_breach:
            n_real_breach += 1
        else:
            n_real_ontime += 1

        pr = per_rest[restaurant]
        pr["n"] += 1
        pr["shift_min"] = round(shift, 2)
        pr["age_samples"].append(age)
        if real_breach:
            pr["real_breach"] += 1

        if absshift <= 0:
            n_zero_shift += 1
            continue

        # baseline gate = age (proxy). Korekta: efektywny bag_time = age + absshift.
        # FLIP (PASS->REJECT) gdy baseline <=35 ale skorygowany >35, tj.
        # age <= 35 AND age + absshift > 35  <=>  age in (35-absshift, 35].
        if age <= HARD_MAX:
            if age + absshift > HARD_MAX:
                n_newly_gated += 1
                pr["newly_gated"] += 1
                # newly-gated: czy realnie był breachem? Z definicji age<=35 → NIE.
                # ale sprawdzamy względem „prawdziwej" granicy świeżości: te ordery
                # były on-time (<=35) → korekta to potencjalny FALSE-REJECT.
                n_newly_gated_false += 1
                pr["newly_gated_false"] += 1
        else:
            # baseline już breach (>35) → R6 i tak odrzuca; korekta nie zmienia decyzji
            n_already_breach_gated += 1

    # newly_gated_correct z definicji = 0 w tym proxy (newly-gated band to age<=35),
    # bo korekta przesuwa granicę W DÓŁ — łapie ordery które BYŁY on-time.
    # To jest właśnie miara „ile świeżo-granicznych dostaw R6 zacznie odrzucać".
    n_newly_gated_correct = n_newly_gated - n_newly_gated_false

    return {
        "n_join_orders": len(oids),
        "n_eval": n_eval,
        "n_grace_or_no_delivery": n_grace,
        "n_real_breach_gt35": n_real_breach,
        "n_real_ontime_le35": n_real_ontime,
        "real_ontime_rate": round(n_real_ontime / n_eval, 4) if n_eval else None,
        "n_already_breach_baseline_gated": n_already_breach_gated,
        "n_zero_shift_orders": n_zero_shift,
        "n_newly_gated_flips": n_newly_gated,
        "n_newly_gated_were_real_breach": n_newly_gated_correct,
        "n_newly_gated_were_real_ontime_FALSE_REJECT": n_newly_gated_false,
        "flip_direction": "PASS->REJECT only (korekta nie rozluźnia R6)",
        "per_restaurant": per_rest,
    }


def _print_report(res):
    print("=== prep_bias R6 replay (offline, flaga symulowana ON) ===")
    print(f"orders w join (ready∩delivery): {res['n_join_orders']}")
    print(f"ocenione (z realnym wiekiem termicznym): {res['n_eval']}")
    print(f"  grace/brak dostawy pominięte: {res['n_grace_or_no_delivery']}")
    print(f"realne breache R6 (>35min od ready): {res['n_real_breach_gt35']}")
    print(f"realnie on-time (<=35min): {res['n_real_ontime_le35']} "
          f"(rate {res['real_ontime_rate']})")
    print(f"baseline już breach (R6 i tak by odrzuciło): {res['n_already_breach_baseline_gated']}")
    print(f"orders gdzie korekta=0 (restauracja bez dodatniego biasu): {res['n_zero_shift_orders']}")
    print()
    print(f">>> R6 DECYZJE ZMIENIONE przy ON (PASS->REJECT, near-miss band): "
          f"{res['n_newly_gated_flips']}")
    print(f"    z tego były realnym breachem (>35): {res['n_newly_gated_were_real_breach']}")
    print(f"    z tego były realnie on-time (FALSE-REJECT): "
          f"{res['n_newly_gated_were_real_ontime_FALSE_REJECT']}")
    print(f"    kierunek: {res['flip_direction']}")
    print()
    # per restauracja: te z największą liczbą flipów / breachy
    rows = []
    for r, v in res["per_restaurant"].items():
        med_age = _median(v["age_samples"])
        rows.append((r, v["n"], v["real_breach"], v["newly_gated"],
                     v["shift_min"], med_age))
    rows.sort(key=lambda t: (-t[3], -t[2]))  # po newly_gated, potem real_breach
    print("Per restauracja (sort: newly-gated flips, potem realne breache):")
    print(f"  {'restauracja':38} {'n':>4} {'breach':>6} {'flips':>5} {'shift':>6} {'med_age':>7}")
    for r, n, rb, ng, sh, ma in rows[:20]:
        ma_s = f"{ma:.1f}" if ma is not None else "  -"
        print(f"  {r:38} {n:>4} {rb:>6} {ng:>5} {sh:>6} {ma_s:>7}")


def main(argv=None):
    res = run()
    _print_report(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
