# C14 — min_delivered_at (lane C, RUNTIME-ORACLE) — backing

**Agent:** C14-min-delivered · lane C · READ-ONLY · 2026-06-30 ~17:45 UTC
**Focus:** zwaliduj przyrząd `min_delivered_at` — claim „VALIDATED producent 165/165, at-166 INCONCLUSIVE/mało-danych". Producent poprawnie liczy min(spóźnienie+dowóz)? Czemu changed=0/non-null=0? validated/void/untested + czy A/B słusznie odroczona.
**Numery linii re-grepowane świeżo (drift).**

## 0. TL;DR (werdykt)
- **PRODUCENT (silnik, `dispatch_pipeline.py:6019-6047`) = VALIDATED** (logic ground-correct; data **proxy-certified**). Oracle 2× deterministyczny: 802 non-null rekordów; **inwariant delta≥0 = 0/802 naruszeń**; arytmetyka producenta vs mój re-recompute z ISO = **0/802 mismatch**; changed⟺nierówność-cid = 0 naruszeń w obie strony; 128 null = wszystkie `pool_feasible_count=0` (pusta pula → słusznie brak liczenia). Logika `min(feasible, key=predicted_delivered_at[new])` = reguła Adriana „najwcześniej do klienta" (total = dostawa − committed, committed stały) — POPRAWNA.
- **WERDYKT-TOOL (`min_delivered_at_verdict.py`, at-166 odpalony 27.06 07:00) = VOID.** Raport „non-null: 0, changed: 0 (0%), mało danych ⚠" to **FAŁSZYWY NEGATYW** z dwóch sprzężonych bugów cyklu życia: (E/H) **rotation-blindness** (czyta TYLKO żywy `shadow_decisions.jsonl`, NIE `.1`; logrotate **dzienny** → dane 25-26.06 wylądowały w `.1`=357 non-null, niewidoczne) + (H/timing) odpalony 07:00 = głęboka noc po rotacji, przed peakiem lunch → żywy plik miał tylko rzadkie nocne decyzje 0-non-null.
- **„changed=0/non-null=0" NIEPRAWDA DZIŚ:** żywy ledger = **802 non-null, 266 changed (33,2%)**, `.1` = 357 non-null (25.06=139, 26.06=218). Zero było artefaktem rotacji+pory, NIE wadą producenta.
- **A/B „słusznie odroczona"? — ODROCZONA NA FAŁSZYWEJ PRZESŁANCE; wynik obronny przypadkiem.** Re-run TEGO SAMEGO toola na świeżych danych przerzuca werdykt: materialność **33% (MATERIAL, nie <20%)** + **odpala się WŁASNA klauzula regresji floty** ((R6_worse+spread_worse)=194 > 0,3×266=79,8) → rekomendacja toola staje się „ostrożnie — widoczna regresja floty, skłania ku **NEITHER (psuje flotę)**". Czyli prawdziwy werdykt to nie „inconclusive/przedłuż shadow" lecz „MATERIAL ale leans NEITHER (R-FLEET-LEVEL conflict)". Decyzja, by NIE flipować A na ślepo = słuszna; INSTRUMENT, który ją „uzasadnił" = void i wymaga re-runu rotation-aware przed jakimkolwiek werdyktem A/B.

---

## 1. METODA ORACLE (druga, niezależna)
1. **Lokalizacja ledgera:** `scripts/logs/shadow_decisions.jsonl` (54MB, mtime 17:45 FRESH) — NIE w `dispatch_state/`. Pole `min_delivered_at_shadow` serializowane top-level w `shadow_dispatcher.py:864` (`getattr(result, "min_delivered_at_shadow", None)` → KAŻDA linia ma KLUCZ; wartość null gdy producent nie policzył).
2. **Disambiguacja klucz vs wartość:** 929 linii, 929× klucz, **802 non-null (object), 127-128 null**. (A4 „841×" = liczył obecność KLUCZA o 13:33, nie wartości non-null — to dwie różne rzeczy; stąd pozorna sprzeczność z werdyktem „0".)
3. **Re-recompute (2× determinizm):** sparsowałem `live_delivered_at`/`mda_delivered_at` z ISO i policzyłem `sooner_min = (live−mda)/60` SAM (nie ufając polu producenta), porównałem do `mda_delivers_sooner_min`.
4. **Inwarianty-tripwire:** delta≥0 (mda nie dostarcza PÓŹNIEJ niż live, bo mda=argmin a live∈feasible); changed⟺cid-różne; ten sam zbiór (mda∈feasible).
5. **Rotation hypothesis:** policzyłem non-null per-dzień w `.1` (118MB, mtime 27.06 00:00) → potwierdza gdzie zniknęła „165/165".
6. **Verdict re-run simulation:** odtworzyłem klauzule rekomendacji toola (`:80-86`) na świeżych danych.
7. **Twin/blast-radius:** którzy inni czytelnicy `shadow_decisions.jsonl` są rotation-blind; cadence z `/etc/logrotate.d/dispatch-v2`.

---

## 2. PRODUCENT — kod (świeże file:line) + werdykt VALIDATED
- **Helper prawdy** `dispatch_pipeline.py:622-630` `_new_delivered_at_dt(c,new_oid)` → `plan.predicted_delivered_at[new_oid]` (datetime|None). Docstring: „committed stały → min delivered_at = min total". Czysta funkcja.
- **Blok producenta** `dispatch_pipeline.py:6012-6047`, **podwójnie bramkowany**:
  - **Outer** `:5953` `if getattr(C,"ENABLE_LATE_PICKUP_HARD_GATE",False) and feasible:` — `ENABLE_LATE_PICKUP_HARD_GATE` = **stała MODUŁOWA** `common.py:2822-2823` default `"1"`=ON (NIE klucz flags.json → stąd „<ABSENT>" w flags.json, ale efektywnie ON). `and feasible` → pusta pula = blok pominięty = null.
  - **Inner** `:6019-6020` `if C.flag("ENABLE_MIN_DELIVERED_AT_SHADOW", getattr(C,...,False)):` — flags.json = **True** (`common.py:2741-2742` default `"0"`).
  - Rdzeń `:6022-6024`: `_mda = min(feasible, key=lambda c: _d.timestamp() if (_d:=_new_delivered_at_dt(c,oid)) else inf)`; `:6028` `_sooner = round((_live_d−_mda_d)/60,1)`.
  - **ZERO mutacji** `feasible`/`_winner`; try/except defensywny (`:6046` log.warning, połyka — klasa M, ale log-only).
- **Werdykt: VALIDATED (logic ground; data proxy).** Oracle (RUN1≡RUN2):
  - `non-null n=802 | changed=266 (33,2%)`
  - **INWARIANT delta≥0: 0/802 naruszeń** (mda NIGDY nie dostarcza później niż live → min() liczy poprawnie). `my recomputed sooner: min=0.0 max=23.3`.
  - **Arytmetyka: 0/802 mismatch** producent `mda_delivers_sooner_min` vs mój ISO-recompute.
  - **changed=True & cid-równe: 0; changed=False & cid-różne: 0** (flaga `changed` spójna z `mda_cid!=live_cid`).
  - **128 null = 100% `pool_feasible_count=0`** (45 KOORD + 83 PROPOSE-pool0) → blok słusznie pominięty (`and feasible`). Brak fikcyjnych pickupów, brak ujemnych delt.
  - Próbka changed (powtarzalny wzorzec): live_cid 457→mda_cid 531, sooner 2.0 min, mda_R6 9.8<11.8 (tu Pareto-lepszy).

## 3. WERDYKT-TOOL — VOID (rotation-blind false-negative)
- **Plik** `eod_drafts/2026-06-25/min_delivered_at_verdict.py`: `:16` `SHADOW=".../shadow_decisions.jsonl"` (TYLKO żywy, brak `.1`/`.gz`), `:29` `for line in open(SHADOW)` (whole-file), `:37` `if ts < SINCE: continue` (SINCE=`2026-06-25T12:24:00`), `:80-81` klauzula „⚠ Mało danych" gdy `materiality<20 or n<20`.
- **Wynik at-166** (`min_delivered_at_verdict_result.txt`, frozen **27.06 07:00**): „non-null: 0 | changed: 0 (0%) | materialność <20% ⚠ | Mało danych". `atrun.log` = telegram sent=True.
- **Dlaczego 0 (root cause, potwierdzony):**
  - `.1` archiwum (logrotate, mtime 27.06 00:00) zakres **18.06→26.06**: non-null per-dzień 18-24.06=**0** (flaga OFF), 25.06=**139**, 26.06=**218** → **357 non-null uwięzione w `.1`**. Producent ruszył 25.06 (restart 12:24:50 z flagą, per memory `60cfa57`).
  - logrotate `/etc/logrotate.d/dispatch-v2`: `*.jsonl` = **daily / 30d / 100M cap / compress (po 1d delay)**. Czyli ledger rotuje CO DZIEŃ → tool czytający whole-file + SINCE 25.06 widzi tylko BIEŻĄCY dzień.
  - O 27.06 07:00 żywy plik (po rotacji 00:00, głęboka noc, przed lunch) miał garść nocnych decyzji = 0 non-null w oknie → „0 / mało danych".
  - **Twin check (precyzyjny):** `min_delivered_at_verdict.py` = **rotation-BLIND**; `objm_lexr6_canary_monitor.py` (siostrzany P0 czytający TEN SAM ledger) = **HANDLES .1**. Czyli wzorzec naprawialny istnieje obok, ale tu nie zastosowany. (Żaden czytelnik nie sięga do `.gz` po 1d.)
- **Stale-file (H):** `result.txt`/`atrun.log` zamrożone 27.06 07:00, **brak TTL/„stale" markera** → ktokolwiek je czyta jako „bieżący werdykt" dostaje „0 changed, odrocz", podczas gdy rzeczywistość = 33% material + regresja floty.

## 4. A/B — czy słusznie odroczona? (re-run na świeżych danych)
Symulacja rekomendacji toola (`:60-86`) na ŻYWYM ledgerze (802 non-null):
- `materiality=33% (n=802, changed=266)` → **≥20% MATERIAL** (klauzula „mało danych" `n<20 or mat<20` = **False** → tool NIE powiedziałby „mało danych").
- Klauzula regresji floty `:85` `(r6_worse+spread_worse) > 0.3*len(changed)`: **114+80=194 > 79,8 = TRUE → „skłania ku NEITHER (psuje flotę)"**.
- Klauzula „czysty zysk" `:83` (zero regresji ∧ med≥3) = **False**.
- Rozkład zysku (changed,>0): n=266, **mediana 2,8 / p90 12,3 / max 23,3 min** wcześniej do klienta.
- Regresja floty na 266 changed: **R6 gorszy 114 (42,9%) | spread gorszy 80 (30,1%) | late gorszy 30 (11,3%)**.
- **Wniosek:** min-total dowozi NOWE zlecenie ~2,8 min (mediana, do 23) wcześniej w 33% decyzji, ale KOSZTEM R6 (max bag time) w 43% i spreadu w 30% — dokładnie konflikt per-klient-vs-flota (R-FLEET-LEVEL) przewidziany w `memory/min-delivered-at-shadow-2026-06-25.md` §18. **Prawdziwy werdykt = „MATERIAL ale leans NEITHER", NIE „inconclusive/przedłuż shadow".** A/B odroczona na fałszywej przesłance („brak danych"); decyzja-by-nie-flipować-na-ślepo obronna, ale uzasadnienie instrumentu = void → przed A/B re-run rotation-aware (żywy + `.1` + `.gz`).

## 5. CAVEAT proxy vs ground-truth (krytyczne dla A/B)
`delivered_at` = `plan.predicted_delivered_at[new]` = **PREDYKCJA SILNIKA (PROXY-CERTIFIED), NIE fizyczny GPS**. Per FUNDAMENT-CAVEAT (A4 §7): klik dostawy ~192s przed GPS, 0/377 auto_geofence GT; predykcja jest jeszcze luźniejsza. **mediana zysku 2,8 min mieści się w podłodze szumu predykcji** → flip A walidowany na tym proxy ryzykuje gonienie artefaktów predykcji (analogia: food-age „62% regresji" = artefakt budżetu solvera 200ms, nie objektyw — `memory` §14). Każdy werdykt A/B MUSI joinować `gps_delivery_truth.jsonl` (jedyny ground-truth) zanim uzna „sooner" za realny.

## 6. SMELLS poboczne (zasila Fazę B)
- **I/D (ukryta zależność/coupling):** shadow `min_delivered_at` ŻYJE tylko gdy NIEPOWIĄZANA stała `ENABLE_LATE_PICKUP_HARD_GATE` (gate late-pickup tieringu) = ON. Flip tej stałej OFF cicho zabija shadow mimo `ENABLE_MIN_DELIVERED_AT_SHADOW=True`. `dispatch_pipeline.py:5953` vs `:6019`.
- **M (cicha awaria):** `:6046` `except ... log.warning` połyka błąd liczenia mda (instrument może „milczeć" null zamiast krzyczeć) — ten sam wzorzec co A4 §8 M.
- **F (field-semantics, minor):** 83 rekordy PROPOSE z `pool_feasible_count=0` + null mda — rozjazd „pool_feasible_count vs lokalny `feasible` w bloku late-pickup" (różne etapy?) — nie root-caused, tangencjalne do C14.

---

## 7. TABELA POKRYCIA
| Obszar | Zbadane? | Dowód/metoda |
|---|---|---|
| Producent blok `dispatch_pipeline.py:6012-6047` | ✅ | Read + oracle 2× |
| Helper `_new_delivered_at_dt:622-630` | ✅ | Read |
| Outer gate `:5953` + `common.py:2822` default | ✅ | grep + Read |
| Inner flag `flags.json` + `common.py:2741` | ✅ | venv json load |
| Serializer `shadow_dispatcher.py:864` | ✅ | grep |
| Ledger `shadow_decisions.jsonl` (802 non-null) | ✅ | recompute 2× determ. |
| Archiwum `.1` (357 non-null, 18-26.06) | ✅ | per-day bucket |
| Inwariant delta≥0 / arytmetyka / changed-cid | ✅ | ISO re-recompute 0 naruszeń |
| Null records (128) | ✅ | wszystkie pool_feasible=0 |
| Verdict tool + result + atrun | ✅ | Read + re-run sym. |
| Verdict re-run na świeżych | ✅ | klauzule `:60-86` odtworzone |
| Twin rotation-blindness + logrotate | ✅ | grep + cat logrotate.d |
| **Full-pool brute-force argmin z logu** | ❌ GAP | tylko best+2 alternatives serializowane, brak per-cand `predicted_delivered_at` → wymaga engine replay (poza no-write DoD). Zastąpione inwariantem delta≥0. |
| **Join z GPS ground-truth (`gps_delivery_truth.jsonl`)** | ❌ GAP | proxy „sooner" niezweryfikowane fizycznie; cross-join odroczony (heavy). |
| **Pełny re-run werdyktu nad `.1`+`.gz` łącznie** | ❌ GAP | `.1` policzone (counts/daty); starsze `.gz` nie dekompresowane → 357 = floor. |
| **pickup_slip/reassignment/pending rotation** | ❌ GAP (poza zakresem) | czytają własne `dispatch_state` jsonl (inna rotacja); sprawdzony tylko twin `shadow_decisions.jsonl` (objm canary=.1-aware). |

## 8. CO FLIPUJE TEN PRZYRZĄD
Decyzja **A** (flip „min-total" jako PRIMARY obiektyw selekcji — mapa kompletności `_late_pickup_score_first_key`+`_best_effort_*`+`objm_lexr6.lex_qual` RAZEM) vs **B** (dostroić committed_pickup+food_age do sumy 1:1) vs **neither**. Dziś instrument napędza tę decyzję FAŁSZYWYM „inconclusive"; po rotation-aware re-run napędzałby „MATERIAL leans neither (R-FLEET-LEVEL)".
