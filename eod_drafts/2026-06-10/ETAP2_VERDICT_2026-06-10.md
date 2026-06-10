# ETAP 2 — WERDYKT (sesja CC 2026-06-10 wieczór)

5/5 fixów scoringu z planu po audycie 10.06 — wdrożone, zwalidowane replayem, LIVE po restarcie
dispatch-shadow 19:09:43 UTC (czysty, NRestarts=0, ortools warm-up 54.3ms, login OK).
Design: `ETAP2_DESIGN_scoring_fixes.md`. Replay: `etap2_replay_z02_z10.py` (pełny output `/tmp/etap2_replay_out.txt`).

## Commity + tagi (master, chronologicznie)
| Fix | Commit | Tag | Flaga (kill-switch hot-reload flags.json) |
|---|---|---|---|
| Z-02 sign-guard + Unknown-split | `8bc073c` | `v327-mult-sign-guard-unknown-split-2026-06-10` | `ENABLE_V327_MULT_SIGN_GUARD` (ON) |
| Z-09 serializacja v327_/late_pickup_/pos_from_store | `c58f121` | `shadow-serializer-v327-posstore-2026-06-10` | — (czysta obserwowalność) |
| Z-10 margin finalny + C7 best==score-top | `b97023c` | `f7-margin-final-ranking-2026-06-10` | `ENABLE_F7_MARGIN_FINAL_RANKING` (ON; AUTO_PROXIMITY live-OFF → czysto shadow) |
| Z-11 bramka grafikowa mass-fail | `df2598d` | `v328-heuristic-shift-end-guard-2026-06-10` | `ENABLE_V328_HEURISTIC_SHIFT_END_GUARD` (ON) |
| Z-06 pos_from_store w FAIL12 + C4 | `7dcd230` | `fail12-storepos-strict-2026-06-10` | `ENABLE_FAIL12_STOREPOS_STRICT` (ON) |

Rollback per-fix: flaga w flags.json=false (hot-reload, bez restartu) LUB `git revert <commit>` + restart shadow.
Backupy: `*.bak-pre-etap2-z{02,09,10,11,06}-2026-06-10` (common, dispatch_pipeline, shadow_dispatcher,
auto_proximity_classifier, feasibility_v2).

## Replay 7d (1461 decyzji PROPOSE, 2026-06-04 → 06-10)

### Z-02 (sign-guard + Unknown-split)
- 1322/1454 puli z mnożnikiem ≠1.0 u ≥1 kandydata; **18 flipów zwycięzcy (1.4%)**.
- Przegląd ręczny 15/18 — DWA spójne wzorce, oba korekcyjne:
  1. **Inwersja znaku** (~11 flipów): stary zwycięzca = ujemny score „uratowany" przez ×0.1
     (np. #478239 Tomasz Ch pre_mult −55 cross-quadrant → −5.5 bił czystego Bartka −29.6;
     #478553 Jakub OL −72 → −7.2 bił wolnego Mateusza O −54.6). Po fixie wygrywa mniej zły
     geometrycznie/czystszy kandydat — dokładnie intencja audytu.
  2. **Unknown-split** (~7 flipów, w tym 4 z 07.06 #479042/43/71/175): DODATNI bundle-kandydat
     z pre_mult 130-245 pkt zgnieciony ×0.1 wyłącznie przez strefę Unknown (luka districts)
     przegrywał z wolnym kurierem ~85-125; po fixie mult 0.7 → bundle wygrywa (np. #479042
     Gabriel J 244→170.6 vs Paweł SC 114.9). Zgodne z R-04/R-NO-WASTE (bundle na korytarzu
     nie powinien przepadać przez brak pokrycia ulicy w districts_data).
- Caveat rekonstrukcji: strefy bagów liczone bez delivery_city (bag_context go nie ma) —
  od dziś (Z-09) pola v327_* są w logu i kolejna kalibracja będzie bez rekonstrukcji.

### Z-10 (margin na finalnym rankingu)
- **best ≠ score-top w 901/1320 (68%) decyzji z ≥2 feasible** — selekcja jest mocno
  nie-argmax (demote blind-empty V3.16, late_pickup Opcja B tiering, pos_source bucket).
  Stary margin top1−top2 zawyżał „przewagę best" o medianę **105 pkt** — opisywał dwóch
  innych kandydatów.
- Wpływ na AUTO (7d, shadow): z 53 AUTO — **50 zostaje** (best był score-topem),
  **3 → ACK best_not_score_top** (np. #478253: best 69.6 vs top-other 93.4, stary margin
  +23.8 „pewności" był fikcją). AUTO nie traci wolumenu istotnie, a margin w
  auto_route_context od dziś opisuje realnego zwycięzcę.
- **Wniosek do kalibracji Fazy 7:** progi min_score_margin (15/10/5) były stroione na
  fikcyjnym marginie — przed flipem przeliczyć rozkład NOWEGO marginu (pole
  `auto_route_best_is_score_top` + poprawiony `auto_route_score_margin` już się logują).

### Z-11 (bramka grafikowa w mass-fail)
- 0 zdarzeń V328_OR_TOOLS_MASS_FAIL w 14d journala — guard to uśpione ubezpieczenie
  degraded-mode; walidacja = 9 testów jednostkowych (skip post-shift, fail-open bez
  shift_end/pozycji, clamp speed, naive TZ).

### Z-06 (pos_from_store w FAIL12)
- Ekspozycja realna: **LAST_KNOWN_POS_USED 202×/7d** (rescue ze store) ×
  **FAIL12_SCHEDULE_FAILOPEN 83×/7d** (fail-open aktywny w shadow przez override env).
  Przecięcie (store-pos jako JEDYNY dowód pracy przy braku grafiku) nieznane z historii
  (pole nieserializowane do dziś) → metryka `fail12_storepos_blocked` + warning
  FAIL12_STOREPOS_BLOCKED policzą je od teraz. Tygodniowy przegląd: jeśli blokady łapią
  realnie pracujących kurierów → poluzować (kill-switch) albo skrócić próg wieku.

## Regresja pytest
Baseline pre-ETAP2: 49 failed / 2089 passed. Post: **49 failed / 2145 passed — diff listy
faili PUSTY (zero nowych), +56 nowych testów PASS** (16 Z-02, 4 Z-09, 8 Z-10, 9 Z-11, 7 Z-06
+ regresje współdzielonych plików).

## Koordynacja drzewa
Równoległa sesja ETAP 3 (PANEL_AGREE) commitowała w trakcie (`6e11712`, `f1c78ce` —
panel_watcher.py + shift_notifications/*); dispatch-shadow ich nie importuje → restart
shadow nie wdrożył jej zmian usługowo (panel-watcher restart = jej sesja). Cudze dirty:
tylko `restaurant_company_mapping.json` (JSON valid). dispatch-telegram NIE ruszany.

## Weryfikacja live (po restarcie)
- [x] restart czysty 19:09:43 UTC, zero errorów w journalu
- [x] **smoke E2E 19:15 UTC na ŻYWEJ flocie** (assess_order + _serialize_result, syntetyczny
  order SMOKE_ETAP2, bez zapisu do logu): 8 kluczy `v327_*` w best ORAZ alternatives
  (w tym `v327_mult_sign_guarded`/`v327_min_drop_factor_known`/`v327_unknown_zone_present`),
  `pos_from_store`+`pos_age_min` w obu lokalizacjach, `late_pickup_*`/`new_pickup_*` w best,
  `auto_route_best_is_score_top` w context. **Bonus — Z-10 zadziałał na realnym przypadku:**
  NO_GPS_DEMOTE zdjął cid=515 z topu → best=Patryk (4.5 pkt) NIE jest score-topem →
  auto_route=ACK reason `best_not_score_top`, margin −120.5 (stary kod policzyłby
  margin z dwóch innych kandydatów). Store-rescue też żywy w smoke
  (LAST_KNOWN_POS_USED kid=413 age=23.9 min).
- [ ] potwierdzenie na pierwszej PRODUKCYJNEJ decyzji (rano 11.06: `tail shadow_decisions.jsonl`
  → klucze v327_*/pos_from_store/auto_route_best_is_score_top)
