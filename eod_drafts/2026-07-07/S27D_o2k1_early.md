# S27-D · O2-K1 — wczesny obraz po flipie (READ-ONLY)

**Pas D / READ-ONLY · bieg 2026-07-07 ~19:26 UTC · flaga `ENABLE_O2_CAPZ_RESEQ`**
Źródło: `logs/shadow_decisions.jsonl` przez kanon `ledger_io.iter_shadow_decisions` (rotation-aware).
Metryka `o2_capz` serializowana z `plan.o2_capz` (route_simulator_v2._capz_reseq_plan → feasibility_v2:866 → serializer). Schemat: `{considered, applied, blocked_by_cap, detour_min, overage_saved_min}`.

## Kiedy flip (pinned)
- `flags.json` mtime = **2026-07-07 19:05:00 UTC** (= 21:05 Warsaw), `ENABLE_O2_CAPZ_RESEQ: true` (linia 281). Hot-reload → efekt natychmiastowy.
- Korroboracja: raport A1 o **17:59 UTC** raportował O2-K1 jako „WSTRZYMANY przez Adriana" → flip nastąpił między 17:59 a 19:05. flag-fingerprint-guard przemielił o 19:10 → werdykt `OK`.
- **Okno pomiaru = 19:05 → 19:26 UTC ≈ 21 minut, wieczór (po peaku, niski ruch).**

## Dowód flaga ON ≠ OFF (integralność)
| okno | decyzji | z `o2_capz` na ≥1 kandydacie |
|---|---|---|
| dziś PRZED flipem (00:00–19:05 UTC) | 229 | **0** |
| dziś PO flipie (19:05–19:26 UTC) | **3** | **3** |

`o2_capz` pojawia się WYŁĄCZNIE po flipie (przed = `None`, bo `_capz_reseq_plan` robi early-return gdy flaga OFF). To czysty dowód, że ścieżka reseq jest żywa i emituje metrykę. ✅

## Wszystkie 3 decyzje post-flip (pełny zrzut — n mały, więc surowo)
| order | @UTC | verdict | best (proponowany) | kandydaci z `o2_capz` (bag_final / considered / blocked_by_cap / applied) |
|---|---|---|---|---|
| 486232 | 19:11:33 | PROPOSE | 509 | 515 (bag2 / 6 / 0 / **0**) · 370 (bag3 / 90 / 49 / **0**) |
| 486233 | 19:11:53 | PROPOSE | 509 | 515 (bag2 / 6 / 0 / **0**) · 370 (bag3 / 90 / 2 / **0**) |
| 486234 | 19:20:14 | PROPOSE | 515 | 509 (bag3 / 90 / 38 / **0**) |

- Wszystkie 3 to konteksty MULTI-ORDER (kandydaci niosą bag 1–2, dodawane nowe zlecenie → przeplot wielostopowy). `considered` 6–90 = enumeracja permutacji realnie się dzieje.
- **`applied = 0` na KAŻDYM kandydacie** (5 ocen carry-kandydatów, 0 zastosowanych). Żaden przeplot nie przeszedł jednocześnie 4 bramek (detour≤8 ∧ carried≤Z=20 ∧ redukcja overage≥2 ∧ SLA nie gorsze).
- `blocked_by_cap` (0/49/2/38) = sufit detouru drive-only realnie filtruje kandydatów — bramka (a) działa.
- `detour_min = 0`, `overage_saved_min = 0` wszędzie (bo `applied=0`).

## Odpowiedzi na pytania Pasa 0
| pytanie | odpowiedź (wczesna) |
|---|---|
| ile multi-order ma `o2_capz` **applied** | **0 z 3 decyzji** (0 z 5 ocen carry-kandydatów) → **applied% = 0%** |
| median detour / gain (applied) | **N/D** — brak zastosowanych reseq w oknie |
| carried-first nienaruszony | **TAK** — `sla_violations = 0` na wszystkich; `applied=0` ⇒ sekwencja = baseline; enumeracja z `lock_first` (niesione zamrożone) |
| regres_o2 = 0 | **TAK** — 0 zastosowanych reseq; strukturalnie strzeżone bramką (d) `sla_violations NIE większe` |

## Werdykt (WCZESNY — NIE końcowy)
🟡 **SMOKE-OK, za wcześnie na ocenę wpływu.** Flip potwierdzony żywy (ON≠OFF czysto), ścieżka aktywnie enumeruje i poprawnie blokuje po detourze, **0 regresji**, carried-first nietknięty. Ale n=3 decyzje w 21 min wieczorem (niski ruch, mało niesionych bagów spełniających „materialny zysk pod sufitem detouru") = **brak dowodu POZYTYWNEGO wpływu**.

O2-K1 z założenia adoptuje RZADKO (wąska Opcja 3: tylko czyste wygrane). 0/3 wieczorem jest nieinformatywne co do wartości. **Pełny obraz wymaga poniedziałkowego typu peaku z realnym niesieniem bagów → werdykt 2-dniowy ~09.07** (zgodnie z planem). Do tego czasu: monitorować, nie wnioskować.

## Uwagi metodyczne
- Read-only: nie tknięto flag/kodu/stanu; liczby z kanonicznego readera (spójnie z A1).
- „applied% = 0%" NIE znaczy „feature nic nie robi" — znaczy „w tym mikro-oknie żadna konfiguracja bagów nie dała materialnego zysku pod sufitem detouru". `considered`/`blocked_by_cap` dowodzą aktywnej ewaluacji.
