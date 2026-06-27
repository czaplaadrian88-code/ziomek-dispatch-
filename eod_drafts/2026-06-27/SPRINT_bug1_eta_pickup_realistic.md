# SPRINT — Bug #1 naprawa: `eta_pickup_realistic` (additive, display-aware)

**Autor:** CC (sesja 2026-06-27) | **Zlecił:** Adrian („przygotuj sprint naprawy, to pewne, po naprawie 100% progres")
**Protokół:** `memory/ziomek-change-protocol.md` ETAP 0→7. **Case spustowy:** Adrian Citko (cid 457), worek `[483714 Parkowa carried, 483721 Eat Point pickup]`, 27.06.
**Powiązane:** [[bug1-eta-display-bug4-reseq-2026-06-26]] (diagnoza, pomiar 35%), pułapki #8/#9 protokołu.

---

## CEL (jednozdaniowo)
Propozycja/„duch" pokazuje czas ODBIORU policzony ślepo na to, co kurier już wiezie (drive-from-now + floor `ready_time`) → mediana 24 min za wcześnie dla zajętych. Dodajemy **NOWE pole additive `eta_pickup_realistic_utc/_hhmm`** (insertion-aware: free_at + dojazd z ostatniego dropu) **tylko do wyświetlania** — `eta_pickup_utc` (zmienna decyzyjna) NIETKNIĘTE.

---

## ETAP 0 — STAN NA ŻYWO (zweryfikowane 2026-06-27)
- **git:** HEAD `e72e0f1` (bug4_reseq_verdict). ⚠️ **DRZEWO BRUDNE — inna sesja:** `M shadow_dispatcher.py`, `M tools/pending_global_resweep.py`, `M tests/test_pending_global_resweep.py` (robota pending-global-resweep z MEMORY). **KOLIZJA:** mój fix dotyka serializera w `shadow_dispatcher.py` → przed startem `git log -3` + `git stash list` + uzgodnić z tamtą sesją; commitować JAWNE pliki (wzorzec #5/Lekcja #47), nie `git add -A`.
- **baseline testów:** `3420 collected` (2026-06-27). Pełny `pytest tests/` ZIELONY vs baseline PRZED kodowaniem (ETAP 0 wymóg) — uruchomić na starcie wykonania.
- **shadow joby:** Bug #4 reseq shadow LIVE (`ENABLE_BUG4_RESEQ_SHADOW`, at-188 werdykt 28.06 18:00) — **NIE dubluje** tego sprintu (#4=sekwencja worka, #1=czas odbioru propozycji). `atq` reconcile na starcie.
- **flaga:** nowa `ENABLE_ETA_PICKUP_REALISTIC` — default OFF, czytana przez `flag()` (NIE `os.environ` modułu — anty-wzorzec #9). Serwis liczący: `dispatch-shadow` (główny pipeline) — czytać `FLAG_FINGERPRINT` z jego logu, nie z flags.json.
- **Materialność JUŻ udowodniona** (Adrian: „nie ma co mierzyć"): `tools/measure_bug1_eta_vs_freeat.py` = 34,7% propozycji zajętych, mediana 24 min < free_at, 100% przez `ready_time`. ETAP 5 „pozytywny wpływ" = **dowód poprawności** (nowe pole == rzeczywistość vs stara liczba), nie nowy pomiar ryzyka.

## ETAP 1 — ŹRÓDŁO NIE OBJAW
**Przyczyna u źródła:** `dispatch_pipeline.py:3845-3873` — `eta_pickup_utc` ma 4 gałęzie:
| eta_source | wzór | insertion-aware? |
|---|---|---|
| `plan` (3846) | `plan.pickup_at[oid] - DWELL` | ✅ tak |
| `soon_free` (3868) | `free_at_min + drive_min` | ✅ tak |
| `r07_chain_eta` (3862) | `r07_chain_result` | ✅ tak |
| **`haversine` (3855)** | **`now + OSRM(courier_pos→pickup)`** | ❌ **ŚLEPE** |

Bug = świeża propozycja dla zajętego kuriera (brak planu dla NOWEGO ordera, soon_free nie zaaplikowany) → gałąź `haversine` → `now + dojazd-z-pozycji`, floor `ready_time`. Inputs poprawnej liczby liczone OBOK: `soon_free_probe` (`free_at_min`, `free_at_iso`, `last_drop_coords`) z `_compute_free_at` (`dispatch_pipeline.py:2288-2319`).

**„To tylko display" — UDOWODNIONE grepem że NIE (pułapka #8):** `eta_pickup_utc` karmi:
- kara scoringu V3.24-A `extension_penalty` (`dispatch_pipeline.py:4960`, `extension = eta_pickup_utc - pickup_ready_at`),
- **FEASIBILITY HARD-REJECT >60 min** (`extension_penalty()→None`, reject `dispatch_pipeline.py:5398`),
- kara łańcucha `eta_pickup_min` (`common.py:3484-3513`),
- soft-cap PACZKA overrun (`dispatch_pipeline.py:2985`),
- **na akceptacji ducha → `time_arg` → committed `czas_kuriera`** (`Ops13Console.tsx:661`).
→ **fix = NOWE pole obok, NIE podmiana `eta_pickup_utc`.**

## ETAP 2 — HARD vs SOFT + ŚWIADOME INWERSJE → **⛔ DECYZJA ADRIANA**
SOFT/display nie dotyka HARD (feasibility/R6 zostają na `eta_pickup_utc`). **ALE** jeden punkt dotyka świadomej inwersji „zamrożony umówiony czas odbioru" (`ENABLE_FROZEN_PICKUP_ETA`/`PIN_AGREED_PICKUP_TIME`, 19.06) i committed `czas_kuriera`:

**Pytanie: co robi AKCEPTACJA ducha (`Ops13Console.tsx:661`, `time_arg`)?**
- **Wariant A — czysto display (rekomendowany, zero ryzyka decyzji):** kafel/feed pokazuje `eta_pickup_realistic`, ale `accept` nadal wysyła **stary** `eta_pickup_hhmm` jako `time_arg` → committed `czas_kuriera` BEZ ZMIAN. Scoring/feasibility/promesa nietknięte. Minus: kafel pokazuje realny czas, a zatwierdzenie commituje wcześniejszy (rozjazd widok↔commit).
- **Wariant B — display + accept commituje realny:** `accept` wysyła `eta_pickup_realistic` → committed `czas_kuriera` = realny czas. Spójne dla kuriera. **Minus: rusza committed → dotyka R6/feasibility/promesy + koliduje z doktryną frozen-pickup → NIE jest display-only, wymaga pełnej ścieżki decyzyjnej (ETAP 4 e2e przez feasibility+kanon+apkę).**

→ **STOP do ACK Adriana.** Rekomendacja: **A** w pierwszym rzucie (czysty, pewny „100% progres" na tym co widać), B jako osobny follow-up jeśli rozjazd widok↔commit przeszkadza.

## ETAP 3 — MAPA KOMPLETNOŚCI (klasa: kanon/display + nowe pole + metryka)
| Miejsce | Plik:linia | Dotknięte? |
|---|---|---|
| **Compute** nowego pola | `dispatch_pipeline.py` ~po 3873 (po pętli eta_source) | ✅ TAK — `eta_pickup_realistic_utc = eta_pickup_utc` gdy source∈{plan,soon_free,r07}; gdy `haversine` → `max(pickup_ready_at, free_at_utc + OSRM(last_drop_coords→pickup_coords))`; fail-soft → = eta_pickup_utc |
| Serialize metrics (źródło) | `dispatch_pipeline.py:5077` (obok `eta_pickup_utc`) | ✅ TAK — dodać `eta_pickup_realistic_utc` |
| Serializer LOCATION A | `shadow_dispatcher.py:274` (`_serialize_candidate`) | ✅ TAK — `eta_pickup_realistic_hhmm` |
| Serializer LOCATION B | `shadow_dispatcher.py:599` (`_serialize_result.best`) | ✅ TAK — bliźniak A |
| `eta_pickup_utc` (decyzja) | 4960/5398/2985 + `common.py:3513` | ⛔ N-D — celowo NIETKNIĘTE (zmienna decyzyjna) |
| Panel typ API | `coordinator/api.ts:365` (`GhostProposal`) | ✅ TAK — pole `eta_pickup_realistic_hhmm?` |
| **GhostTile display** | `Ops13Console.tsx:672` | ✅ TAK — render realistic (fallback `eta_pickup_hhmm`) |
| GhostTile `propMin` (pickupLate) | `Ops13Console.tsx:656` | ✅ TAK — late liczone od realistic |
| GhostTile accept `time_arg` | `Ops13Console.tsx:661` | ⚠️ **wg DECYZJI ETAP 2** (A=zostaje stary / B=realistic) |
| Ziomek feed | `Ops12ZiomekFeed.tsx:52-55` | ✅ TAK — bliźniak displayu |
| Telegram „Kandydaci"/header | `telegram_approver.py:347/871/1318` | ✅ TAK — bliźniak (Telegram dziś wyciszony, ale parytet powierzchni) |
| Shadow monitor | `ShadowMonitorPage.tsx:274` (`best_eta_pickup_hhmm`) | 🔵 OPCJA — dodać kolumnę realistic dla porównania (pomocne w ETAP 5) |
| `Ops13Console.tsx:1137` `Q.eta_pickup_utc` | quick-assign panel | ❓ sprawdzić czy to ta sama propozycja — jeśli tak, parytet |

**Bliźniaki RAZEM:** serializer A+B; GhostTile + Ops12Feed + Telegram (3 powierzchnie tej samej liczby).

## ETAP 4 — BRAK MARTWEGO/PÓŁ-WPIĘCIA
- Flaga `ENABLE_ETA_PICKUP_REALISTIC` w `ETAP4_DECISION_FLAGS` + stała OFF + `decision_flag()` + test **ON≠OFF** (przy OFF pole == `eta_pickup_utc`; przy ON dla busy carried pole > eta_pickup_utc).
- Metryka `eta_pickup_realistic_utc` w `shadow_decisions.jsonl` — assert obecności (test).
- Parytet serializer A↔B (test).
- Checkery: `flag_hygiene_check.py`, `flag_doc_coverage_check.py`, `flag_registry.py` zielone + `ZIOMEK_LOGIC_REFERENCE.md` wpis.
- **PEŁNA regresja** `pytest tests/` vs baseline 3420 + nowe testy (`test_eta_pickup_realistic.py`).
- **e2e:** `assess_order` na replay case Citko (457) + Bartek O (123, 26.06) — pole insertion-aware, busy>blind, idle==blind.

## ETAP 5 — POZYTYWNY WPŁYW (= dowód poprawności, nie nowy pomiar)
- OFF default, metryka obserwowana ON.
- Replay korpus 3 dni: dla każdej propozycji gdzie `eta_source=haversine` + busy → porównać `eta_pickup_realistic` vs **realny `free_at` / faktyczny pickup z historii** → realistic bliżej rzeczywistości niż blind (oczekiwane: blind mediana −24 min, realistic ≈0). To jest „pozytyw" displayu: **liczba przestaje kłamać** na 35% propozycji.
- +2 dni przypomnienie (at-job) na werdykt korelacji realistic↔rzeczywisty pickup.

## ETAP 6 — DEPLOY
`.bak` → `py_compile`+import → testy kanoniczne → `git log -3` (kolizja sesji shadow_dispatcher!) → commit JAWNE pliki + `Co-Authored-By` → restart `dispatch-shadow` (off-peak, ACK; NIGDY telegram/peak bez OK) → build panelu `vite build --base=/admin/` → `gps.nadajesz.pl/admin` ([[nadajesz-panel-dual-deploy]]) → logi `FLAG_FINGERPRINT` → `ZIOMEK_LOGIC_REFERENCE.md`.

## ETAP 7 — ROLLBACK
Flaga `ENABLE_ETA_PICKUP_REALISTIC=false` (hot) / `.bak` / `git revert`. Panel: fallback `?? eta_pickup_hhmm` = przy OFF/braku pola działa jak dziś.

---

## DoD
Każde miejsce mapy dotknięte lub N-D+powód · flaga ON≠OFF · metryka w jsonl · parytet A↔B · checkery+ref · pełna regresja zielona vs 3420 · dowód poprawności (realistic↔rzeczywisty) · +2dni · rollback. **Częściowe = niezakończone.**

## ⛔ BLOKER STARTU
1. **ACK Adriana na ETAP 2** (Wariant A vs B — accept commituje stary czy realny czas).
2. Uzgodnienie z sesją trzymającą `shadow_dispatcher.py` brudny (kolizja serializera).
