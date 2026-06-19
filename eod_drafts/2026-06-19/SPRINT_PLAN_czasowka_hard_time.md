# SPRINT PLAN — Fix B: twardy czas czasówki w decyzji Ziomka (MIERZONY, replay-first)

**Data:** 2026-06-19
**Status:** PLAN — do akceptacji Adriana. ZERO zmian w kodzie żywego silnika do czasu Faza 0 GO.
**Zasada (Adrian 14.06):** zweryfikuj bieżący stan → baseline → udowodnij REPLAYEM że WARTO i BEZ REGRESJI, ZANIM kodujesz/flipujesz. Net-szkodliwe/no-op → NIE rób, werdykt z liczbami.
**Reguła kardynalna:** R-DECLARED-TIME (HARD) — `czas_kuriera ≥ czas_odbioru_timestamp`; czasówka (`czas_odbioru ≥ 60`) = twarda deklaracja, trzymana w Koordynatorze (id_kurier=26).

---

## 0. Problem (z panelu, 2026-06-19)

Czasówka „Raj" 11:38: panel pokazał propozycję shadow „Gabriel, odbiór 12:00" (start jego zmiany), choć w puli był Rutkowski 11:00 (na czas — i to on został LIVE przypisany). Silnik ranguje po WYNIKU (bliskość/trasa/obciążenie), a spóźniony odbiór traktuje jako koszt MIĘKKI → kandydat lepiej ustawiony, lecz spóźniony, może przebić tego na czas.

**Grunt (skan `shadow_decisions.jsonl`, 197 decyzji PROPOSE z best):**
- 22 (11,2%) → proponowany odbiór ≥10 min PO `pickup_at_warsaw`.
- **22/22 miały `order_type`=∅ i `czas_kuriera_warsaw`=∅ w chwili oceny** (0 z ustawionym twardym czasem).
- 18/22 (82%) miały kandydata „na czas" w alternatywach → naprawialne wyborem innego.

⚠️ KLUCZOWE ZASTRZEŻENIE: `order_type`=∅ to TEŻ normalny stan zwykłego zlecenia (elastyk). Skan NIE dowodzi, że te 22 to czasówki — pokazuje tylko, że NIGDY nie widzimy w decyzji twardego czasu. To jest dokładnie premisa do obalenia/potwierdzenia w Fazie 0.

## 1. Dwie hipotezy roota (rozłączne interwencje)

- **B1 — PROPAGACJA (tania, deterministyczna):** atrybuty czasówki (`order_type=czasowka` + twardy `czas_kuriera_warsaw`) NIE docierają do `assess_order` w chwili pierwszej oceny — panel gastro dostarcza je później, `panel_watcher._diff_pickup`/`_diff_czas_kuriera` wpina je z opóźnieniem („null→value, panel dostarczył późno"). Skutek: ocena „na ślepo", bez twardego okna. Gdyby czas był obecny — istniejące **R27 ±5 frozen window (V3.27.4, `ENABLE_V3274_FROZEN_PICKUP_WINDOW`)** już by zadziałało.
- **B2 — OBJEKTYW (droższa, ryzykowna):** nawet z obecnym czasem, spóźniony odbiór to kara miękka → trzeba hard-gate / dominującej kary dla czasówek. Dotyka scoringu/feasibility = ryzyko ↑KOORD/infeasible.

**Hipoteza wiodąca:** B1 dominuje (22/22 bez czasu). B2 wtórny (jeśli po B1 nadal spóźnienia na czasówkach z OBECNYM czasem).

## 2. Czy to w ogóle problem LIVE? (a nie tylko cień/panel)

Krytyczne dla priorytetu. Żywą czasówkę realnie przydziela `czasowka_scheduler` BLISKO czasu (T-60/T-50/T-40), czytając `orders_state` PO wpięciu twardego czasu — i nasz przypadek wyszedł poprawnie (Rutkowski). Shadow loguje wcześniejszą, ślepą ocenę.

**Faza 0 musi rozstrzygnąć:** czy czasówki bywają LIVE przypisane na podstawie ślepej wczesnej oceny (AUTO/koordynator z panelu), czy `czasowka_scheduler` ZAWSZE domyka je poprawnie blisko czasu? Jeśli zawsze poprawnie → Fix B to głównie czyszczenie sygnału cienia/panelu (niższy priorytet; panel już ostrzega — commit `4644949`). Jeśli bywają realne błędne przydziały → Fix B ma wartość LIVE.

---

## FAZA 0 — POMIAR PREMISY (read-only, ZERO zmian, ~1 dzień danych)

Cel: potwierdzić/obalić B1 vs B2 i policzyć realny zasięg. Wszystko offline na logach + orders_state snapshotach.

**0.1 Identyfikacja czasówek w logu.** `order_type` w decyzji jest ∅ — potrzebny inny sygnał:
- (a) JOIN `shadow_decisions.order_id` → `orders_state.json` / `state_snapshot` (dispatch-state-snapshot daily) gdzie `order_type=czasowka` osiada.
- (b) ALBO sygnał z panelu: `czas_odbioru ≥ 60` (czasówka) — sprawdzić czy `panel_watcher` ma to w surowym payloadzie (`edit-zamowienie`).
- Deliverable: lista historycznych order_id = czasówki + ich twardy czas (z orders_state).

**0.2 Re-skan TYLKO czasówek (nie wszystkich):** dla każdej czasówki policz na decyzji shadow: `eta_pickup(best) − czas_kuriera_warsaw(twardy)`. Ile czasówek dostało best z odbiorem > twardy czas + tolerancja (R27 = +5)? To prawdziwy licznik (nie 11,2% z miksu).

**0.3 B1 vs B2 split:** dla spóźnionych czasówek — czy `czas_kuriera_warsaw` BYŁ w `order_event` w chwili oceny?
- ∅ → **B1** (propagacja). Policz % decyzji czasówek, gdzie czas dochodzi PO pierwszej ocenie (porównaj `ts` decyzji vs moment wpięcia `czas_kuriera` z `panel_watcher` log / event_bus `CZAS_KURIERA_UPDATED`).
- ustawiony, a mimo to spóźniony best wygrał → **B2** (objektyw). Sprawdź czy `ENABLE_V3274_FROZEN_PICKUP_WINDOW` był ON i czemu nie zadziałał.

**0.4 Impact LIVE:** dla spóźnionych czasówek — kto je realnie przypisał i kiedy?
- JOIN do `audit_log`/`orders_state` (`assigned_at`, `courier_id`) + `czasowka_eval_log.jsonl` (czasowka_scheduler).
- Pytanie: finalny kurier = ślepy best wczesny, czy inny (blisko czasu)? Policz: ile czasówek skończyło u SPÓŹNIONEGO kuriera vs naprawione przez scheduler.

**BRAMKA Faza 0 (GO/STOP):**
- Jeśli realnych spóźnionych czasówek LIVE (0.4) ≈ 0 (scheduler domyka) → **STOP Fix B w silniku**; wystarczy panel-warning + ewentualnie czyszczenie cienia. Werdykt: net-no-op dla live.
- Jeśli B1 dominuje i są realne błędy → **GO Faza 1 (propagacja)**.
- Jeśli B2 istotny → **GO Faza 1' (objektyw)**, ale dopiero po replayu (Faza 3).

---

## FAZA 1 — INTERWENCJA B1 (propagacja czasówki), za flagą OFF

Tylko jeśli Faza 0 wskaże B1. Wariant zależny od 0.3:

- **B1a (czas dochodzi późno z panelu):** przy NEW_ORDER dla czasówki (`czas_odbioru ≥ 60`) wyprowadź twardy czas odbioru z `czas_odbioru_timestamp` od razu (nie czekaj na późny `czas_kuriera`), wpinając go jako committed do `order_event` → aktywuje istniejące R27 ±5.
- **B1b (czas jest, ale gubiony między warstwami):** napraw passthrough `order_type`+`czas_kuriera_warsaw` w `shadow_dispatcher._build order_event` (l. ~1015-1026) / `panel_watcher` emit. (Lekcja #80: pole gubione między warstwami — audyt KONSUMENTÓW.)

Flaga: `ENABLE_CZASOWKA_HARD_TIME_AT_NEW_ORDER` (default OFF, env+flags.json hot). Defense-in-depth: brak `czas_odbioru_timestamp` → fallback do obecnego zachowania, NIGDY crash.

## FAZA 1' — INTERWENCJA B2 (objektyw), za osobną flagą OFF

Tylko jeśli po B1 nadal spóźnione czasówki z OBECNYM czasem. Opcje (od najmniej do najbardziej inwazyjnej):
- (i) rozszerz R27 frozen window żeby obejmował czasówki uncommitted (nie tylko committed `czas_kuriera`).
- (ii) twarda kara/feasibility=NO gdy `eta_pickup > czasówka + TOL` (TOL≈5–10, env).
- ⚠ Każda dotyka rankingu → obowiązkowy replay (Faza 3) + gate G2.

---

## FAZA 2 — HARNESS REPLAY

Wzór: `obj_replay_capture.py` / `replay_failed.py` (UWAGA Lekcja: `replay_failed.py:132` używał surowego `build_fleet_snapshot` — użyj `dispatchable_fleet()` jak shadow/czasowka_scheduler, inaczej `v325_NO_ACTIVE_SHIFT` fałszuje całość).
- Wejście: historyczne czasówki (Faza 0.1) z capture floty/stanu w chwili decyzji.
- Dwa przebiegi per zlecenie: **OFF** (baseline = obecny silnik) vs **ON** (flaga Fazy 1/1').
- Zbierz per zlecenie: verdict, best.courier_id, best.eta_pickup, feasibility, pool_feasible, czy on-time.
- Minimalna próba: ≥2 tygodnie czasówek lub ≥150 zdarzeń (jak food-age n=77+ dało sens).

## FAZA 3 — BRAMKI (z liczbami, jak N5/food-age)

- **G1 (poprawa):** liczba czasówek z odbiorem na czas ON−OFF > 0 istotnie; suma minut spóźnienia ↓. (Cel kierunkowy: zredukować ~82% „naprawialnych wyborem innego".)
- **G2 (regresja):** % zleceń z gorszym wynikiem (nowy KOORD / spadek pool_feasible / infeasible) ON vs OFF. Próg: jak N5-S2 — pojedyncze % akceptowalne, ale **INFEASIBLE delta ≈ 0** (zero nowych fallbacków) i KOORD-regres < kilka %. Net minut musi być dodatni.
- **G3 (outcome, jeśli da się zjoinować):** na zleceniach z `delivered_at` — czy ON nie pogarsza realnych dostaw (12/12 delivered jak N5).
- Werdykt: GO tylko gdy G1↑ ∧ G2 w progu ∧ G3 OK. Inaczej STOP + raport.

## FAZA 4 — FLIP (osobny ACK Adriana)

- Flip flagi w `flags.json` (hot) lub env + restart `dispatch-shadow` off-peak; `py_compile` + import check + pełna suita PASS przy ON.
- Monitor: `grep czasowka_hard_time shadow_decisions.jsonl` + `czasowka_eval_log.jsonl` kilka dni.
- Rollback: flaga=false (hot, ~5s) / `cp flags.json.bak-pre-czasowka-hard-*`.

---

## Pliki / kotwice kodu (do audytu w Fazie 0/1)

- `panel_watcher.py` — czasówka detect (`order_type=="czasowka"`, l.~979), `_diff_czas_kuriera`/`_diff_pickup` (l.~700/782, „null→value późno").
- `shadow_dispatcher.py` — build `order_event` (l.~1015-1026: `czas_kuriera_warsaw`/`pickup_at_warsaw` passthrough z payload).
- `dispatch_pipeline.py` — V3.19f fallback chain `czas_kuriera→pickup_at→czas_odbioru` (l.~2869-2880), early-bird→KOORD (l.~2900), R27 frozen window (`ENABLE_V3274_FROZEN_PICKUP_WINDOW`, `V3274_FROZEN_PICKUP_WINDOW_MIN=5`).
- `czasowka_scheduler.py` — near-time eval (T-60/50/40), `_minutes_to_pickup` z `pickup_at_warsaw` (l.~138), build order_event (l.~314-341).
- Panel API: `czas_odbioru ≥ 60` = czasówka; `czas_odbioru_timestamp` (Warsaw); `czas_kuriera` HH:MM declared.

## Ryzyka

- B2 (objektyw) może ↑KOORD wieczorem (mało GPS) — dlatego za osobną flagą + gate G2 INFEASIBLE≈0.
- Replay bez `dispatchable_fleet()` = fałszywe „wszyscy odrzuceni" (znany landmine).
- Strefy: ZAWSZE UTC↔Warsaw przed liczeniem „przed/po" (lekcja 2026-06-19: błędne „3h" = pomyłka stref; `minutes_ahead` w logu = źródło prawdy).
- Współdzielone repo paneli — nie dotyczy dispatch_v2, ale capture/replay nie może blokować live `dispatch-shadow`.

## Szac. nakład

Faza 0: ~3–4h (skrypty analityczne, read-only). Faza 1/1'+2: ~1 dzień. Faza 3: replay + raport ~0,5 dnia. Razem ~2 dni robocze, rozłożone (replay calibration może czekać na ≥2 tyg. danych).

---

# ✅ FAZA 0 — WYNIKI (uruchomiona 2026-06-19, read-only) + WERDYKT

## Dane
- **901 unikalnych czasówek** w `czasowka_eval_log.jsonl` (03.05→19.06, ~7 tyg.). Cykl scheduler-a: WAIT(7895)→EMIT(567)/FORCE_ASSIGN(742)/KOORD(238)/DONT_EMIT(239). match_quality: ideal(139)+good(1592) vs none(1276).
- **192 czasówki** z `orders_state` (snapshoty 12-14.06 + bieżący), 182 z przypisanym kurierem.

## Pomiary
**P1 — TIMING przydziału (n=183, assigned_at vs twardy czas):**
- przypisane **≤60 min przed odbiorem = 93%** (170/183), p50 = **37 min przed**, p90 = 55 min przed.
- przypisane **>90 min przed (wczesne/ślepe) = 1%** (1/183).
→ LIVE przydziela czasówki **blisko czasu** (scheduler), NIE wczesną ślepą propozycją.

**P2 — czy SHADOW (wczesny, ślepy) steruje LIVE? (n=9 overlap):**
- finalny kurier **≠ wczesny best shadow = 78%** (7/9). Z 2 przypadków, gdzie wczesny shadow był SPÓŹNIONY (≥10min) → **2/2 (100%) skończyły u INNEGO kuriera**. (Mała próba — okno snapshotów 12-14.06 vs log shadow ~dziś; spójne z architekturą + przypadkiem 481758: shadow=Gabriel 12:00, live=Rutkowski 11:00.)

**P3 — OUTCOME odbioru (n=169 doręczonych, picked_up_at vs twardy czas):**
- na czas (≤+5min) 43%, spóźniony >+5 = 57%, ale **umiarkowanie**: p50 = 5,9 min, p90 = 13,4 min, **>+20min tylko 5%** (9). ⚠ Miesza opóźnienia RESTAURACJI (jedzenie niegotowe) z doborem kuriera — `arrived_at_restaurant` (GPS) nie wdrożone, więc nie da się czysto odseparować. NIE jest to dowód systematycznego przydzielania spóźnionych kurierów.

**P4 — B1 vs B2 (z wcześniejszego skanu, 22 spóźnione propozycje shadow):** 22/22 bez `czas_kuriera_warsaw` w decyzji = mechanizm SHADOW = **B1** (ocena bez twardego czasu). B2 niepotwierdzony (brak spóźnionych z OBECNYM czasem).

## 🛑 WERDYKT: STOP dla zmiany w SILNIKU (Fix B / B1 / B2)

**Uzasadnienie z liczbami:** spóźniony kurier („Gabriel 12:00") to **artefakt SHADOW/panelu, NIE błąd live dispatchu**:
1. Czasówki przydzielane near-time (93% ≤60min przed; tylko 1% wczesnych).
2. Wczesny ślepy best shadow NIE steruje przydziałem (78% mismatch; 2/2 spóźnione skorygowane; 481758 potwierdza).
3. Outcome spóźnień umiarkowany (p50 5,9; >20min 5%) i zanieczyszczony opóźnieniami restauracji.
4. B1 dotyczy tylko ścieżki shadow → naprawa = czyszczenie LOGU cienia, nie live. Dotykanie żywego scoringu/feasibility = realne ryzyko ↑KOORD/infeasible przy **~zero korzyści LIVE**. **Net: net-no-op/szkodliwe → NIE robić** (reguła Adriana).

**Co JUŻ rozwiązuje realny problem (panel):** ostrzeżenie „⚠ PO czasówce" + zielona alternatywa „na czas" (commit `4644949`) — operator nie da się zmylić wczesną propozycją.

**Opcjonalny tani follow-up (PANEL, nie silnik, niski priorytet):** nie pokazywać/akceptować propozycji Ziomka dla czasówki, dopóki nie dojdzie twardy czas (`czas_kuriera_warsaw` ustawiony) — eliminuje ślepą propozycję u źródła wyświetlania. Do rozważenia osobno.
