# L6.C — geometria w selekcji + de-pile (C1+C2+C3 RAZEM) — design sprintu 2026-07-04

**Protokół #0 ETAP 0 (zamknięty):** baseline kanon **4169/0** (po 2 fixach baseline: doc ENABLE_PERF_SLO_ALERT
po at-209 + hermetyzacja test kill-switch v3273 zależnego od żywego zegara traffic-mult — commit `0575f27`).
Multi-sesja: tmux czysty (14=audyt zamknięty, worktree usunięty). atq zrekoncyliowane (at-200 GO / at-201 POZYTYWNY
skonsumowane → tracker; at-202/203/204 dziś auto). Flagi live: OBJM_LEXR6_SELECT=ON, SELECT_SHADOW=OFF,
PENDING_RESWEEP=ON shadow, PENDING_RESWEEP_LIVE=OFF (no-op branch), GLOBAL_ALLOC_WRITE=ON, POST_SHIFT_OVERRUN=OFF.
Recony (3 agenty, pełne raporty w sesji): lexqual / geometry / depile.

**Bramka nieprzekraczalna (C10-oracle 30.06):** flip PENDING_RESWEEP_LIVE bez geometrii w lex_qual =
**279 propozycji spread>8km** (z 2019 alokacji multi-drop; 35,2% łamie R1 8km; przykłady: 484250 18km/r6 73min,
484020 cid=447 24,3km). Dlatego C2+C3 RAZEM; gate zakodowany (nie tylko w głowie).

## ETAP 1 — źródło, nie objaw
Warstwa-przyczyna = **SELEKCJA** (7): klucz jakości `lex_qual` ślepy na geometrię (producent `deliv_spread_km`
w feasibility JUŻ stempluje candidate.metrics — nikt nie czyta przy wyborze) + **_tick** ocenia eventy na
NIEmutowanej flocie (brak claimu → pile-on: 447 proponowany 127×/32 zlecenia, g_maxpile=7).
NIE render, NIE feasibility (HARD nietykalne).

## ETAP 2 — HARD vs SOFT
Człon geometrii = SOFT tie-break PODRZĘDNY wobec osi R6 (ostatni element krotki) → nie osłabia HARD (R6 35
w feasibility nietknięte; geometria działa TYLKO wewnątrz puli, którą HARD już przepuścił). Claim ledger nie
zmienia werdyktów feasibility — zmienia OBRAZ floty między eventami (uczciwszy, nie luźniejszy). Żadna inwersja
P-1..P-7 nie jest dotykana.

## ETAP 3 — MAPA KOMPLETNOŚCI (miejsce → dotknięte?)
| Miejsce | Dotknięte? |
|---|---|
| `objm_lexr6.lex_qual` (kanon) | TAK — człon geometrii za flagą (C2) |
| `_objm_lexr6_shadow._lex_qual`+`_lex_pln` (dp:1167/1183) | TAK — przepięcie na kanon (C1; bajt-parytet przy OFF, cień i tak SHADOW=false) |
| `_best_effort_objm_pick/_shadow`, `_feas_carry_readmit_pick`, `_objm_lexr6_d2_pick` | AUTO przez kanon (już importują `_OL.lex_qual`) — dostają człon geometrii RAZEM, parytet z konstrukcji |
| `_best_effort_sort_key`/`_late_pickup_score_first_key`/`_best_effort_fastest_pickup_key` | N-D ŚWIADOMIE — inne klucze (score-first/fastest-pickup), nie lex_qual; dotknięcie = zmiana zachowania poza zakresem ROOT-7; odnotowane do osobnej decyzji |
| `R1_MAX_DELIV_SPREAD_KM` (feas:90) + `BUNDLE_MAX_DELIV_SPREAD_KM` (common:2559) | TAK — scalenie na 1 kanon `MAX_DELIV_SPREAD_KM` w common (env-override ZACHOWANY, wartość 8.0 = bajt-parytet) |
| bearing 2→1 + cosine (wave_scoring vs feasibility inline ×3) | N-D ŚWIADOMIE — recon: matma NIE bit-identyczna (planar dot vs bearing-space) → scalenie zmienia bonus_r1_corridor = osobny pas z własnym parytetem; człon geometrii używa deliv_spread_km, nie cosine → nie blokuje |
| martwa R7 `LONG_HAUL_DISTANCE_KM=99` (common:1055, feas:472-497) | TAK — usunięcie + przepis testów f21 (sentinel 99) |
| `shadow_dispatcher._tick` (flota między eventami) | TAK — claim ledger za flagą (C3a) |
| `_tentative_assign` (tools/pending_global_resweep:124) | TAK — ekstrakcja do modułu SILNIKA `claim_ledger.py`; resweep importuje z silnika (1 źródło; kierunek tools→engine, nie odwrotnie) |
| `reassignment_global_select` | AUTO — dalej przez global_allocate → wspólne źródło (zero 2. kopii) |
| `_compute_repo_cost_km` (dp ~2108) except→None | TAK — fail-loud + metryka `sentinel_swallow` (C3b) |
| `PENDING_RESWEEP_LIVE` no-op branch (resweep:419) | TAK — zakodowany GATE: LIVE bez `ENABLE_LEXQUAL_GEOMETRY_TIEBREAK` → HOLD+warning (C3c) |
| serializer A+B | AUTO (deny-lista L1.1) — nowe metryki (`objm_geom_*`, `sentinel_swallow`, `claim_*`) wchodzą same; test grep na świeżym oknie po flipie |
| plan_recheck twin spread (:1151) | N-D — producent identycznej matmy, NIE dotykam producenta (czytam istniejącą metrykę); zero zmiany |
| flags.json + ETAP4_DECISION_FLAGS + LOGIC_REFERENCE + checkery | TAK — 3 nowe klucze (2 flagi + 1 próg), doc wymuszony ratchetem |

## Nowe flagi (wszystkie default OFF / neutral)
- `ENABLE_LEXQUAL_GEOMETRY_TIEBREAK` (bool, OFF) — człon `deliv_spread_km` (None→0.0) jako OSTATNI term lex_qual.
- `LEXQUAL_TIME_QUANT_MIN` (float, 0.0=OFF) — kwantyzacja termów czasowych krotki do kubełków N-min (odpala się
  TYLKO gdy geometria ON). Powód: czysty append działa na idealnym remisie floatów; pod scarcity (realne breache)
  może się nie odpalać. Zamiast zgadywać siłę tie-breaku → replay mierzy quant=0 vs 1.0, werdykt z liczbami do Adriana.
- `ENABLE_ENGINE_CLAIM_LEDGER` (bool, OFF) — _tick mutuje flotę claimem po PROPOSE (wspólny `_tentative_assign`).

## ETAP 4/5 — dowody
Test ON≠OFF per flaga; parytet cień↔kanon (usunięcie wyłączenia w test_objm_lexr6_unify, oba stany POST_SHIFT);
INV-LAYER-4 test (2 eventy/1 tick: claim ON → 2. event widzi doładowany worek); pełna regresja vs 4169/0;
replay ON↔OFF na korpusie resweep/shadow_decisions: metryka docelowa = # picków spread>8km ↓ (cel: leczy klasę 279)
+ g_maxpile ↓, NETTO bez regresji R6/committed. Flip = osobny ACK po werdykcie replay + 2 dni obserwacji.

## ETAP 6/7 — deploy/rollback
Kod inertny do restartu (flagi OFF); restart shadow wieczorem off-peak za ACK. Rollback: flagi=false (hot) /
`.bak-pre-l6c-2026-07-04` / git revert per commit.
