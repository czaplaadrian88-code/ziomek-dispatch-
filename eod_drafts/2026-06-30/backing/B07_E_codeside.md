# B07 — KLASA E od strony KODU (serializer): E2 metryka-liczona-nieserializowana / E3 shadow-twin-inne-guardy / E5 temporalna-osiągalność-hooka

**Agent:** B07-E-codeside-serialize · **Lane:** B · **Tryb:** READ-ONLY · **Data:** 2026-06-30 ~14:1x UTC · **Sesja:** tmux 2
**Komplement do lane-C (runtime oracle).** Tu = STRONA KODU: które metryki HARD **giną w serializerze** (compute-but-vanish), zanim trafią do `shadow_decisions.jsonl`.
**Numery linii ze ŚWIEŻEGO grepu (HEAD `8024705`, linie dryfują — re-grep przed użyciem).** Skrypty: `scratchpad/{vanish_analysis,vanish_final}.py`.

---

## 0. METODA + DEFINICJA „GINIE"

**Serializer = 3 mechanizmy widoczności metryki** (`shadow_dispatcher.py`):
1. **Explicit-read** — `m.get("KEY")` / `best_m.get("KEY")` / `getattr(c,"KEY")` wprost wypisany w `_serialize_candidate` (LOCATION A, `:276`) lub bloku `best` (LOCATION B, w `_serialize_result` `:615-824`).
2. **Prefix auto-prop** — `_propagate_prefixed_metrics(base, m)` (`:266`) dokleja KAŻDY klucz `metrics` zaczynający się od jednego z **35 prefiksów** `_AUTO_PROP_PREFIXES` (`:190-263`). Wołane: `out` LOCATION A (`:501`) + `out["best"]` LOCATION B (`:885`).
3. **Plan-subdict** — pola `plan.*` (sequence/strategy/sla_violations/o2_score/...) serializowane osobno w `"plan"` (`:410-427` A / `:742-759` B).

**Metryka GINIE ⟺** klucz `metrics` (a) **NIE** zaczyna się od żadnego z 35 prefiksów **ORAZ** (b) **NIE** jest explicit-read w serializerze. Wtedy wartość liczona w silniku (feasibility/pipeline) **nigdy nie trafia do ledgera** — kalibracja/oracle/replay tej reguły ślepe.

**Korpus metryk (źródła pisania `metrics`):**
- `feasibility_v2.check_feasibility_v2` (zwraca `metrics`, spread przez `**metrics` do `enriched_metrics` `dispatch_pipeline.py:5270`) — **WARSTWA HARD**.
- `dispatch_pipeline.py` `enriched_metrics` literał (`:5269-5606`) + 2 dodatkowe literały (`metrics={...}`): V328 mass-fail fallback (`:5814`), solo-fallback (`:6995`) + mutacje `c.metrics["K"]=` (no_gps/pre_shift `:5859-5884`, inv `:2499`).
- `scoring.py` — **NIE pisze `metrics`** (zwraca tylko score; potwierdzone grepem). `route_simulator_v2` — pisze `plan.*` (osobny kanał).

**Walidacja adwersarialna (ground-truth, NIE deklaracja):** próbka 858 ŚWIEŻYCH decyzji peaku z `/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl` (master ledger). Każdy klucz „ginący" = **0 wystąpień**; klucze kontrolne naprawione (#6/F2) = obecne. Tabela §3.

---

## 1. ⭐ ROOT (źródło, nie objaw): `_AUTO_PROP_PREFIXES` = allowlist BEZ kontroli kompletności

`shadow_dispatcher.py:185-189` docstring H1 (2026-04-25): „Pipeline regularly adds nowe v325_/v326_ keys… serializer trzymał hardcoded explicit list — 14+ kluczy droppowane". **Mechanizm naprawy = dorzucanie prefiksu pole-po-polu / sprintem-po-sprincie** (35 prefiksów narosło do `:263`).

**Patch-nie-źródło (wzorzec #1 protokołu):** ostatnie 2 „naprawy" (audyt #6 + F2, 28-29.06) dodały `would_hard_cap`, `hard_tier_bag_cap`, `post_shift_overrun_`, `end_of_day_salvage` jako **dosłowne stałe-prefiksy** (`:258-263`) — czyli załatały DWIE rodziny, NIE mechanizm. **Brak inwariantu/testu „każdy klucz `metrics` ma prefiks ALBO jest na explicit-liście ALBO świadomie pominięty".** Skutek: **38 kluczy nadal ginie**, z czego ~14 to metryki HARD/bramkowe. To **TEN SAM root** co 11 „compute-but-vanish" z audytu 28.06 (`shadow-jobs-registry` BACKLOG) — domknięte tylko 2 rodziny, reszta otwarta.

**Klasa:** E (kłamiący/niemy przyrząd — ledger pokazuje „brak danych" tam gdzie silnik policzył) + C (patch-na-serializerze zamiast schema/allowlist u źródła). **still_open.**

---

## 2. ⭐ E2 — PEŁNA LISTA 38 GINĄCYCH KLUCZY (świeży file:linia + warstwa + dedup)

Legenda warstwy: **HARD** = liczona w `check_feasibility_v2`, bramkuje/audytuje werdykt · **PROV** = prowenancja/próg (N/sentinel) · **DIAG** = diagnostyka scarcity/inwariant · **SOFT** = kara/telemetria score · **REDUND** = dane obecne osobnym kanałem (plan-subdict) — szum.

| # | Klucz `metrics` | Pisany (świeże file:linia) | Warstwa | Co tracimy w ledgerze | dedup_hint |
|---|---|---|---|---|---|
| 1 | `sla_violations` (detail-lista) | `feasibility_v2.py:1182` | **HARD** | per-order DETAL naruszeń SLA (która dostawa, ile) | R-SLA-detail |
| 2 | `sla_violations_blocking_count` | `feasibility_v2.py:1185` | **HARD** | ile naruszeń realnie BLOKUJE (gateuje werdykt) vs ile policzonych | R-SLA-detail |
| 3 | `sla_violations_pre_existing` | `feasibility_v2.py:1183` | **HARD** | rozróżnienie naruszeń PRE-ISTNIEJĄCYCH vs spowodowanych nowym orderem | R-SLA-detail |
| 4 | `eta_source` | `dispatch_pipeline.py:5289` (+`:5864` `no_gps_fallback`, `:5879` `pre_shift`) | **PROV** | czy ETA odbioru = realny route vs FIKCJA (no_gps_fallback/pre_shift/haversine) | R-ETA-prov + M-sentinel |
| 5 | `pickup_dist_km` | `feasibility_v2.py:649` | **HARD** | dystans napędzający `pickup_too_far` HARD-reject (`:651-652`); w reason-stringu, nie strukturalnie | R-feas-internal |
| 6 | `r6_gold4_gate_recovered` | `feasibility_v2.py:1098` | **HARD** | recovery bramki R6 gold≤4 (ETA_QUANTILE_R6_BAGCAP — JEDYNA ścieżka >35 przechodzi) | R-feas-internal + A6-gr4 |
| 7 | `r6_paczka_exempt_oids` | `feasibility_v2.py:1117` | **HARD** | które ordery R6-zwolnione (paczka thermal-exempt audit, A2 3-HARD-site) | R-feas-internal |
| 8 | `r6_soft_zone_active` | `feasibility_v2.py:1130` | **HARD** | czy R6 w strefie 30-35 (granica twardego 35) | R-feas-internal |
| 9 | `d2_stale_schedule_soft` | `feasibility_v2.py:684` | **HARD** | czy zadziałał fail-open D2 (grafik nieświeży → soft zamiast NO_ACTIVE_SHIFT) | R-feas-internal (asym fail12_) |
| 10 | `d2_soft_penalty` | `feasibility_v2.py:685` | **HARD** | magnitude kary D2 stale-schedule | R-feas-internal (asym fail12_) |
| 11 | `c2_passes` | `feasibility_v2.py:1285` | **HARD** | wynik bramki C2 per-order ≤35 (`USE_PER_ORDER_GATE`→reject `:1301`) | R-feas-internal (own sink c2_shadow_log) |
| 12 | `c2_violations_count` | `feasibility_v2.py:1287` | **HARD** | liczba naruszeń C2 | R-feas-internal (own sink) |
| 13 | `c2_max_elapsed_min` | `feasibility_v2.py:1286` | **HARD** | max elapsed C2 | R-feas-internal (own sink) |
| 14 | `c2_per_order_data_available` | `feasibility_v2.py:1288` | **HARD** | czy C2 miał dane per-order (graceful-degrade) | R-feas-internal (own sink) |
| 15 | `sla_minutes_used` | `dispatch_pipeline.py:5336` | **PROV** | jaki PRÓG SLA zastosowano dla tej decyzji (35 vs tier) | R-threshold-prov (N) |
| 16 | `cs_tier_label` | `dispatch_pipeline.py:5580` | **PROV** | tier kuriera (napędza R6 cap 35/40, new-courier penalty) | R-threshold-prov (N) |
| 17 | `cs_tier_bag` | `dispatch_pipeline.py:5581` | **PROV** | tier z bag (gold/std/slow/new) | R-threshold-prov (N) |
| 18 | `shift_start_min` | `dispatch_pipeline.py:5301` | **PROV** | start zmiany (audyt pre-shift floor A6-gr6 ślepy z ledgera) | R-threshold-prov + A6-gr6 |
| 19 | `shift_remaining_min` | `feasibility_v2.py:773` | **PROV** | ile zmiany zostało (napędza end_of_day_salvage + post-shift) | R-threshold-prov |
| 20 | `fallback_strategy` | `dispatch_pipeline.py:5815` | **DIAG** | że decyzja = V328 heuristic post-mass-fail | R-scarcity-diag + K5 |
| 21 | `fallback_score` | `dispatch_pipeline.py:5816` | **DIAG** | score fallbacku V328 | R-scarcity-diag + K5 |
| 22 | `mass_fail_ratio` | `dispatch_pipeline.py:5817` | **DIAG** | UDZIAŁ floty która mass-failowała feasibility (scarcity!) | R-scarcity-diag + K5 |
| 23 | `mass_fail_count` | `dispatch_pipeline.py:5818` | **DIAG** | ilu kurierów wypadło | R-scarcity-diag + K5 |
| 24 | `fleet_size` | `dispatch_pipeline.py:5819` | **DIAG** | rozmiar floty w momencie mass-fail | R-scarcity-diag + K5 |
| 25 | `solo_fallback` | `dispatch_pipeline.py:6995` | **DIAG** | że kandydat = solo-fallback | R-scarcity-diag |
| 26 | `inv_feasibility_first_violation` | `dispatch_pipeline.py:2499` (`c.metrics[]=`) | **DIAG** | per-kandydat flaga złamania P0-inwariantu (fail-loud `log.error` ŻYJE osobno) | R-inv |
| 27 | `r3_soft_would_block` | `feasibility_v2.py:503` | **SOFT** | czy dynamic-bag-cap by zablokował (R3 soft) | R-soft-telem |
| 28 | `r8_violation_min` | `feasibility_v2.py:642` | **SOFT** | magnitude naruszenia R8 span (bonus_r8_soft_pen ŻYJE) | R-soft-telem |
| 29 | `r1_violation_km` | `feasibility_v2.py:505` | **SOFT** | km naruszenia R1 spread (bonus_r1_soft_pen ŻYJE) | R-soft-telem |
| 30 | `r5_violation_km` | `feasibility_v2.py:574` | **SOFT** | km naruszenia R5 pickup-spread (bonus_r5_* ŻYJE) | R-soft-telem |
| 31 | `wave_bonus` | `dispatch_pipeline.py:5333` | **SOFT** | term score „wave" (czytany w breakdown `:1715`) — niewidoczny | R-soft-telem |
| 32 | `panel_packs_oids_signal` | `dispatch_pipeline.py:5382` | **SOFT** | OID-y rozjazdu panel-packs (rozmiar `panel_packs_signal_size` ŻYJE) | R-soft-telem |
| 33 | `r6_soft_penalty_c3_legacy` | `feasibility_v2.py:1129` | **MARTWY** | kara R6 c3-legacy — **kod-zombie** (tylko gdy `DEPRECATE_LEGACY_HARD_GATES`=True, nigdy) | K (A2 smell#1) |
| 34 | `sla_violations_count` | `feasibility_v2.py:825` | **REDUND** | = `plan.sla_violations` (int) — serializowany w plan-subdict | R-redund |
| 35 | `sequence` | `feasibility_v2.py:821` | **REDUND** | = `plan.sequence` — serializowany | R-redund |
| 36 | `strategy` | `feasibility_v2.py:823` | **REDUND** | = `plan.strategy` — serializowany | R-redund |
| 37 | `total_duration_min` | `feasibility_v2.py:822` | **REDUND** | = `plan.total_duration_min` — serializowany | R-redund |
| 38 | `osrm_fallback_used` | `feasibility_v2.py:824` | **REDUND** | = `plan.osrm_fallback_used` — serializowany | R-redund |

**Podsumowanie warstw:** HARD=14 (#1-14) · PROV=5 (#15-19) · DIAG=7 (#20-26) · SOFT=6 (#27-32) · MARTWY=1 · REDUND=5.

---

## 3. WALIDACJA ADWERSARIALNA — ledger ground-truth (858 świeżych decyzji peaku)

`grep -c '"KEY"'` na `tail -3000` (=858 linii) `scripts/logs/shadow_decisions.jsonl`:

| Klucz | wystąpień | werdykt |
|---|---|---|
| `eta_source`, `c2_passes`, `c2_violations_count`, `d2_soft_penalty`, `d2_stale_schedule_soft`, `sla_violations_blocking_count`, `sla_violations_pre_existing`, `sla_minutes_used`, `pickup_dist_km`, `r6_gold4_gate_recovered`, `r6_paczka_exempt_oids`, `r6_soft_zone_active`, `r3_soft_would_block`, `shift_remaining_min`, `shift_start_min`, `cs_tier_label`, `wave_bonus`, `inv_feasibility_first_violation`, `panel_packs_oids_signal`, `r8_violation_min` | **0** | ✅ GINIE (potwierdzone) |
| `fallback_strategy`, `mass_fail_ratio`, `mass_fail_count`, `fleet_size` | **0** | ✅ GINIE (V328) |
| **kontrola naprawione #6/F2:** `would_hard_cap` | **340** | ✅ ŻYJE (prefiks dosłowny `:263`) |
| **kontrola:** `post_shift_overrun_min` | **359** | ✅ ŻYJE (prefiks `post_shift_overrun_` `:258`) |
| **kontrola:** `fail12_signal` | **3** | ✅ ŻYJE (prefiks `fail12_`) |
| `sla_violations` (substring) | **813** | ⚠ to `plan.sla_violations` (INT count, plan-subdict `:414/746`) — NIE detail-lista `metrics["sla_violations"]` która GINIE (potwierdzone: `route_simulator_v2.py:224 sla_violations:int`) |

**Wniosek:** 0/858 dla całego zbioru ginącego = compute-but-vanish **potwierdzony ground-truth**, nie deklaracja. Naprawy #6/F2 ŻYWE (would_hard_cap 340×, post_shift 359×) — ale to PUNKTOWE, root otwarty.

---

## 4. ⭐ KTÓRE METRYKI **HARD** GINĄ (odpowiedź wprost na zlecenie)

**14 metryk HARD ginie w serializerze** (warstwa `check_feasibility_v2`, bramkują/audytują werdykt):

1. **Rodzina SLA-detal (P1)** — `sla_violations` (detal-lista), `sla_violations_blocking_count`, `sla_violations_pre_existing` (`feasibility_v2.py:1182-1185`). Serializowany TYLKO `plan.sla_violations` (int). **Ślepy: która dostawa breachuje, ile naruszeń realnie BLOKUJE, pre-istniejące vs nowe.** Bramkuje sprint **O2 SLA-anchor (review 02.07, at#168/#200)** — kalibracja ready-anchor↔pickup_at z master-ledgera niemożliwa (musi sięgać `r6_breach_shadow.jsonl`). Cross-ref A6-gr4 / A2 SLA-anchor 3-bliźniaki.
2. **Rodzina R6-internal (P2)** — `r6_gold4_gate_recovered` (`:1098`), `r6_paczka_exempt_oids` (`:1117`), `r6_soft_zone_active` (`:1130`). Audyt R6: która ścieżka >35 przeszła (gold4 ETA_QUANTILE), które ordery paczka-exempt, strefa 30-35. **Ślepy na wewnętrzne decyzje HARD-bramki R6** (najważniejsza reguła).
3. **`pickup_dist_km` (P2)** — `feasibility_v2.py:649`, napędza `pickup_too_far` HARD-reject (`:651-652`). Wartość TYLKO w reason-stringu (`pickup_too_far (X.X km)`), nie jako pole → margines przekroczenia nieanalizowalny strukturalnie.
4. **Rodzina D2-schedule (P2)** — `d2_stale_schedule_soft`, `d2_soft_penalty` (`:684-685`). **Asymetria E2+B:** bliźniak `fail12_*` (ten sam gate schedule-hardening, fail-open) **ŻYJE** przez prefiks `fail12_`, a `d2_*` GINIE → ten sam mechanizm bramki częściowo widoczny, częściowo ślepy.
5. **Rodzina C2-gate (P2, częściowo mitygowana)** — `c2_passes`, `c2_violations_count`, `c2_max_elapsed_min`, `c2_per_order_data_available` (`:1285-1288`). Bramka per-order ≤35 (`USE_PER_ORDER_GATE`→reject `:1301`). **Mitygacja:** ma WŁASNY sink `c2_shadow_log.jsonl` (`feasibility_v2.py:39`, emit `:1291` gdy `not c2_passes`) — ale tylko dla NARUSZEŃ, nie PASS; join do konkretnej decyzji wymaga 2. pliku. Z master-ledgera C2 niewidoczny.

**PROV/threshold (N) granicznie-HARD (P2):** `sla_minutes_used` (`:5336` — który PRÓG SLA), `cs_tier_label`/`cs_tier_bag` (`:5580-5581` — tier napędza R6 cap 35/40), `shift_start_min`/`shift_remaining_min` (`:5301`/`:773`). **Rozsyp progów (N):** nie wiadomo z ledgera jaki próg/tier zastosowano → kalibracja tier-aware R6 (35 T1/2 vs 40 T3) i pre-shift floor (A6-gr6) ślepa.

---

## 5. E3 — SHADOW-TWIN-Z-INNYMI-GUARDAMI-NIŻ-LIVE (strona kodu serializera)

**E3a (w serializerze, best-only):** `_serialize_result` LICZY inline 3 wartości shadow przy SERIALIZACJI, nie przy decyzji, guardem `C.flag(X, True)` (default-ON):
- `prep_bias_min`/`effective_ready_shadow` — `shadow_dispatcher.py:518` `C.flag("ENABLE_PREP_BIAS_SHADOW", True)`.
- `target_pickup_debiased_iso`/`pickup_debias_min` — `:562` `C.flag("ENABLE_PICKUP_DEBIAS_SHADOW", True)`.
**Smell:** liczone TYLKO dla `best` (LOCATION B), NIE dla `alternatives` (LOCATION A) → **asymetria A/B** (kontrfaktyk pickup-debias dostępny tylko dla zwycięzcy; replay alternatyw ślepy). Guard default-True czytany przy SERIALIZE-time (nie decision-time) → jeśli flaga zmieni się między decyzją a serializacją w tym samym ticku — bez znaczenia praktycznego (ten sam proces/tick), ale wartość odzwierciedla serialize-time. **Klasa E3/F (asymetria A/B), P3.**

**E3b (cross-ref do A6-gr1, NIE re-derywuję):** `_objm_lexr6_shadow` (`dispatch_pipeline.py:1097`) liczy `objm_lexr6_*` ZAMROŻONĄ 3-krotką `_lex_qual` (hard-coded `:1122-1126`, NIE post_shift-aware), gdy LIVE `objm_lexr6.lex_qual` (`:29`) jest warunkowo 3/4-krotką (`ENABLE_POST_SHIFT_OVERRUN_PENALTY`). Serializowane `objm_lexr6_*` (prefiks `objm_`) = **KŁAMIĄCY PROXY gdy flaga ON** (shadow-cień rankuje INACZEJ niż live). **Dedup: TEN SAM root K1/A6-grupa1 (frozen `_lex_qual`)** — raportowane przez agenta A6, tu tylko cross-ref że objawia się w serializowanej metryce E3. **still_open, dedup→A6-gr1.**

---

## 6. E5 — TEMPORALNA OSIĄGALNOŚĆ HOOKA (sygnał rodzi się downstream)

**Hook serializacji = PROPOSAL-TIME:** `_serialize_result` wołany RAZ w `shadow_dispatcher.py:1215` (`record = _serialize_result(result, eid, latency_ms)`), na FINALNYM `PipelineResult` zwróconym z `assess_order`.

**E5a — NIE problem (zweryfikowane):** pola result-level redirect (`commit_divergence_redirect` `:6583`, `late_pickup_shadow` `:6703`, `min_delivered_at_shadow` `:6705`, `best_effort_r6_redirect` `:6883`) ustawiane WEWNĄTRZ `assess_order` PRZED zwrotem → serializer czyta je przez `getattr(result, ..., None)` (`:847-866`) = ZŁAPANE. Mutacje selekcji (`_best_effort_objm_pick`→`best_effort_objm_*`, `_pln_pure_resort`→`pln_ab_flipped`, `_demote_blind_empty`) piszą na `top[0].metrics` — ten SAM obiekt dict co `result.best.metrics` → serializacja (później) ZŁAPIE (referencja). Brak E5 tu.

**E5b — PLAUSIBLE (downstream-of-hook):** `plan_recheck` (timer 5 min, K2 „cofacz") REGENERUJE `courier_plans` + re-aplikuje `_apply_canon_order_invariants` (`plan_recheck.py:1478`) **PO** zapisaniu wpisu `shadow_decisions`. Serializowane `plan.o2_score`/`max_carried_age`/`sequence` (`:417-418/749-750`) = stan PROPOSAL-TIME, NIE po recanon. **Każda rozbieżność wprowadzona przez plan_recheck (resekwencja, carried-first-relax, o2 re-seq) jest NIEWIDOCZNA w `shadow_decisions`** — sygnał rodzi się downstream hooka. Mitygacja: plan_recheck ma własny `plan_recheck_log.jsonl` (osobny sink). **Klasa E5/H, P3 PLAUSIBLE** (cross-ref unified-audit K2; nie hard-defect — inherentna własność event-logu proposal-time).

**E5c — NIE problem:** mutacje `c.metrics["eta_source"]="no_gps_fallback"` (`:5864`) / `="pre_shift"` (`:5879`) dzieją się PO konstrukcji Candidate ale PRZED serializacją (ten sam dict ref) → temporalnie OK. `eta_source` ginie z powodu E2 (brak prefiksu/explicit), NIE E5.

---

## 7. DEDUP → ROOTY (anty-double-count)

| Root (dedup_hint) | Klucze | Relacja do rootów Fazy A |
|---|---|---|
| **R-serializer-allowlist** (META) | mechanizm `_AUTO_PROP_PREFIXES` explicit-or-prefix bez kontroli kompletności | **NOWY root klasy E** (źródło wszystkich 38). = ten sam root co 11 „compute-but-vanish" 28.06 (#6/F2 załatały 2 rodziny) |
| R-SLA-detail | #1-3 | ⊂ A6-grupa4 / A2 SLA-anchor (instrument O2 ślepy z master-ledgera) |
| R-ETA-prov | #4 eta_source | most do **M-sentinel** (BIALYSTOK_CENTER) + A6-grupa3 (no_gps/pre_shift equal-treatment) — fikcja-ETA niewidoczna |
| R-feas-internal | #5-14 | wewnętrzne metryki HARD-bramek (pickup_too_far/R6/D2/C2) |
| R-threshold-prov (N) | #15-19 | klasa N (rozsyp progów 35/40) + A6-grupa6 (pre-shift floor, shift_start ślepy) |
| R-scarcity-diag | #20-25 V328 | most do **unified-audit K5** (sentinele kurczą pulę→V328→best-effort pile-on) — decyzje pod mass-fail nieidentyfikowalne |
| R-inv | #26 | P0-strażnik (fail-loud log ŻYJE, strukturalny marker ginie) |
| R-soft-telem | #27-32 | klasa SOFT (kary ŻYJĄ przez `bonus_`, raw-violation ginie) |
| R-redund | #33-38 | szum (dane w plan-subdict / kod-zombie) — NIE liczyć jako lukę |
| E3-objm-shadow | objm_lexr6_* | ⊂ **A6-grupa1 / K1** (frozen `_lex_qual`) — cross-ref |
| E5-plan-recheck | plan.* downstream | ⊂ **unified-audit K2** (plan_recheck cofacz) — cross-ref |

**Anty-double-count:** R-redund (#34-38) i #33 (martwy) = NIE liczyć jako realne luki widoczności (dane obecne osobno / kod nieosiągalny). Realne otwarte = **R-serializer-allowlist (META) + 14 HARD + 5 PROV + 7 DIAG + 6 SOFT.** E3b/E5b deduplikują do A6-gr1/K2 (nie nowe rooty).

---

## 8. TABELA POKRYCIA (jawne — nie cisza)

**ZBADANE (świeży grep + ledger-oracle):**
- `shadow_dispatcher.py` — `_AUTO_PROP_PREFIXES` (`:190-263`, 35 prefiksów), `_propagate_prefixed_metrics` (`:266`, wołania `:501`/`:885`), `_serialize_candidate` LOCATION A (`:276-502`), blok `best` LOCATION B (`:615-824`), `_serialize_result` (`:505-1215` hook).
- `feasibility_v2.py` — WSZYSTKIE `metrics[...]=` (`:445-1288`, ~70 zapisów).
- `dispatch_pipeline.py` — `enriched_metrics` literał (`:5269-5606`), V328-literał (`:5814`), solo-fallback-literał (`:6995`), `c.metrics[]=` mutacje (`:2499`, `:5859-5884`).
- `scoring.py` — potwierdzone: NIE pisze `metrics`.
- `route_simulator_v2.py` — `plan.sla_violations` typ (`:224` int) — potwierdzenie distinct od detail-listy.
- Ledger: 858 świeżych decyzji peaku, 25 kluczy grep-zweryfikowanych (20 vanish=0, 5 kontrola>0).

**LUKI POKRYCIA (jawne):**
1. **Inne writery `metrics` poza feas+pipeline+scoring** — NIE prześwietliłem `wave_scoring.py`/`pln_objective.py`/`ml_inference.py` pod kątem własnych zapisów do `c.metrics` (zwracają wartości doklejane w pipeline; założenie że nie piszą bezpośrednio — NIE potwierdzone grepem dla każdego). **Faza C/B: dogrepować `\.metrics\[` w tych 3.**
2. **LOCATION A vs B asymetria explicit-listy** — NIE zrobiłem pełnego diff KTÓRE explicit-keys są w `best` (B) ale nie w `_serialize_candidate` (A) (np. `target_pickup_at`, `effective_start_at`, `pre_shift_clamp_applied`, debias — best-only). To osobna pod-luka B/F (kontrfaktyk alternatyw ślepy) — zasygnalizowane §5 E3a, nie wyliczone 1:1.
3. **Realność „HARD" per klucz** — sklasyfikowałem z warstwy pisania (feasibility=HARD) + reason-grep; NIE prześledziłem każdej ścieżki czy wartość realnie zmienia werdykt vs czysta telemetria (F2 komentarz: „decyzyjnie-NEUTRALNE — liczone zawsze, doklejane tylko do logu" — czyli vanish NIE psuje decyzji LIVE, psuje OBSERWOWALNOŚĆ/kalibrację). **Severity skalowane pod „blokuje kalibrację/flip", nie „psuje live".**
4. **Cross-repo serializery** (konsola `feed.py`, apka `courier_orders` build_view) — POZA zakresem (renderują z `shadow_decisions`, nie tworzą go; klasa J osobny agent). STOP na dyspozytorni.
5. **E5b plan_recheck-downstream** = PLAUSIBLE, nie CONFIRMED (nie zmierzyłem ile decyzji realnie rozjeżdża się po recanon vs proposal-time) — to oracle Fazy C.

**NIE-luki (świadomie):** Mailek/Papu (granica). Runtime oracle wartości (lane C). Twin-graf A6 (nie re-derywuję — cross-ref).

---

## 9. HANDOFF

- **Lane C (oracle):** zweryfikuj że naprawione #6/F2 (would_hard_cap/post_shift) realnie ŻYWE per-decyzja (sample pokazał 340/359 — VALIDATED-świeże). Dla ginących HARD (SLA-detail/R6-internal): potwierdź że alternatywny sink (`r6_breach_shadow.jsonl`, `c2_shadow_log.jsonl`) pokrywa lukę PRZED sprintem O2 02.07 — inaczej kalibracja SLA-anchor ślepa.
- **Faza E/F (PoC):** kanon docelowy = **serializer ze schema/allowlist + test kompletności** („każdy klucz `metrics` ∈ {prefiks} ∪ {explicit} ∪ {świadomie-pominięte}") zamiast doklejania prefiksów sprintem. To zamyka root R-serializer-allowlist RAZ (nie 35. prefiks po raz N-ty).
- **Faza D:** R-ETA-prov (`eta_source`) + R-scarcity-diag (V328 mass_fail_*) to mosty do M-sentinel i K5 — dashboard entropii ich potrzebuje, dziś niewidoczne.
