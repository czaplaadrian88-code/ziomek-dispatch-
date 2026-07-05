# A1-INVARIANTS (Sprint 1 Z2, tmux 17) — de-VOID carried_first_guard + global_allocate + sloty ①②③ — 2026-07-05

## Werdykt
**⚠️VOID = 0/4** (z Z1: serializer + CONFTEST-STRIP; z Z2: carried_first_guard + global_allocate geometria).
**Bramka `PENDING_RESWEEP_LIVE` ma podstawę pomiarową** — DoD Zadania 2 spełnione.
**5 slotów ①②③ uzbrojonych** (weryfikacja stale-dashboardu: strażnicy istnieli, dashboard ich nie widział).

## 1. carried_first_guard: VOID → 🟢 (re-oracle C9)
**Czemu był VOID (oracle 30.06, R3-D1):** proces strażnika (oneshot timer co 3 min) biegał z PUSTYM env →
flagi route/canon env-frozen liczyły default-OFF → 91,7% rekordów = fikcyjne `no_position` (10/11 „zaginionych"
cidów miało świeży wpis w courier_last_pos.json).
**Co usunęło przyczynę:** L0.2 (01.07, `131b555`): drop-in `engine-env-parity.conf` (lustro env silnika) +
test anty-dryfu `test_carried_first_guard_env_parity` (czyta PLIKI drop-inów; brak/rozjazd = FAIL, nie skip).
D3 (02.07): gros flag route/canon → flags.json (hot-reload) → oneshot-proces czyta TO SAMO źródło co silnik
= parytet z konstrukcji; drop-in został dla 2 flag nie-zmigrowanych.
**Re-oracle (świeże okno 02.07 → 05.07 ~17:35 UTC):** timer ŻYWY (ostatni tick <3 min przed pomiarem),
**4901 rekordów, `no_position` = 0** — rozkład realnych klasyfikacji: ok 4120 · plan_invalidated 462 ·
canon_divergence 235 · carried_first 83 · coverage_gap 1. Fikcja zniknęła; strażnik UJAWNIA (cel L0.2).
**Mutation-probe ×2 (wszystko przywrócone, git diff 0 / diff drop-ina 0):**
1. Usunięcie 1 linii `Environment=` z drop-ina strażnika → `test_guard_env_mirrors_engine` **FAILED** ✅
2. `_carried_first_smell` → zawsze False → `test_carried_first_detected` + `test_carried_first_smell_pure` **FAILED** ✅

## 2. global_allocate geometria: VOID → 🟢
**Czemu był VOID (oracle 30.06):** przyrząd de-pile certyfikował LICZBĘ (alokacje/score), ślepy na geometrię —
35,2% worków po de-pile spread>8km (łamie R1), werdykt tego nie widział → liczba przyrządu pchałaby zły flip.
**Co usunęło przyczynę (Faza C + L6.C 04.07):** rekord alokacji niesie `spread/km/r6/cos` z metryk silnika;
werdykt `would_repropose` uwzględnia `spread_improved`; wiersze jsonl niosą `new_deliv_spread_km` +
`g_spread_improved`; **bramka `live_gate_open()`** (flip `PENDING_RESWEEP_LIVE` bez
`ENABLE_LEXQUAL_GEOMETRY_TIEBREAK` = HOLD + głośny warning) — zakodowana, WPIĘTA w jedyną ścieżkę LIVE
(`run_once`), testowana ON≠OFF (`test_l6c_geometry_claim` l.202-212). Geometria w selekcji: replay L6.C
3d/463 = PARETO quant=0 (flip za ACK — Sprint 2).
**Nowy strażnik `test_global_allocate_geometry_guard` (5 testów):** certyfikator NIE-ślepy (rekordy alokacji
muszą nieść geometrię) · geometria w jsonl · **czysta poprawa geometrii odpala werdykt** (delta<margin,
jedyny powód = rozjazd_kierunkow) · osiągalność bramki (C5/#18: FLAG_LIVE=ON ⇒ `live_gate_open()`
skonsultowany, zamknięta bramka ⇒ live_acted=0) · default-zamknięta bramka.
**Mutation-probe ×3 (wszystko przywrócone):**
1. Ucięcie `spread` z rekordu alokacji → 2 testy **FAILED** ✅
2. Werdykt ślepy na geometrię (`would = changed and better_now`) → `test_geometry_only_improvement_fires_verdict` **FAILED** ✅
   ⚠ near-miss procesu: pierwotnie liczyłem, że starą lukę łapie `test_run_once_spread_fix` — probe pokazał,
   że NIE (tamten fixture ma better_now=True równolegle) → test izolujący geometrię DOPISANY. Probe > lektura.
3. Ominięcie bramki (`if live_gate_open():` → `if True:`) → `test_live_path_consults_geometry_gate` **FAILED** ✅

## 3. Sloty ①②③ uzbrojone (5) — weryfikacja „strażnik istnieje, dashboard stale"
| Slot | Strażnik (zweryfikowany biegiem, zielony) | Uwagi |
|---|---|---|
| ① INV-SRC-AVAILABLE-FROM | `test_l4_available_from` (25) | źródło L4 LIVE od 04.07 (at-203) |
| ① INV-SRC-LEXQUAL | `test_objm_lexr6_unify_2026_06_25` | 3 kopie→kanon; pick==kanon OFF+ON; anty-redywergencja pick+cień; parytet cienia oba stany POST_SHIFT (L6.C C1) |
| ② INV-FEAS-PICKUP-FLOOR | `test_pickup_floor_guard` + żywy `tools/pickup_floor_guard` | NIE-ŚLEPY po L4; zakres silnik+monitor (rendery = pas route-order/L3) |
| ③ INV-TWIN-LEXQUAL | jw. (wspólny moduł z konstrukcji) | |
| ③ INV-TWIN-SLA-ANCHOR | `test_sla_anchor_unified` | `sla_anchor.py` 3 bliźniaki RAZEM; flaga OFF (fuzz 400/0 bajt-parytet); flip = prerekwizyt O2 za ACK |

NIE uzbrojone świadomie: INV-SRC-ROUTE-ORDER (Sprint 0 tmux 15 w toku, deadline 07-10.07 — nie dotykam
`tests/golden/`), INV-SRC-EQUAL-TREATMENT (inwariant NIE jest spełniony żywo — bliźniaki #4/#7 wciąż
nierówne, patrz protokół #0 „dyskryminacja pozycji"; uzbrojenie = dopiero po naprawie klasy).

## 4. Zakres zmian (protokół: zero zmiany zachowania silnika)
Tylko: `tests/test_global_allocate_geometry_guard.py` (NOWY) + `ZIOMEK_INVARIANTS.md` (dashboard) + ten dowód.
Silnik/tools nietknięte (mutation-probes = transient sed + git checkout, working tree czysty po każdej).
