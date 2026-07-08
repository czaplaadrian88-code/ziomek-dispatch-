# world_record v1 — sesja A, zadanie PO K13 (dyspozycja koordynatora)

**Gałąź:** `refaktor/worldrecord-v1` (commit `347615c`, od master `43ac947`). **NIE zmergowana** — dotyka żywej ścieżki decyzji (2 hooki) + ścieżki nagrywania; aktywacja = restart dispatch-shadow za ACK Adriana. Czeka na decyzję o merge (koordynacja multi-sesja).

## Problem (klasa różnic replayu, diagnoza sesji A 19:33)
world_record v0 nagrywał flagi+flota+order+OSRM+`now`, ale NIE żywych wejść czytanych z dysku w scoringu. Replay godziny później czytał je „teraz" → dryf. Dowód, że to dryf a NIE kod: przy zamrożonym oknie i niezmienionym kodzie liczba różnic ROŚNIE z czasem od decyzji (16:37→1 kryt · 18:20→7 · 19:33→10), a master(K09-K13) ≡ `bab1797`(sprzed sprintu) co do znaku. Źródła (potwierdzone grepem punktów odczytu w decyzji): K07 prefetch czas_kuriera (HTTP), loadgov (orders_state.json + in-proc EWMA `_LOADGOV_STATE`), reliability (`courier_reliability.json`, 2 czytelników), saved plans (`courier_plans.json`, `ENABLE_SAVED_PLANS_READ` default ON), calib eta/bias.

## Rozwiązanie (SCHEMA wr0→wr1, ADDITIVE)
Nagranie dostaje `live_inputs`:
- **k07** / **loadgov** — obliczone w silniku, nieodtwarzalne offline → cienki hook `world_record.note_decision_input(key, value)` w 2 miejscach dispatch_pipeline (loadgov @~3741, K07 @~3928). Hook za `if not _CAP_ACTIVE: return` (no-op poza oknem capture = OFF/brak around_assess), fail-soft, **first-note-wins** (rekurencyjny kontrfaktyk early-bird NIE nadpisuje decyzji zewnętrznej — loadgov liczony przed bramką early-bird). **ZERO wpływu na decyzję** (note po przypisaniu wartości, tylko zapis do dict).
- **reliability/plans/eta/bias** — treść plików **przycięta do floty**, snapshot NA WEJŚCIU `around_assess` (czyste odczyty dysku, ZERO patchowania żywego procesu). Ścieżki z KANONICZNYCH stałych modułów (lazy import, unik cyklu).

`world_replay` serwuje wr1: reliability/plans/eta/bias przez przekierowanie kanonicznych stałych ścieżek na tmp z nagraniem + reset mtime-cache; loadgov przez patch `_loadgov_compute`; k07 zastępuje stub `{}` z K17. Rekord v0 (bez `live_inputs`) = stary best-effort (wsteczna zgodność — istniejący korpus dożywa retencji 14d).

## Dowody
- **BIT-W-BIT e2e** (`scratchpad/wr_v1_proof.py`, real OSRM :5001): nagraj wr1 (order 485927 jako scaffold, K07 sentinel) → **ZMUTUJ wszystkie żywe pliki na śmieci + reset EWMA + orders_state** → replay = wynik ŻYWY co do znaku (`PROPOSE best=509 score=205.431 pool=7`), **0 missów OSRM**. k07 sentinel + loadgov + 4 pliki nagrane wiernie.
- **RYGOR serwowania** (`test_world_record_v1.test_serve_loaders_read_recorded`): po `_serve_live_inputs` loadery silnika (`_load_courier_reliability`/`_read_raw_shared`/calib) zwracają NAGRANIE mimo że żywy dysk = garbage — dowód, że serwowanie działa niezależnie od (nie)wrażliwości konkretnej decyzji.
- 4 nowe testy `test_world_record_v1` (note first-wins, snapshot-pruning, serve-rygor, v0-noop) + k04 schema wr0→wr1. **Suita 4361/0**, ratchet ZIELONY, py_compile OK.

## Aktywacja i następne
- **Aktywacja = restart dispatch-shadow za ACK Adriana** (ENABLE_WORLD_RECORD już ON; brak nowej flagi). Po restarcie nowe rekordy = wr1 → replayują bit-w-bit; stare wr0 dożywają retencji.
- **Odblokowuje**: bramkę K06/K17 i night-guard EGZEKWUJĄCY (werdykt na wr1 orzeka o KODZIE, nie o dryfie plików — koniec potrzeby biegu kontrolnego). Rekomendacja dla K17c: night-guard włączyć w tryb egzekwujący dopiero na korpusie wr1 (≥1 dzień po aktywacji).
- **Koszt na żywo**: +4 małe odczyty JSON/decyzję (fail-soft, tylko przy ON) + ~1-3 KB/rekord (przycięte do floty). Proporcjonalne, retencja GC 14d.
- **Rezyduum**: czasówka_scheduler (osobny proces) nadal N-D w world_record (v0 i v1) — dołączy przy wspólnym WorldState (K15/pakiet dalej). Legacy per-candidate K07 (gdy `ENABLE_PRE_RECHECK_BEFORE_POOL` OFF) serwowany unią prefetchu — poprawny dla żywej konfiguracji (flaga ON); przy OFF unia jest nadzbiorem (apply filtruje po oid worka) — odnotowane.

## ⚠ Znane (nie-moje, do właściciela world_replay = sesja B/K17)
`OsrmReplayer.table` zwraca macierz floatów `9999.0` dla pustego fallbacku, a `route_simulator_v2:407` robi `cell.get(...)` → `AttributeError` przy MISSIE OSRM (crash zamiast degradacji). Nie dotyka wr1 z 0-missów, ale to fragilność replayera na missach. Zgłoszone.
