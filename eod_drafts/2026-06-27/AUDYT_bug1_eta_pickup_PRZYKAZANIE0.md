# AUDYT Bug #1 (eta-display / target_pickup) wg PRZYKAZANIA #0 — 2026-06-27

**Zlecił:** Adrian — „zrób dokładny audyt wg przykazania #0". Decyzja kierunku (jego 4 odpowiedzi): **B** — JEDNA prawdziwa liczba odbioru spójna w **konsoli + apce + restauracji**, a propozycja Ziomka **nie łamie R6 (≤35 dowóz) ani R27 (±5 odbiór)**.
**Case spustowy:** Adrian Citko cid 457, worek `[483714 Parkowa carried, 483721 Eat Point pickup]`. Plan silnika był POPRAWNY (Parkowa→Eat Point), kłamał WYŚWIETLany/commitowany czas odbioru przed odbiorem.

---

## ETAP 0 — STAN NA ŻYWO (zweryfikowane)
- **git HEAD `4812f28`** („Faza C globalna alokacja (display) + Opcja B zasilanie pending_proposals z silnika") — inna sesja **zacommitowała** swoją robotę w trakcie audytu (poprzednio drzewo brudne na `shadow_dispatcher.py`/`pending_global_resweep`). **KONSEKWENCJA:** mój fix też dotyka `shadow_dispatcher.py` (l.526) → przed edycją `git log -3` + diff `4812f28`, commit JAWNE pliki.
- **baseline (kanoniczny venv `/root/.openclaw/venvs/dispatch/`): 3380 pass / 11 fail / 26 skip / 7 xfail** (100s). 11 failów = znany pre-existing set: `test_courier_reliability` ×8, `test_flag_doc_coverage::test_no_new_undocumented_decision_flag`, `test_objm_lexr6_select_faza2::test_flag_default_off`, `test_working_override_2026_06_01::test_13`. ⚠️ METODYKA: pierwszy run systemowym `python3` dał 118 fałszywych `ModuleNotFound` — **baseline TYLKO kanonicznym venv** (CLAUDE.md). `test_bug2_newdrop` NIE jest realnym failem (był artefakt złego interpretera). ⚠️ **`test_flag_doc_coverage` JUŻ czerwony w baseline** — mój nowy flag `ENABLE_ETA_PICKUP_REALISTIC` MUSI być udokumentowany (nie pogorszyć checkera).
- **atq shadow:** 188/182/168/167 — żaden nie dubluje tego tematu. OK.
- **flaga (nowa):** `ENABLE_ETA_PICKUP_REALISTIC` OFF default. Serwis liczący propozycję = `dispatch-shadow`; `target_pickup_at` w `shadow_dispatcher._serialize_result`. Stan flagi czytać z `FLAG_FINGERPRINT` procesu (wzorzec #9).
- **Materialność:** udowodniona, Adrian zwalnia z ponownego pomiaru. `tools/measure_bug1_eta_vs_freeat.py` (3 dni): 34,7% propozycji zajętych pokazuje odbiór < free_at; podział na NAIVE (`bug2_pickup_src=ready_time`) vs legalny interleave.

## ETAP 1 — ŹRÓDŁO (NIE OBJAW) — ZNALEZIONE DOKŁADNIE
**Pole, które widzi koordynator I którym AUTO-ASSIGN commituje czas:** `target_pickup_at`.
- Liczone: **`shadow_dispatcher.py:526-527`** → `target_dt = max(eta_pickup_utc, pickup_ready_at)`.
- Konsumowane jako COMMIT: **`auto_assign_executor.py:191`** (`time` dla gastro_assign = `target_pickup_at - now`).
- Konsumowane jako DISPLAY: serializowane `target_pickup_at` + `eta_pickup_hhmm` → panel/feed/Telegram.

**Dlaczego kłamie:** `target = max(eta_pickup_utc, pickup_ready_at)` — **ŻADEN człon nie zna `free_at`** (kiedy zajęty kurier skończy NIESIONY worek). Gdy `eta_pickup_utc` przyszło z gałęzi BLIND `haversine` (`dispatch_pipeline.py:3855`, `now + OSRM(courier_pos→pickup)`, dla świeżej propozycji bez planu nowego ordera) → target=floor do `pickup_ready_at` (gotowość jedzenia), ślepy na carried → mediana 24 min za wcześnie. Dla Citki: jedzenie Eat Point gotowe ~14:58, ale realnie odbierze po dowiezieniu Parkowej → ~15:0x.

**Reconcyliacja paradoksu (kluczowe ustalenie audytu):**
- Gdy plan ISTNIEJE i nowy order jest w `plan.pickup_at` → `eta_source="plan"` (`dispatch_pipeline.py:3847`) = **insertion-aware, uczciwe**.
- Gałąź BLIND `haversine` pali się gdy `plan is None` (feasibility early-reject PRZED budową planu: `bag_full`/`pickup_too_far`/`shift_ending` itd., `feasibility_v2.py:455/465/652/722/781`) LUB order już `picked_up`.
- **FEASIBILITY (R6 i R27) JEST JUŻ UCZCIWE/insertion-aware:** R6 z `plan.predicted_delivered_at` (`feasibility_v2.py:1033/1079`, anchor `pickup_ready_at` via `r6_thermal_anchor`); R27 jako TSP time-window `[ck-5, ck+5]` HARD dla committed (`route_simulator_v2.py:988-1001`, `ENABLE_V3274_FROZEN_PICKUP_WINDOW`). **Czyli kłamie tylko prezentacja/commit `target_pickup_at`, NIE bramka feasibility.**

→ **Fix u źródła = dołożyć człon `free_at` do floora `target_pickup_at` w `shadow_dispatcher.py:526`** (additive; no-op gdy eta już plan-aware, bo plan.pickup_at ≥ free_at+drive). To naprawia 1 polem: display (target/eta_hhmm) + commit (auto_assign) RAZEM.

## ETAP 2 — HARD vs SOFT + INWERSJE → **⛔ JEDNA DECYZJA ADRIANA (P-1/R27)**
Feasibility (HARD) nietknięte. Ale jest realna rozbieżność, którą trzeba świadomie rozstrzygnąć:
- Dla **NOWEGO** ordera (czas_kuriera jeszcze niecommitowany) feasibility używa okna **60 min** (`V327_PICKUP_TIME_WINDOW_CLOSE_MIN`), NIE ±5. Więc zajęty kurier z realnym odbiorem +24 min PRZECHODZI feasibility (≤60) — propozycja jest „legalna", choć łamie życzenie R27 ±5.
- Adrian (pkt 2) chce: „propozycja nie łamie R27 ±5". To zderza się z oknem-60 dla nowych. **Dwa warianty fixu:**
  - **B1 — uczciwa liczba, okno bez zmian:** `target_pickup_at` pokazuje/commituje REALNY czas (z floorem free_at), wszędzie spójnie. Propozycje jak dziś (okno 60). Liczba przestaje kłamać. Zero zmiany selekcji/feasibility. **Najbezpieczniejsze, pełne „100% progres" na tym co widać i co się commituje.**
  - **B2 — uczciwa liczba + twardszy R27 dla zajętych:** jw. ORAZ gdy realny odbiór > `czas_kuriera + R27(±5)` → NIE auto-proponuj / werdykt KOORD. To jest świadoma INWERSJA okna-60→±5 dla nowych → **zmienia selekcję (mniej interleave na zajętych), wymaga pełnej ścieżki ETAP 4 e2e przez feasibility+selekcję + replay ON↔OFF + ACK na inwersję.**

**Rekomendacja:** **B1 najpierw** (pewny, szybki, zero ryzyka decyzji — realizuje „jedna prawdziwa liczba wszędzie"), **B2 jako osobny sprint** jeśli po B1 chcesz twardo ucinać spóźnione odbiory zajętych. Mieszanie obu w jednym = łamie „SOFT nie osłabia HARD bez ACK".

## ETAP 3 — MAPA KOMPLETNOŚCI (decyzja B1; B2 dokłada blok selekcji)
| Klasa | Miejsce (plik:linia) | Dotknięte? (B1) |
|---|---|---|
| **Compute źródła** | `shadow_dispatcher.py:526-527` `target_dt=max(eta,ready)` → `max(eta,ready,free_at_floor)` | ✅ rdzeń fixu |
| free_at floor input | `best_m.free_at_utc`/`free_at_min` (serializ. 297/298/627/628) + drive last_drop→pickup | ✅ użyć (już liczone) |
| eta blind branch | `dispatch_pipeline.py:3855` haversine | ⛔ N-D (nie ruszać eta_pickup_utc — karmi extension_penalty/hard-reject; floor robimy w target) |
| **Commit auto-assign** | `auto_assign_executor.py:191` (`time` z target_pickup_at) | ✅ automatycznie poprawne (czyta naprawiony target) |
| **Commit accept ducha** | `Ops13Console.tsx:661` `time_arg=eta_pickup_hhmm` | ⚠️ ZMIENIĆ na realny (eta_pickup_hhmm musi pochodzić z naprawionego target — patrz display) |
| Serializer A | `shadow_dispatcher.py:274` `eta_pickup_hhmm` | ✅ derive z target (lub dodać realistic) |
| Serializer B | `shadow_dispatcher.py:599/601` `eta_pickup_hhmm`+`target_pickup_at` | ✅ bliźniak A |
| Display konsola | `Ops13Console.tsx:656/672/1137` | ✅ |
| Display feed | `Ops12ZiomekFeed.tsx:52-55` | ✅ |
| Display Telegram | `telegram_approver.py:347/871/1318` (dziś wyciszony) | ✅ parytet |
| Shadow monitor | `ShadowMonitorPage.tsx:274` | 🔵 opcja (kolumna realistic do walidacji) |
| **APKA kuriera** | `courier_api/courier_orders.py:641-892` (`_committed_pickup_eta`, `FROZEN_PICKUP_ETA`) — czyta `czas_kuriera_warsaw` | ✅ gdy commit=realny, apka pokaże realny (frozen-doctrine zgodne) |
| **RESTAURACJA** | `panel .../deliveries.py:102/491` `promised_pickup_at = now + proposal.eta_pickup_min` (NIE czyta czas_kuriera!) | ⚠️ **LUKA: restauracja widzi INNĄ liczbę** — musi czytać committed czas_kuriera, inaczej „jedna liczba wszędzie" niespełnione |
| Doktryna frozen-pickup | `ENABLE_FROZEN_PICKUP_ETA` (apka) / `PIN_AGREED_PICKUP_TIME` (konsola) | ✅ ZGODNE — oba tylko PINują/wyświetlają committed; realny commit pokażą bez jittera (audyt potwierdził brak konfliktu) |
| Nowa flaga | `ENABLE_ETA_PICKUP_REALISTIC` OFF + test ON≠OFF + checkery | ✅ |
| State handlery | `state_machine.py:509/591/654/707` (czas_kuriera_warsaw+hhmm symetrycznie) | ✅ N-D zmian (już symetryczne; commit wpływa przez time_arg) |

**Bliźniaki RAZEM:** serializer A+B; display konsola+feed+Telegram; commit auto-assign + accept-ducha; **apka↔restauracja (dziś czytają 2 różne źródła — to największa luka „jednej liczby").**

## ETAP 4 — DOWODY (do wykonania w sprincie)
flaga ON≠OFF (busy→target≥free_at; idle→bez zmian) · metryka `target_pickup_realistic` w shadow_decisions.jsonl · parytet serializer A↔B · checkery flag · **pełna regresja vs baseline 3420** + nowe `test_eta_pickup_realistic.py` · e2e `assess_order` replay Citko(457)+Bartek(123) · **e2e „jedna liczba": ten sam czas w `target_pickup_at` (konsola/commit) ↔ `czas_kuriera_warsaw` (apka) ↔ restauracja**.

## ETAP 5 — POZYTYWNY WPŁYW (= dowód poprawności)
Replay 3 dni: dla `eta_source=haversine` + busy porównać naprawiony `target` vs realny pickup z historii → bliżej rzeczywistości niż blind (blind med −24 → ~0). +2 dni okno.

## ETAP 6 — DEPLOY
`.bak`→py_compile→testy→`git log -3` (kolizja `shadow_dispatcher.py` z `4812f28`!)→commit jawne→restart `dispatch-shadow` off-peak ACK→build panelu→`gps.nadajesz.pl/admin`→`ZIOMEK_LOGIC_REFERENCE.md`.

## ETAP 7 — ROLLBACK
`ENABLE_ETA_PICKUP_REALISTIC=false` (hot) / `.bak` / `git revert`. Panel fallback `?? eta_pickup_hhmm`.

---

---

## ETAP 4 — IMPLEMENTACJA (wariant B1, wykonane 2026-06-27) — SILNIK GOTOWY, flaga OFF
**Decyzja Adriana: B1** (jedna prawdziwa liczba; feasibility R6/R27 nietknięte). Restaurację wyrównujemy (jego wymóg „wszędzie spójny").
Mechanizm potwierdzony NA ŻYWO przed kodem (`measure_bug1`: 20% zajętych 27.06, wszystkie `ready_time`, mediana 24,4 min; surowe rekordy: `free_at_utc` OBECNE → floor się odpali; Citko 483665 +13,4 min).

**Zrobione (silnik, flaga `ENABLE_ETA_PICKUP_REALISTIC` OFF = zero wpływu na produkcję):**
- `dispatch_pipeline.py` (~3982): liczy `eta_pickup_realistic_utc = max(eta_pickup_utc, free_at_dt + dojazd ostatni_drop→pickup)`; additive, NIE rusza `eta_pickup_utc`; no-op gdy wolny/insertion-aware; fail-soft.
- `dispatch_pipeline.py` (~5122): serializacja metryki obok `free_at_utc`.
- `shadow_dispatcher.py`: helper `_target_pickup_floor(best_m, eta_dt, ready_dt)` (testowalny) → `target_pickup_at` (=display+commit auto_assign) używa realistycznego gdy flaga ON; serializery A+B emitują `eta_pickup_realistic_hhmm`.
- `ZIOMEK_LOGIC_REFERENCE.md`: flaga udokumentowana.
- Testy: `tests/test_eta_pickup_realistic_2026_06_27.py` 6/6 ON≠OFF (busy→realny, idle→bez zmian, brak pola→fallback, gotowość-floor wygrywa).
- py_compile+import OK. Backupy `*.bak-pre-bug1-eta-realistic-2026-06-27`.
- Regresja flagi-OFF (stan pre-refaktor): **3380 pass / 11 known-fail = byte-identyczna z baseline**. Finalna (po refaktorze+nowe testy): w toku.

**Zrobione (PANEL — repo nadajesz_clone, wspólny deploy; git status = tylko moje 4 pliki):**
- `backend/.../ziomek/feed.py`: `CandidateOut.eta_pickup_realistic_hhmm` + mapowanie z `raw` (FastAPI serializuje dataclass → pole dochodzi do API). Backup `.bak-pre-bug1-eta-realistic-2026-06-27`.
- `frontend-shared/.../ziomek/api.ts`: pole w `ZiomekCandidate`.
- `frontend-shared/.../coordinator/Ops13Console.tsx` GhostTile: `pickupShown = eta_pickup_realistic_hhmm ?? eta_pickup_hhmm` → display (672) + late-calc (656) + **accept `time_arg` (661)** = JEDNA liczba display+commit, fallback do blind gdy flaga OFF.
- `frontend-shared/.../ziomek/Ops12ZiomekFeed.tsx`: display realistic (52-55).
- Typecheck `tsc -b` z `frontend/`: moje pliki **czyste**. ⚠️ jeden błąd TS w `ControllingPanel.tsx:487` = **pre-existing/inna sesja (NIE mój)** — blokuje build panelu, do naprawy przed deployem panelu.

**KOREKTA AUDYTU — RESTAURACJA: BEZ ZMIANY (już wyrównana).** `deliveries.py:489` czyta odbiór: **kanon Ziomka (`courier_plans.json`) → obietnica → snapshot** + floor do committed `czas_kuriera` (`FLOOR_PICKUP_DISPLAY_TO_AGREED`). Kanon (plan insertion-aware) i committed (po fixie realny) → restauracja pokazuje realny czas automatycznie. `promised_pickup_at` z proposal = tylko zdegradowany fallback przed przypisaniem. Wcześniejszy audyt przeszacował tę lukę.

**ZAKRES B1 = KOMPLETNY w kodzie.** Apka kuriera: realny przez committed `czas_kuriera` (frozen-doctrine). Konsola: GhostTile realny + commit. Auto-assign: commituje realny `target_pickup_at`. Restauracja: kanon+committed (bez zmian). Feasibility R6/R27 nietknięte.

## ETAP 5 — WERDYKT REPLAY: ⚠️ NIEJEDNOZNACZNY → **NIE FLIPUJEMY** (na razie shadow)
Replay 27.06 (`eod_drafts/2026-06-27/replay_bug1_positive_impact.py`, actuals z `events.db COURIER_PICKED_UP`):
- 4 bugowe propozycje (zajęty, blind<free_at), korekta blind→realny median **+24,4 min** (mechanizm „za wcześnie" potwierdzony).
- ALE vs FAKTYCZNY odbiór, jedyny weryfikowalny **same-courier** (483695 Chicago cid 508): blind 13:56 / realny 14:07 / **FAKT 13:49** → `|blind−fakt|=7min` < `|realny−fakt|=18min` → **realny GORSZY** (przestrzelił w późno; `free_at` pesymistyczny — kurier skończył worek szybciej niż plan).
- Citko (483665): realny lepszy (4 vs 9 min). Mieszane, n=1 weryfikowalny → **niejednoznaczne, raczej ostrzegawcze**.
- **PRZYCZYNA RYZYKA:** `free_at_dt` = `plan.predicted_delivered_at[last_bag_oid]` ma własny błąd (bywa pesymistyczny). Floor display do niego propaguje ten błąd → zamiana „kłamie za wcześnie" na „kłamie za późno". Reassignment do wolnego kuriera dodatkowo myli (zlecenie często przejmuje ktoś wolny → odbiór ~ready, nie ~free_at).

**DECYZJA (zgodna z regułą Adriana „udowodnij że WARTO przed flipem"):** realny **liczony ZAWSZE (shadow log-only)** — `dispatch_pipeline` compute bez bramki flagi; UŻYCIE w `target_pickup_at` nadal gatuje flaga OFF → **behawior produkcji bez zmian**. Regresja 3386/11. Werdykt 2-dniowy: **at-job 191 (29.06 18:30)** → `dispatch_state/bug1_eta_realistic_verdict.txt`.
**GATE FLIPU:** dopiero gdy na większej próbie same-courier `|realny−fakt|` MIERZALNIE < `|blind−fakt|`. Inaczej — poprawić estymator (np. precyzyjny OSRM insertion zamiast `free_at+haversine`, albo użyć `plan.pickup_at` świeżego solve nowego ordera) i ponowić.

**PANEL:** zmiany wpięte (fallback do blind gdy pole==eta → przy OFF panel = jak dziś). Bezpieczne do deployu kiedykolwiek (nie zmienia nic póki silnik nie poda realnego ≠ eta... a podaje TYLKO dla zajętych — ⚠️ UWAGA: po deployu panelu BEZ flipa flagi, panel zacznie pokazywać realny dla zajętych, bo compute jest ZAWSZE. **Więc panel NIE deployować przed werdyktem flipa** — inaczej pokaże „za późno" liczby. Albo: panel czyta realny tylko gdy osobna flaga — TODO jeśli chcemy rozdzielić.).

## ETAP 6 — DEPLOY: **WSTRZYMANY do werdyktu ETAP 5 (29.06)**
- Silnik: kod LIVE-ready ale **flaga OFF zostaje**; compute-always = bezpieczny (log-only). Restart `dispatch-shadow` MOŻNA (żeby zacząć logować realny do shadow), off-peak + ACK — to NIE zmienia decyzji.
- Panel: **NIE deployować** przed flipem (compute-always sprawi, że panel pokaże realny dla zajętych nawet bez flipa — patrz wyżej). Najpierw werdykt; ew. rozdzielić flagą display.
- `ControllingPanel.tsx:487` TS error (cudzy) blokuje build panelu niezależnie.

## DWA BLOKERY STARTU KODU (rozstrzygnięte)
✅ B1 wybrane. ✅ Restauracja w zakresie. ✅ Baseline znany (3380/11).
Pozostały bloker DEPLOYU: ACK Adriana na flip flagi + restart off-peak (NIE w peaku).
1. **Decyzja Adriana B1 vs B2** (ETAP 2 — inwersja okno-60↔R27±5 dla nowych zleceń).
2. **Restauracja czyta inne źródło** (`promised_pickup_at` z proposal, nie `czas_kuriera`): czy „jedna liczba wszędzie" obejmuje wyrównanie restauracji do committed (rekomendowane), czy restaurację zostawiamy na osobny krok.
3. **Baseline 1 fail** (`test_bug2_newdrop_fix...`) — ustalić known-fail vs skutek `4812f28` zanim cokolwiek dotykam.

---

## ⛔ DIAGNOZA KOŃCOWA (2026-06-27) — FIX ODRZUCONY, kod ZREWERTOWANY
Adrian: „popraw, ale wcześniej zdiagnozuj czy będzie 100% progres". Diagnoza na DUŻEJ próbie
(`eod_drafts/2026-06-27/diag_estimators_vs_actual.py`, 68 same-courier matched busy, actuals
z `events.db`, logi `shadow_decisions.jsonl`+`.1`) — mediana |estymator − FAKTYCZNY odbiór|:

| estymator | mediana | wygrane (najbliżej faktu) |
|---|---|---|
| **blind = target_pickup_at (dziś)** | **7,4 min** | 18% |
| free_at / „realny" (mój fix) | **20,2 min** | 0% |
| plan.pickup_at[new] / new_pickup_eta | 7,1 min | 18% |
| **target_pickup_debiased (blind+4,5)** | **5,8 min** | 49% |

**WNIOSKI:**
1. **Mój free_at-fix jest NET-SZKODLIWY** (≈3× gorszy od blind) → ODRZUCONY, kod zrewertowany (`d5f90d0` silnik, `fc9cc3a` panel). Flaga `ENABLE_ETA_PICKUP_REALISTIC` usunięta z drzewa.
2. **„Bug 24 min" był ARTEFAKTEM POMIARU** (`measure_bug1` liczył blind-vs-`free_at`, a `free_at`=`predicted_delivered_at` jest pesymistyczny). **Wobec REALNYCH odbiorów blind jest celny (~7 min).** Premisa Bug #1 obalona pomiarem.
3. Jedyny realny (marginalny) lewar: **`target_pickup_debiased` (+4,5 min)** = mediana 5,8 min, wygrywa 49%. Ale to **osobny, JUŻ istniejący track** `ENABLE_PICKUP_DEBIAS_SHADOW` (TIER-1 debias 2026-06-22, OOS −47% spóźnień). NIE część tego sprintu.

**STAN:** kod zrewertowany (oba repo), at-191 skasowany, `.bak-pre-bug1-*` zostają (24h). Bug #1 (free_at) ZAMKNIĘTY jako measure-first rejection. Ewentualny follow-up = promocja `target_pickup_debiased` z shadow do live (osobny temat, przez protokół; dowód OOS już istnieje).
