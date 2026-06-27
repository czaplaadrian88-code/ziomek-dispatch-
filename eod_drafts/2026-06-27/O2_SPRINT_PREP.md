# O2 RE-SEKWENCJA WORKA — PREP SPRINTU (ETAP 0-3, bez edycji silnika)

**Data:** 2026-06-27 | **Status:** PREP gotowy, WYKONANIE zbramkowane na review 02.07 GO + ACK Adriana
**Lewar:** Ziomek globalnie re-sekwencjonuje worek pod objektywem O2 (overage + λ·czas_late, ready-anchor) — wypełnia martwy czas przed czasówką dostawą po drodze. ACK kierunku JEST (25.06).
**Referencja:** [[bag-resequence-fill-deadtime-candidate-2026-06-25]] · [[sweep-r6-anchor-pickup-vs-ready-2026-06-25]] · [[ziomek-change-protocol]] Załącznik A
**Repo:** `/root/.openclaw/workspace/scripts/dispatch_v2/` | flags: `dispatch_state/flags.json`

> ⚠️ Ten doc żyje w scratchpad (NIE eod_drafts) — repo współdzielone z ≥3 sesjami CC. Przenieść do `eod_drafts/` dopiero przy WYKONANIU (ETAP 6), po rebase na ówczesny HEAD.

---

## ETAP 0 — STAN NA ŻYWO (zweryfikowany 27.06 ~21:40 UTC)

- **git HEAD = `5562e7a`** (liveness-probe, 3. sesja — telegram-intentional-off; tknął TYLKO `observability/liveness_probe.py` + test → ZERO plików O2). Wcześniej `65dadcd` (baseline sesji 14).
- **Aktywne sesje CC (3):** [14] B2 feas-carry-readmit (WIP: `dispatch_pipeline.py`/`shadow_dispatcher.py`/`common.py`) · [3.] liveness-probe (zacommitowana `5562e7a`) · [ja] O2 prep (read-only).
- **Trójka O2 CZYSTA** — `git status` feasibility_v2/route_simulator_v2/plan_recheck = brak zmian, nikt nie edytuje. Potwierdza N-D w mapie sesji 14.
- **Baseline testów:** referencja sesji 14 @65dadcd = **3378 pass / 13 fail pre-existing / 26 skip** (13 faili: courier_reliability ×8, flag_doc_coverage, objm_lexr6_select_faza2::flag_default_off, working_override ×3 — żaden nie dotyczy O2). **O2 EXEC musi re-baseline na ówczesny HEAD** (post-02.07, po committed B2).
- **Shadow-jobs reconcile** (vs [[shadow-jobs-registry]]): `dispatch-bundle-calib-shadow.timer` LIVE co 5 min (flaga `ENABLE_BUNDLE_CALIB_SHADOW=1`, λ=1.5) · one-shot `dispatch-bundle-calib-review.timer` **02.07 07:00 UTC** (at-#168 reminder 02.07) · korpus `bundle_calib_shadow.jsonl` świeży (305 worków/27.06, bundle_improved 14% ≈ improved_O2 ~20%). **NIE dubluję — shadow istnieje, jest referencją.**

## ETAP 1 — ŹRÓDŁO (warstwa-przyczyna, NIE objaw)

**Bug correctness POTWIERDZONY na żywym kodzie:**
`route_simulator_v2._count_sla_violations:625` kotwiczy R6 na symulowanym TSP `pickup_at`, NIE na `pickup_ready_at`. Bliźniak `_compute_per_order_delivery_minutes:703` JUŻ używa `r6_thermal_anchor` (ready). Docstring `r6_thermal_anchor:641` (INV-R6-ANCHOR-CONSISTENCY, audyt 24.06) wymienia konsumentów: `_compute_per_order_delivery_minutes` + `check_feasibility_v2` — **POMIJA `_count_sla_violations` = to jest luka.** Skutek: OR-Tools nagradzany za opóźnianie odbioru → `sla_violations=0` dla planu z realnymi naruszeniami R6 od ready + ślepy na czasówki.

**⚠️ Wzorzec #8 — `plan.sla_violations` to ZMIENNA DECYZYJNA HARD, nie tylko sort:**
Grep konsumentów (`sla_violations` w trójce + dispatch_pipeline):

| Plik:linia | Rola | HARD/SOFT |
|---|---|---|
| `route_simulator_v2._count_sla_violations:625` | objektyw + ANCHOR (rdzeń) | — |
| `route_simulator_v2._select_best_plan:142,836` | selekcja planu (greedy/ortools) | sort |
| `route_simulator_v2:1311` | OBJ_FOODAGE_HARDSLA_FALLBACK | **gate** |
| `feasibility_v2:1135` (SLA_PREEXISTING_BYPASS) | **TWARDA bramka** + re-kalkulacja per-order `:1156-1164` **też pickup_at** (2. pickup-anchor!) | **HARD** |
| `plan_recheck._sweep:688` | objektyw re-seq LIVE | sort |
| `plan_recheck:704` | ck-vs-base gate | **gate** |
| `dispatch_pipeline:585` | objektyw best_effort PRIMARY | sort |
| `plan_recheck._bug4_reseq_shadow:1623` | SHADOW (log-only, NIE live) — at-#188 werdykt 28.06 | shadow |

**Korekta vs notatka:** „trójka" = w rzeczywistości **~8 sitów decyzyjnych w 4 plikach + 2 pickup-anchored kalkulacje** (`_count_sla_violations` ORAZ `feasibility_v2:1156-1164`) które MUSZĄ ruszyć razem (bliźniaki). `plan_recheck:1623` to shadow bug4 (nie żywy bliźniak), ale czyta tę samą symulację → anchor-fix przesunie jego liczby (post-28.06 werdykt, bez kolizji).

## ETAP 2 — HARD vs SOFT + inwersje (TO PRZESĄDZA ZAKRES)

1. **Anchor-fix WZMACNIA HARD correctness** (liczy realne R6 od ready), ALE **ZMIENIA zachowanie twardej bramki** `feasibility_v2:1135` (zacznie łapać naruszenia dziś niewidoczne → więcej kandydatów do best_effort/reject). To **dotyka P0** — nie czysty tweak sortu. → wymaga jawnego ACK Adriana na zmianę zachowania HARD-bramki + replay z dowodem netto.

2. **⚠️ TIER-AWARENESS (korekta Adriana 27.06) — cap NIE jest płaski 35:**
   - `BAG_TIME_HARD_MAX_MIN=35` ale `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN=40` (common.py:2544) = **Tier-3 cap-stretch**. Tier 1/2 → 35 HARD, **Tier-3 → 40** (best_effort path, „max ~5 min ponad R6=35", dispatch_pipeline:681).
   - `bundle_calib_shadow` liczy `overage=max(0,age−35)` PŁASKO (tier-blind) → **over-penalizuje Tier-3** (38 min legalne dla T3, shadow liczy 3 overage).
   - **Engine port O2 MUSI: `overage = max(0, age − cap(tier))`**, cap=35 (T1/2) / 40 (T3); cap-Z też per tier. NIE płaski 35.
   - **Pomiar 02.07 skażony tier-blindnością** → flat-35 może zawyżać improved_O2% (graniczny 19-23% sztucznie nad próg 20%). **Werdykt 02.07 czytać z SEGMENTACJĄ per tier** (czy win trzyma się na T1/2 osobno).

3. **cap-Z = inwersja P-1-adjacent (carried freshness)** — `Z_CAPS=[20,32,35]` w shadow; O2 surowy ślepy na pasmo 20-35 → carry do 90 min (55% rekomendacji carried>R6, med 39,6). Adrian wybrał **Opcję 3 (wąska reguła X/Y/Z, TWARDY cap Z)** w [[carried-vs-coloc-pickup-priority-2026-06-25]]. → **flip MUSI być wąską regułą pod capem Z, NIE surowym O2.** To dotyka P-1 (carried priority) → **DECYZJA ADRIANA** (Z value + tier-awareness Z).

4. **P0 `_assert_feasibility_first`** (dispatch_pipeline:2446, woła :5794) — anchor-fix jest UPSTREAM (zmienia CO feasibility liczy, NIE kolejność HARD-przed-SOFT) → invariant trzyma się. ✅

## ETAP 3 — MAPA KOMPLETNOŚCI (klasa: feasibility + objektyw-sweepa, bliźniaki RAZEM)

Referencyjna implementacja O2 = `tools/bundle_calib_shadow.py:_walk_calib:218` (overage + 1.5·czas_late, ready=`min(ck,pu)`, Z_CAPS). **Sprint = port `_walk_calib` do silnika** + parytet test (engine O2 == shadow O2 na korpusie).

| Klasa | Miejsce(a) | Dotknięte? | Co |
|---|---|---|---|
| objektyw+anchor | `route_simulator_v2._count_sla_violations:612` | TAK | ready-anchor (woła `r6_thermal_anchor`) + człon O2 (overage+λ·czas_late) tier-aware |
| HARD bramka | `feasibility_v2:1135-1185` | TAK | 2. pickup-anchor `:1156` → ready; gate behavior change (ACK) |
| selekcja planu | `route_simulator_v2._select_best_plan:142,836` | TAK | klucz sortu O2 (nie sla_count) |
| foodage fallback | `route_simulator_v2:1311` | TAK (sprawdź) | spójność z nowym objektywem |
| sweep LIVE | `plan_recheck._sweep:688` | TAK | klucz O2 + cap-Z hard filter |
| ck-vs-base gate | `plan_recheck:704` | TAK | spójność |
| best_effort obj | `dispatch_pipeline:585` | TAK (sprawdź) | spójność O2 vs sla_count |
| bug4 shadow | `plan_recheck._bug4_reseq_shadow:1623` | N-D + powód | shadow log-only; liczby przesuną się (post-28.06 werdykt) |
| NOWA flaga | `common.py` ETAP4_DECISION_FLAGS:61 + stała OFF :165 + `decision_flag():262` + fingerprint :284 | TAK | `ENABLE_O2_READY_ANCHOR_SWEEP` (robocza) — 3 checkery + test ON≠OFF |
| NOWA metryka | `shadow_dispatcher` `_AUTO_PROP_PREFIXES` LUB LOCATION A+B | TAK | prefix `o2_` (overage/czas_late/under_z/tier_cap) + test w jsonl |
| stała cap-Z + tier | `common.py` | TAK | `O2_Z_CAP_{T12,T3}` + `O2_OVERAGE_CAP_{T12,T3}` (35/40) — DECYZJA Adriana |
| `_objm_lexr6_shadow` | (zamrożony baseline at#152) | N-D | nie ruszać |

**Bliźniaki potwierdzone:** anchor w 2 miejscach (`_count_sla_violations` + `feasibility_v2:1156`) RAZEM. Objektyw w 4 sortach (sweep/select_best_plan ×2/best_effort) spójnie. greedy↔ortools↔plan_recheck (Załącznik A) RAZEM.

## DECYZJE DLA ADRIANA (przed WYKONANIEM, ETAP 2 stop-gates)

1. **Wartość cap-Z + tier-awareness:** Z ∈ {20,32,35} dla T1/2, i ile dla T3 (40? 45?). Wąska reguła X/Y/Z (Opcja 3) — dokładna forma.
2. **Mapowanie tier→cap dla objektywu O2:** potwierdź 35 (T1/2) / 40 (T3); czy „tier 3" = {slow,new} czy inna grupa; czy carried-freshness dostaje ten sam stretch co new-order admission.
3. **ACK na zmianę zachowania HARD-bramki** `feasibility_v2:1135` (anchor-fix = więcej łapanych naruszeń).
4. **Werdykt 02.07 — segmentacja per tier** (czy improved_O2% trzyma się na T1/2 osobno; flat-35 zawyża T3).

## ETAP 4-7 — STUB (do wykonania PO GO 02.07 + ACK)
- **4 (dowody):** flaga ON≠OFF test; metryka `o2_*` w shadow_decisions.jsonl (assert); parytet engine-O2 == bundle_calib_shadow-O2 (test korpus); PEŁNA regresja vs ówczesny baseline; e2e assess_order przez feasibility→sweep→plan_recheck.
- **5 (pozytyw):** replay ON↔OFF na korpusie ≥tydzień — improved_O2 ≥20% NETTO (segmentowany per tier), regres_O2 <5%, Pareto (świeżość niesionego vs czasówka-late vs total-duration); +2 dni.
- **6 (deploy):** .bak→py_compile→test kanoniczny→`git log -3` (kolizja!)→commit jawne pliki+Co-Authored-By→restart `dispatch-shadow` (off-peak, NIGDY telegram/peak bez OK)→logi→ZIOMEK_LOGIC_REFERENCE update.
- **7 (rollback):** flaga=false (hot) / .bak / git revert.

**DoD:** każde miejsce mapy dotknięte lub N-D+powód; flaga ON≠OFF; metryka w jsonl; parytet bliźniaków + vs shadow; tier-aware cap; pełna regresja zielona; dowód POZYTYWNEGO wpływu (segmentowany); replay-werdykt 02.07; rollback. **Częściowe = niezakończone.**
