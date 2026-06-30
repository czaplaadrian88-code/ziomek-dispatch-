# AUTON-02 — rekalibracja bramki auto-assign pod „plaster D" (2026-06-30)

**Decyzja Adriana 30.06:** cel autonomii ~62% wolumenu (plaster D), start od razu od D (nie ramp 12%→). Dowód: analiza fizyczna 14 dni — ZGODA≈OVERRIDE w wyniku dostawy, plaster D breach ~5,5% vs 9% baseline ludzki. Detal: [[autonomy-readiness-2026-06-30]].

**Status:** DESIGN do ACK. NIC nie zaimplementowane. Flip = osobny krok, OFF-PEAK, po kontrolowanym 1. wykonaniu.

⚠ **CAVEAT pomiaru (protokół ORACLE-CAVEATS):** `r6_actual_min`/breach = prawda PRZYCISKOWA (panel/SLA), nie GPS-fizyczna (~3 min bias). Liczby 5,5%/9% = **proxy-certyfikowane**; wniosek WZGLĘDNY (auto-plaster ≤ baseline) trzyma (bias ~stały w obu).

---

## 1. Co to jest „plaster D" (zmienna decyzyjna)
Z mapowania load_bucket→pool_feasible (calibration 14d): `niedobor`=pool 0-1, `srednio`=pool 2, `luzno`=pool≥3. Więc **non-scarcity (luzno+srednio) ⟺ `pool_feasible ≥ 2`**. Bramka JUŻ ma pool_feasible → ZERO nowej hydrauliki load-bucketowej.

**Plaster D = wszystkie poniższe naraz:**
- verdict == PROPOSE
- pool_feasible ≥ 2 (non-scarcity)
- pos_source ∈ INFORMED_POS_SOURCES (wie gdzie kurier) ∧ nie pos_from_store
- NIE czasówka (prep<60) ∧ NIE paczka/firmowe (address_id ∉ {161,232-236})
- NIE late-pickup (redirect/committed_breach/needs_extension) — hard rule, człowiek
- NIE R6/commit redirect ∧ best nie best_effort ∧ plan sla_violations==0 — hard rule
- NIE kurier w rampie „new"
- score ≤ AUTO_ASSIGN_SCORE_DISTRUST_CEILING (90) — sufit nieufności (Bartek 2.0)
- **NIE shift_end_edge** (kurier kończy zmianę) — ⚠ NOWA jawna bramka (patrz §3)
- **NIE parser_degraded** — ⚠ NOWA jawna bramka (patrz §3)

**Czego plaster D NIE wymaga (różnica od AUTON-01 strict):**
- NIE wymaga auto_route==AUTO (klasyfikator Fazy 7) — to był killer (7% AUTO → would_auto≈0)
- NIE wymaga margin≥15 (G12 wyłączony w profilu D)
- pool próg 2 zamiast 3 (srednio wchodzi)

Rozmiar/jakość (proxy): D ≈ 62% wol / ~125/dzień / breach 5,5% / SLA 93%. Wariant konserwatywny D' (pool≥3=tylko luzno) ≈ 50%/102/dzień/5,2%.

## 2. Architektura zmiany — PROFIL, nie zniszczenie strict
Bramka `auto_assign_gate.evaluate_auto_assign` dostaje **profil** przez flagi (AUTON-01 strict zostaje dostępny, rollback = flaga):
- `AUTO_ASSIGN_REQUIRE_CLASSIFIER_AUTO` (ETAP4, default **True**=strict; False=plaster D zdejmuje G2)
- `AUTO_ASSIGN_MIN_POOL_FEASIBLE` (już istnieje, hot-numeric; D=2, strict=3)
- `AUTO_ASSIGN_REQUIRE_MARGIN` (ETAP4, default **True**=strict G12; False=D wyłącza G12)
- twarde bramki (G1/G4/G5/G6/G7/G8/G9/G11) — BEZ flagi, ZAWSZE.
Default wszystkich = zachowanie AUTON-01 dziś (would_auto≈0). Plaster D = 3 flagi flip RAZEM (sprzężone, C3).

## 3. ⚠ KOMPLETNOŚĆ — co G2 NIÓSŁ ukrycie, a co trzeba dodać jawnie
G2 (route==AUTO) implicytnie wykluczał edge'e klasyfikatora. Zdejmując G2 tracę je — MUSZĄ wejść jako jawne bramki, inaczej „zmiana częściowa":
| Edge z klasyfikatora | Pokryte przez? | Akcja |
|---|---|---|
| czasówka | G4 | OK |
| best_effort | G9 | OK |
| solo_fallback / scarcity | G10 (pool≥2) | OK |
| **shift_end_edge** (kurier kończy zmianę) | NIC | **DODAĆ G13: ctx.auto_route_shift_end_edge → block** |
| **parser_degraded** (system zdrowie) | NIC | **DODAĆ G14: flag PARSER_DEGRADED / ctx → block** |
| HIGH_RISK 14-17 bucket | częściowo G11 | zostaje sufit G11; HIGH_RISK bump dotyczył tylko G2-margin → przy D bez margin = świadomie luźniej, monitor |

## 4. Mapa kompletności (ETAP 3)
| Warstwa | Miejsce | Dotknięte? |
|---|---|---|
| Bramka | `auto_assign_gate.evaluate_auto_assign` (G2/G10/G12 profil + G13/G14 nowe) | TAK |
| Flagi | `ETAP4_DECISION_FLAGS` + flags.json: REQUIRE_CLASSIFIER_AUTO, REQUIRE_MARGIN (+ MIN_POOL numeric) | TAK |
| Wpięcie | `dispatch_pipeline:2887` evaluate_auto_assign(...flags=C.load_flags()) — przekazuje flagi: OK, profil czytany z flags | sprawdzić że flags dochodzą |
| Serializer | would_auto_assign + auto_block_reasons już w shadow_decisions (top-level) | weryfikować grep -c |
| Executor | `auto_assign_executor.maybe_execute` — bez zmian logiki; rate-cap/cooldown/gastro_assign | TAK (param AUTO_ASSIGN_MAX_PER_HOUR) |
| Bliźniaki gate | gate wołany TYLKO z dispatch_pipeline (1 miejsce); executor TYLKO z shadow_dispatcher (1 miejsce) — brak bliźniaka | N-D (jedno źródło) |
| Monitor | NOWY `tools/auto_assign_monitor.py` — dzienny: ile would_auto, ile executed, breach auto-plastra vs baseline, stop-loss | TAK (NOWY) |
| Konsola | przycisk killswitch ON/OFF (flaga ENABLE_AUTO_ASSIGN) — osobny deploy panelu | TAK (osobny) |
| Testy | test ON≠OFF per flaga profilu; G13/G14 ON≠OFF; regresja pełna | TAK |

## 5. Rollout (honoruje „od razu D" + bezpieczeństwo nieprzetestowanej egzekucji)
„Od razu D" = bramka liczy plaster D od razu, ALE egzekucja na żywo wchodzi bezpiecznie:
1. **SHADOW najpierw (½-1 dzień):** flip 3 flag profilu D, `ENABLE_AUTO_ASSIGN` WCIĄŻ OFF. would_auto liczy ~125/dzień. Potwierdzić: would_auto≈oczekiwane, rozkład block-reasons, brak shift_end/parser w would_auto.
2. **KONTROLOWANE 1. wykonanie (OFF-PEAK, z Adrianem):** `ENABLE_AUTO_ASSIGN=ON` + `AUTO_ASSIGN_MAX_PER_HOUR=1` → JEDNO auto-przypisanie, na żywo, oglądamy: czy gastro_assign zmatchował nazwę kuriera, dobry czas, idempotentnie. To zamyka jedyne realne ryzyko E2E (matchowanie nazwy nigdy nie szło live).
3. **Otwarcie do D:** podnieść MAX_PER_HOUR (np. 8-10), monitoring dzienny + stop-loss (auto-breach > baseline 2 dni → auto-pauza flagą).
4. Killswitch (przycisk konsoli) gotowy zanim krok 2.

## 6. Rollback
- Każda flaga profilu → False (hot) = powrót do AUTON-01 strict (would_auto≈0).
- `ENABLE_AUTO_ASSIGN=False` (hot, killswitch / przycisk) = zero wykonań natychmiast.
- `git revert` commitu gate + restart dispatch-shadow.
- .bak gate przed edycją.

## 7. Otwarte do decyzji Adriana
- D (pool≥2, 62%) vs D' (pool≥3, 50%, bezpieczniejszy start)? Rekomendacja: shadow liczy OBA, decyzja po podejrzeniu liczb.
- `AUTO_ASSIGN_MAX_PER_HOUR` docelowy (przy ~125/dzień ≈ 8/h peak) — start 1 (kontrola), potem 8-10.
- Cooldown po PANEL_OVERRIDE (60 min) — zostaje.
