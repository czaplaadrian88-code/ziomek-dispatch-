# WERDYKT B3-A — wpięcie kary niepewności no_gps w tor ALWAYS-PROPOSE

**Data:** 2026-06-22 · read-only replay, ZERO prod-touch · harness: `eod_drafts/2026-06-22/no_gps_alwayspropose_forward_replay.py`

## Kontekst
B3 rescue (20.06) wpięty w gałąź `all_candidates_low_score → KOORD`, którą ALWAYS-PROPOSE (15.06) wyłączyła → marker `no_gps_uncertainty_propose`=0 → trial n=0 (martwy kod, diagnoza 22.06). Opcja A = przenieść karę +12 min + cap R6 do toru, którym no_gps+empty realnie dziś przechodzi.

## Co mierzy replay
Forwardowa rzeczywistość pod ALWAYS-PROPOSE: ile decyzji **PROPONUJE no_gps+empty jako best** (slice, który dostałby karę), R6 po karze, korelacja z faktyczną punktualnością.

## Wyniki (żywy `shadow_decisions.jsonl`+`.1`, 3108 linii)
| Metryka | Wartość |
|---|---|
| Decyzje PROPOSE/AUTO ogółem | 2483 |
| **SLICE A — PROPOSE z best=no_gps+empty** | **201 (8,1% decyzji)** |
| z tego best_effort (0 feasible) | 1 (reszta = feasible, realnie wygrywają) |
| committed-late breach w slice | 0 |
| stary slice `all_candidates_low_score` (forward) | 318 (w `.jsonl.1`, w aktywnym logu 0 → czemu trial=0) |
| gate-score slice | median 96,1 (min −178, max 131) |
| **R6 po karze +12** | median 25,6 · p80 31,4 · max 46,2 |
| R6_after <35 (kara nieszkodliwa) | 184 |
| R6_after 35–38 (soft, banner) | 8 |
| **R6_after >38 (>cap, stary B3=KOORD)** | **9** |
| **Outcome slice (on-time)** | **52,2% (82 on-time / 75 late / 44 unknown)** |
| **Outcome gdy R6_after >35** | **on-time 1 / late 13 → late-rate 92,9%** |
| Per kurier (top) | 123 (46), 518 (43), 376 (35), 179 (14), 500 (9)… |

## Interpretacja — A jest WARTE (z liczbami)
1. **Slice realny, nie no-op:** 8,1% wszystkich propozycji to fikcyjnie pozycjonowany (centrum BIałystok) no_gps z pustym workiem, proponowany z optymistyczną ETA bez żadnej flagi.
2. **Fikcja szkodzi:** on-time slice'u **52%** vs baseline floty ~67% (prep_bias `ontime_before` 22.06). Te propozycje psują punktualność ponadprzeciętnie.
3. **Kara +12 to REALNY sygnał, nie szum:** gdy kara wpycha R6 ponad 35 min, zlecenie jest faktycznie spóźnione **w 93%** (13/14). Czyli „+12 min korekty fikcji" celnie wykrywa skazane przypadki — dokładnie ta kalibracja, której brakuje przy surowej ETA.
4. **Ekstremów mało i są obsługiwalne:** tylko 9/201 przekracza cap 38 → wąski zbiór, w którym warto głośno ostrzec operatora (albo dać zawór KOORD).
5. **Per-kurier zgodny z lejkiem KOORD 20.06:** dominują chroniczni bez-GPS (518 Rogucki, 123 Bartek, 376) — to samo źródło co 57% lejka KOORD. A adresuje ich propozycje uczciwą ETA zamiast ciszy.

## Projekt zmiany (do akceptacji)
- **Gdzie:** centralny post-hook w `assess_order` (wrapper 2769–2815, jedyny choke point) — po `_assess_order_impl`. Behaviour-preserving gdy flaga OFF.
- **Co:** jeśli `verdict ∈ {PROPOSE,AUTO}` ∧ `best` = blind+empty no_gps ∧ flaga ON → doliczy +UNC (12) do `travel_min/drive_min/r6_max_bag_time_min`, ustaw `no_gps_uncertainty_applied_min` (jak stary `_build_no_gps_uncertainty_result`, post-selekcja, BEZ re-rankingu).
- **Banner:** gdy `r6_after > 35` → marker render ⚠️ „no_gps fikcja, realny R6 ~X".
- **Nowa flaga:** `ENABLE_NO_GPS_UNCERTAINTY_IN_ALWAYS_PROPOSE` (default OFF; stara `ENABLE_NO_GPS_UNCERTAINTY_PENALTY` zostaje dla legacy rescue/testów).

## JEDYNA decyzja produktowa — semantyka R6_after > cap (38), 9 przypadków/okno
- **Opcja A1 (rekom., spójna z ALWAYS-PROPOSE):** NIE milcz — proponuj, ale z **mocnym** bannerem `no_gps_uncertainty_over_cap` („~93% historycznie spóźnione, rozważ reassign"). Operator decyduje.
- **Opcja A2 (zawór bezpieczeństwa):** sub-flaga `NO_GPS_OVER_CAP_KOORD` (default OFF) — tylko te ~9/okno re-eskaluje do KOORD mimo ALWAYS-PROPOSE. Dla Adriana, jeśli woli twardo.

## Plan po ACK (nic nie flipnięte)
1. Implementacja gated (flaga OFF) → `cp .bak` → edit → `py_compile` → import → testy (nowe + pełna suita baseline) → commit + tag.
2. **Shadow/replay weryfikacja przy fladze OFF** (porównanie metryk) → dopiero potem ACK na flip.
3. Flip = osobny krok z monitorem (parytet z B3 trial, ale na właściwym torze).

**Rollback:** flaga OFF (hot-reload) — zero zmian zachowania.
