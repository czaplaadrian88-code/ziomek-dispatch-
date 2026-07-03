# frozen-objektyw P0 — MEASURE-FIRST + PROJEKT (read-only analiza)

**Data:** 2026-07-03  **Tryb:** READ-ONLY (zero zmian repo; zapisy tylko w scratchpadzie)
**Kod żywy:** KANON `plan_recheck.py` PISZE schema:2 (merge nastąpił — 128 rek. schema-2 dziś, ostatni 11:24).

---

## 0. TL;DR

- **Objektyw-tripwire (cały sens schemy-2) jest DZIŚ MARTWY na żywych danych:** `frozen_total_duration/frozen_sla = null` w 128/128 rekordach → `obj_tripwire` zwraca `None` dla KAŻDEGO → oracle raportuje `obj-tripwire oceniale: 0  NARUSZENIA: 0`. Działa TYLKO na syntetycznym selfchecku.
- **Rekomendacja: WARIANT (a-faithful)** — policz frozen-objektyw W ŻYWYM TICKU przez ten sam silnik (`_simulate_sequence`+`_count_sla_violations`, jak oracle `score_sequence`), reużywając ROZGRZANY już w tym samym ticku cache OSRM. **Marginalny koszt OSRM ≈ 0** (nie ~6 wywołań — patrz §3). Fresh i frozen na TEJ SAMEJ osi silnika → tripwire wiarygodny.
- **Wariant (a-plan-reuse)** (policz frozen z `plan.predicted_at`) = **ODRZUCONY**: niewierny (inny model czasu niż fresh) → fałszywe naruszenia tripwire.
- **Wariant (b)** (świadomie dopuść OSRM w loggerze) = też tani (plan_recheck to oneshot 5-min, NIE hot-path p50 620ms), ale zbędny — bo (a-faithful) i tak jest ~zero-OSRM dzięki cache.

---

## 1. Co robi logger i JAKI OSRM już płaci (nie „zero-OSRM")

`_bug4_reseq_shadow` (plan_recheck.py:1891) na gałęzi RETIME (call-site l.2217, worek bez zmian → tylko re-czasowanie) JUŻ dziś:
1. **N świeżych solverów** — pętla `for newoid in sims: simulate_bag_route_v2(...)` (l.1927-1933). N = `n_orders` (śr. ~2.8). Każdy = OR-Tools + `_simulate_sequence` z pełnym OSRM + **floor picked_up `route(pickup,drop)`** dla ODEBRANYCH (route_simulator_v2.py:638).
2. **2 przejścia drive** `_osrm_drive_min_sum(pos, fresh_coords)` i `(pos, frozen_coords)` — sekwencyjne `route` pos→c0→c1… (śr. 9.7 legów/rekord łącznie fresh+frozen).

⇒ Logger NIE jest zero-OSRM. „Constraint zero-OSRM" z raportu dotyczy tylko tego, żeby NIE DOKŁADAĆ nowego OSRM. Kluczowa obserwacja: OSRM potrzebny do frozen-objektywu (legi frozen + floor pickup→drop dla odebranych) **to DOKŁADNIE te same pary coords, które powyższe kroki już przeliczyły w TYM SAMYM procesie**. `osrm_client` ma module-level `_route_cache` (RLock, TTL) → powtórne `route(a,b)` = cache-hit. Więc marginał ≈ 0.

`_simulate_sequence` „extra" OSRM = **jedno** miejsce: l.638 `osrm_client.route(ref_pickup, ref_drop)` pod `ENABLE_PICKED_UP_DROP_FLOOR` dla węzła dostawy o statusie `picked_up` (leg pickup→drop NIE istnieje w trasie, bo jedzenie już w worku — plan ma dla nich tylko dropoff). Warunki odpalenia: flaga ON (LIVE) + `ref_status=="picked_up"` + niepuste, ≠(0,0) pickup i drop coords + `picked_up_at` niepusty.

---

## 2. MEASURE-FIRST (żywy log `dispatch_state/bug4_reseq_shadow.jsonl`, 2662 rek.; schema-2 = 128, wszystkie 2026-07-03)

| metryka | wartość |
|---|---|
| schema-2 rekordów (fresh wypełniony) | **128/128**, wszystkie z fresh, **frozen=null 128/128** |
| tempo | ~128/dzień (≈ 1 rek./tick 5-min w godzinach aktywnych) |
| `n_orders` rozkład | 2:61, 3:40, 4:15, 5:12 (śr ~2.8) |
| **`seq_differs`=FALSE** (pełna trasa identyczna → **frozen_obj ≡ fresh_obj, |Δ|=0 BEZ liczenia**) | **70 (54.7%)** |
| `seq_differs`=TRUE (trasa różna → frozen wymaga wyceny) | 58 (45.3%) |
| `deliv_seq_differs`=TRUE (kolejność DOSTAW inna — realny reseq) | 32 (25.0%) |
| **śr. odebranych (picked_up) / rekord** = liczba floor-`route(pickup,drop)` | **0.80** (rozkład 0:65,1:39,2:15,3:5,4:2,5:2) — wszystkie cache-hit z fresh-solve |
| śr. legów drive już zOSRM-owanych / rekord (fresh+frozen) | 9.7 |
| `invariant_violation` (drive delta<−0.5 = drive-suspect) | 19 (14.8%) |

**Rozkład |fresh_obj − frozen_obj| (min):**
- Dla `seq_differs`=FALSE (54.7%): **dokładnie 0** (identyczna sekwencja węzłów ⇒ identyczny objektyw) — dowodliwe bez OSRM.
- Dla `seq_differs`=TRUE (45.3%): **NIE DO POLICZENIA z żywego logu** — rekord NIE zapisuje per-order coords, tylko etykiety sekwencji (`frozen_seq`,`fresh_seq`). Oracle `score_bag` potrzebuje coords → z logu ich nie ma. To samo w sobie finding: **log nie jest samowystarczalny do offline-rekonstrukcji frozen.**
- **Proxy (drive) wśród `deliv_seq_differs`** (n=32): median |Δdrive|=2.55, p80=6.70, max=12.80 min. UWAGA: drive to ZŁA oś (patrz niżej).
- **Oracle selfcheck (jedyny punkt z policzonym frozen):** case carried A + future-ready B → **obj_delta = 12.6 min** (frozen_total 57.3 vs opt 44.7), a **drive_delta = −3.1** (fresh jedzie WIĘCEJ, ale dowozi 12.6 min lepiej przez carried-first). Silnik vs niezależny walk `|Δ|=0.000`, determinizm OK. To empirycznie pokazuje: gdy sekwencje się różnią, materialność objektywu potrafi być duża i przeciwna do znaku drive.

**% gdzie fresh > frozen (podejrzenie regresji RETIME):**
- Na osi **DRIVE**: wśród `deliv_seq_differs` 16/32 ma `fresh_drive>frozen_drive` (delta<0) — to **LEGALNE carried-first / reorder pod postój**, NIE regresja (dlatego drive = zła oś).
- Na osi **OBJEKTYWU** (właściwa): dziś **0 ocenialnych / 128** (frozen=null). Z optymalności fresh (solver minimalizuje objektyw, frozen to dopuszczalna sekwencja) oczekiwane `fresh_obj ≤ frozen_obj`; każdy `fresh>frozen` = **suboptymalny OR-Tools (residual)** — DOKŁADNIE to, co tripwire ma łapać, a czego dziś NIE widzi.

Oracle reverdict (całość 2662): `deliv_seq_differs 22.2%`, stary drive-suspect 11.1% = **296/296 wrong-axis FP** (carried-first), skorygowany suspect skażenia **0.0%**. Werdykt: `GO(proxy-certyfikowany)` z CAVEAT: „pełny per-rekord tripwire czeka na frozen_obj (dziś null)".

---

## 3. PROJEKT — 2 warianty + rekomendacja

### Wariant (a-faithful) ⭐ REKOMENDOWANY — policz frozen w ticku przez silnik, reużyj cache
**Jak:** po sukcesie fresh-solve, zbuduj węzły frozen z `sims` (jak oracle `_build_nodes`), zmapuj kolejność `existing_plan.stops` na indeksy węzłów = `frozen_node_seq`, policz `R._simulate_sequence(nodes, leg, frozen_node_seq, now)` + `R._count_sla_violations(...)` → `frozen_total_duration`, `frozen_sla`. (To dosłownie oracle `score_sequence` na sekwencji frozen.)
- **Fast-path:** `seq_differs`==FALSE (54.7% rekordów) → `frozen := fresh` bez ŻADNEGO liczenia (sekwencja węzłów identyczna).
- **Wierność:** fresh i frozen liczone TYM SAMYM `_simulate_sequence` (te same dwell, ten sam floor picked_up, ta sama kotwica/now) → oś porównywalna, tripwire poprawny.

**Koszt OSRM (marginał, per rekord):**
- Legi frozen: `leg(i,j)` = `route(coordA,coordB)` dla kolejnych węzłów frozen = **te same pary co `_osrm_drive_min_sum(pos, frozen_coords)`**, które logger właśnie policzył → **100% cache-hit**.
- Floor `route(pickup,drop)` odebranych (śr 0.80/rek.): **cache-hit z fresh-solve** (te same coords, status picked_up).
- ⇒ **marginał ≈ 0 uncached route/rekord.** Worst-case (gdyby cache pudłował, np. mikro-różnica rounding coords) ≤ ~len(frozen_seq)+served ≈ 5.6 route/rek. Przy capie 20 rek./tick i p50 OSRM ~11 ms = **≤ ~1.2 s DOŁOŻONE raz na 300 s tick oneshot** — nieistotne, plan_recheck NIE jest hot-path proposal (p50 620 ms dotyczy dispatch-shadow).
- **Ryzyko:** minimalne — logger dalej statement/return None, log-only; jedyny nowy wkład to arytmetyka + (cache-hit) OSRM. Fail-soft: brak coords/leg → zostaw `frozen=null`+nota (dziś już tak dla fresh guardów).
- **Wierność vs oracle:** wysoka — używa TYCH SAMYCH funkcji silnika co oracle `score_bag`; parytet do udowodnienia testem (patrz DoD).

### Wariant (a-plan-reuse) — policz frozen z `plan.stops[].predicted_at` — ❌ ODRZUCONY
Plan stops mają `predicted_at` (przeliczone w tym ticku przez `_retime_stops`) + `coords` + `dwell_min`. Kuszące: `frozen_total ≈ (ostatni predicted_at − now)`, `frozen_sla` z porównań per-order.
**Dlaczego NIE:** `_retime_stops` (l.938) używa INNEGO modelu czasu niż `simulate_bag_route_v2` (źródło `fresh_total`):
1. `osrm_client.table` (macierz), nie `route`; consecutive-cell — inne API/wartości niż leg fresh.
2. **BRAK floor picked_up→drop** (l.625-646 sim NIE jest odtworzony) → predicted_at odebranych ZANIŻONE względem osi silnika.
3. Ryzyko dwell/clamp skew (retime clampuje tylko odbiory do `czas_kuriera_warsaw`; sim ma pełny `if t<ready` + floor).
⇒ frozen z predicted_at byłby SYSTEMATYCZNIE NIŻSZY niż fresh na innej osi → fałszywe `fresh>frozen` = **fałszywe residuale**. Niewierne = gorsze niż null.

### Wariant (b) — świadomie dopuść OSRM w loggerze (bez cache-reuse martwienia się)
Policz frozen przez silnik BEZ zależenia od cache-hit (po prostu zaakceptuj koszt).
- **Koszt:** górna granica jak worst-case (a): ~5.6 route × 20 rek. × 11 ms ≈ 1.2 s/tick raz na 5 min. **Budżet OK** (oneshot, nie hot-path). Ale to nadmiarowe — (a) i tak trafia w cache.
- Sensowny tylko jako fallback mentalny; realnie (a-faithful) go zawiera.

### Rekomendacja
**(a-faithful) z fast-path dla `seq_differs`=FALSE.** Zero-OSRM w praktyce (cache), 100% wierne, ożywia tripwire na całym 128/128. Dodatkowo: warto rozważyć drobny fallback — gdy scoring frozen zawiedzie (brak coords/OSRM), zostaw `frozen=null`+istniejąca nota (nie regresuj do fałszu).

---

## 4. Proponowany DoD

1. **frozen NIE-null na żywo:** `frozen_total_duration`/`frozen_sla` wypełnione dla ≥99% schema-2 rekordów, w których fresh jest non-null i frozen-sekwencja ma komplet coords (reszta → jawnie null+nota, ten sam guard co fresh). Weryfikacja: po restarcie/następnym ticku `grep '"frozen_total_duration": [0-9]' bug4_reseq_shadow.jsonl` > 0; oracle reverdict `obj-tripwire oceniale` skacze z 0 → ~n.
2. **Tripwire na OBU osiach żywcem:** `obj_tripwire` (lex: sla, potem total, `_EPS=0.05`) ocenialny per-rekord; drive-tripwire pozostaje diagnostyką. Kontrakt: `fresh_sla ≤ frozen_sla` oraz `fresh_total ≤ frozen_total + _EPS` — naruszenie = FLAGA residual (suboptymalny OR-Tools) do inspekcji, NIGDY cichy drop.
3. **Parytet z oracle:** na korpusie replay (te same rekordy/coords) live-`frozen_total` == oracle `score_bag`→`frozen_total` w `|Δ|<_EPS` (0.05) i identyczne `frozen_sla`; test dowodzi, że in-logger scorer == offline brute-force scoring frozen-sekwencji. Zachować selfcheck `|Δ engine vs independent|=0.000`.
4. **Fast-path poprawny:** dla `seq_differs`=FALSE `frozen==fresh` dokładnie (test: syntetyczny worek z identyczną sekwencją → delta 0, zero wywołań OSRM ponad fresh).
5. **Bez regresji latencji ticku plan_recheck:** zmierz czas ticku (lub `summary` timing) przed/po; dołożone ≤ ~1.5 s worst-case, oczekiwane ~0 (cache). PEŁNA regresja pytest ZIELONA vs baseline; logger dalej statement (return None, wejścia niemutowane, byte-parytet ścieżki decyzji — jak test schema-2).
6. **Fail-soft zachowany:** każdy wyjątek scoringu frozen = `frozen=null`+nota, nie psuje fresh ani retime.

---

## 5. Pliki / kotwice
- Logger: `plan_recheck.py:1891` `_bug4_reseq_shadow` (call-site l.2217, gałąź RETIME w `_gap_fill_plans`).
- Silnik osi: `route_simulator_v2.py:559` `_simulate_sequence` (floor l.625-646), `:654` `_count_sla_violations`.
- Model retime (dlaczego predicted_at niewierny): `plan_recheck.py:938` `_retime_stops` (`osrm_client.table`, dwell 1.0/3.5, brak floor).
- Oracle wzorzec scoringu frozen: `tools/bug4_reseq_oracle.py` `_build_nodes`/`score_sequence`/`score_bag`; reader `read_obj`/`obj_tripwire`.
- Cache OSRM (baza „zero-marginał"): `osrm_client.py:48` `_route_cache` + `:53` RLock + `_cache_get/_cache_set`.
- Log: `/root/.openclaw/workspace/dispatch_state/bug4_reseq_shadow.jsonl` (2662 rek., 128 schema-2).
