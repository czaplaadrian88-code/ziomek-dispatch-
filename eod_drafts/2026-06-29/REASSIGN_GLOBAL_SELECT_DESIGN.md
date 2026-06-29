# DESIGN — globalne rozbijanie pile-on PRZERZUTÓW (reuse global_allocate) — 2026-06-29

**Problem (Adrian 29.06):** konsola koordynatora dostaje ~10 propozycji PRZERZUTU
wszystkie na jednego kuriera (Jakub W). Chce 1-2 tworzące mu dobry worek; reszta ma
się rozejść albo odpaść. „Jak przypisze mu wszystkie → cała praca do niczego."

**Root cause:** `reassignment_forward_shadow.evaluate_order` ocenia KAŻDE zlecenie
NIEZALEŻNIE, na stałym zdjęciu floty → jeśli Jakub dobrze ustawiony, dla 10 zleceń
każda osobna ocena mówi „best=Jakub". Brak JOINT-feasibility/capacity. `feed.py`
ma tylko flagę `pile_on` (wizualne ostrzeżenie, komentarz w kodzie: „per-order, nie globalne").

**Fix u źródła (klasy „brak joint-alokacji"):** zastosować sekwencyjny alokator
`pending_global_resweep.global_allocate` (sprawdzony 5 dni shadow + live na NOWYCH
zleceniach) na ZBIORZE przerzutów: każde zlecenie zdjęte z holdera → re-alokowane
sekwencyjnie z aktualizacją worka → po 1-2 zleceniach worek Jakuba rośnie, jego
score dla kolejnych spada → reszta idzie do innych albo zostaje u holdera.

## Architektura (OSOBNY MODUŁ — izolacja od sesji 1)
Sesja 1 jest właścicielem `reassignment_forward_shadow.py` (Sprint NO-GPS-EQUAL,
może edytować dalej Sprint3). Mój fix = OSOBNA WARSTWA SELEKCJI (inny koncept niż
per-order generacja). Wzorzec 1:1 jak `global_alloc.json` (resweep→plik→feed overlay),
który już działa LIVE dla nowych zleceń.

### Silnik: `tools/reassignment_global_select.py` (NOWY) + timer
1. Flaga `ENABLE_REASSIGN_GLOBAL_SELECT` (default OFF → no-op, bajt-identyczne).
2. Czyta ŚWIEŻE kandydaty z `reassignment_shadow.jsonl` (tail, dedup per order najnowszy,
   `quality_reassign=True`, TTL świeżości, wciąż nieodebrane) → zbiór S = [(oid, holder_cid, rec)].
   (= reużycie quality-gate sesji 1 jako definicji „co jest legalnym kandydatem" — ZERO 2. kopii reguł.)
3. Jeśli |S| < 2 → brak pile-on do rozbicia → przepisz S 1:1 (passthrough), koniec.
4. Buduje `dispatchable_fleet()`, zdejmuje WSZYSTKIE S z workow holderów (`_fleet_without_orders`).
5. `global_allocate(S, fleet_minus_S, now, _results_out=res)` → {oid: alokacja} + pełne PipelineResult per oid.
6. Dla każdego O w S: G = globalnie wyliczony kurier.
   - G == holder(O) lub G is None → DROP (zostaje u holdera / KOORD — NIE przerzucamy).
   - G != holder(O) → re-quality-gate vs G (`_quality_gate` z PipelineResult: a_cand=holder w cands, best=G;
     reserve-aware bundling-only + rescue-require-holder-absent DZIEDZICZONE) → quality? SURVIVOR best_cid=G.
7. Zapis `reassign_global_alloc.json` (OVERWRITE per tick, written_at TTL — wzorzec global_alloc_store):
   {written_at, selected: {oid: {best_cid, best_name, arm, reason, save_min, holder_cid, ...}}}.
   + log `reassign_global_select.jsonl` (shadow verdict): candidates_in, survivors_out,
   maxpile_before, maxpile_after, spread_improved, per-oid kept/dropped+why.

### Konsola: `feed.py` overlay (additive branch w `_load_reassign_proposals` / po nim)
- Flaga panelu `REASSIGN_GLOBAL_SELECT_OVERLAY` (default OFF).
- ON + świeży `reassign_global_alloc.json` → FILTRUJ `reassigns` do oid w `selected`
  (i użyj selected best_cid/arm/reason). OFF/stary/brak → zachowanie jak dziś (fallback).
- Współistnieje z `GLOBAL_ALLOC_OVERLAY` (nowe zlecenia) — inny plik, inny typ propozycji.

## MAPA KOMPLETNOŚCI (ETAP 3)
| Miejsce | Klasa | Dotknięte? |
|---|---|---|
| `reassignment_global_select.py` (NOWY) | selekcja/global | TAK — rdzeń |
| `global_allocate` (pending_global_resweep) | reuse | import, NIE modyfikuję |
| `_active_assigned_orders`/`_fleet_without_order`/`_quality_gate` (reassignment_forward_shadow) | reuse reguł | import, NIE modyfikuję (własność sesji 1) |
| `reassign_global_alloc.json` | state/channel | TAK — NOWY plik (overwrite) |
| `reassign_global_select.jsonl` | metryka/werdykt | TAK — NOWY log (shadow) |
| `feed.py` `_load_reassign_proposals` | kanon/display | TAK — additive filtr za flagą |
| `reassignment_quality_replay.py` | bliźniak (czyta reassignment_shadow.jsonl) | N-D — czyta surowy jsonl generatora, mojego kanału nie; precyzja ratunku niezmieniona |
| timer `dispatch-reassign-global-select` | config/systemd | TAK — NOWY unit + timer |
| flaga ENABLE_REASSIGN_GLOBAL_SELECT | flaga (silnik) | TAK — flags.json + ETAP4_DECISION_FLAGS? (czyta C.flag w SHADOW, nie w decyzji dispatchu → registry-only) |
| flaga REASSIGN_GLOBAL_SELECT_OVERLAY | flaga (panel) | TAK — app/core/flags.py |
| GLOBAL_ALLOC_OVERLAY (nowe zlecenia) | nie-kolizja | N-D — inny kanał, koegzystują |

## ETAP 2 HARD vs SOFT
- Display-only selekcja: NIE dotyka feasibility/scoring/kanonu dispatchu. Filtruje CO konsola POKAZUJE.
- assess_order (HARD-first) dziedziczony przez global_allocate → selekcja nie może być „gorsza" niż silnik.
- Nie dotyka P-1..P-7. Reserve-aware + rescue-holder-absent uszanowane (re-quality vs G).
- Ręcznie zatwierdzane (przycisk „Przyjmij przerzut") — zero autonomii.

## ETAP 5 dowód (przed flipem overlay)
- Replay korpusu reassignment_shadow.jsonl: ile ticków miało pile-on (maxpile≥2 na 1 kuriera),
  ile po global-select spada do 1-2, ile survivorów vs kandydatów. Materialność: realne rozbicie
  na świeżym oknie. + 0 regresji: survivor zawsze ⊆ kandydatów quality (nie wymyśla nowych przerzutów).
- Inwariant tripwire: survivors_out ≤ candidates_in; maxpile_after ≤ maxpile_before; każdy survivor ma quality_reassign.

## Rollback
- Silnik: flaga OFF (hot) / `.bak` / git revert nowego pliku+timer.
- Panel: flaga OFF + restart / `.bak` feed.py / git revert.
