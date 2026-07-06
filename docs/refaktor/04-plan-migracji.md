# 04 — Plan migracji (Faza 4: strangler dla wariantu B, nie rewrite)

**Data:** 2026-07-06 · **Wariant:** B (ACK Adriana 06.07) + C zatwierdzony kierunkowo (ADR-R05, bez uruchamiania).
**Zasady obowiązujące KAŻDY krok:** protokół #0 ETAP 0→7 · pod-gałąź `refaktor/krok-NN-nazwa` (worktree per ADR-007) · testy charakteryzujące PRZED zmianą, zielone na starym kodzie · pełna regresja `pytest tests/` vs baseline (4239/0 na 06.07; baseline aktualizowany, bo inne sesje dopisują testy) · py_compile+import przed restartem · **każdy restart serwisu i flip flagi = koordynacja z FLIPMASTEREM + ACK Adriana** · okna poza peakami i poza So-Nd · commit `refactor(zakres): opis` atomowo po jawnych ścieżkach · wpis w `05-dziennik.md` po każdym kroku.

**Reguła rozmiaru:** PR ≤400 zmienionych linii. Wyjątek JAWNY: kroki-przenosiny (K11-K13) mechanicznie przenoszą bloki 1:1 — diff większy, ale kryterium = **bajt-parytet decyzji na korpusie** i zero zmian treści; oznaczone `[PRZENOSINY]`.

**Tryb shadow:** każdy krok zmieniający ścieżkę wykonania wchodzi za flagą (OFF = stara ścieżka bajt-w-bajt), zbiera parytet w shadow, flip dopiero po dowodzie — zgodnie z ADR-002 (shadow-first) i decyzją „shadow+replay = staging".

---

## PAKIET 0 — „Siatka i rozbrojenie min" (kroki 01-04; brak wpływu na żywe decyzje)

### K01 — tooling: ruff+mypy ratchet (D3)
- **Cel:** siatka bezpieczeństwa na cały program; zero zmian silnika.
- **Pliki:** NOWE: `tools/devlint/` (config ruff+mypy, skrypt `ratchet_check.py`, baseline snapshot), osobny venv narzędziowy (poza `venvs/dispatch` — ACK jest). Zero zmian w kodzie silnika.
- **Testy przed:** n/d (nic nie zmieniamy); baseline naruszeń = snapshot „dziś".
- **Kryterium:** `ratchet_check.py` zielony = liczba naruszeń ≤ baseline; wpięty do night-guard TYLKO informacyjnie (bez exit 1 do czasu okrzepnięcia).
- **Rollback:** usunięcie configów (nic nie konsumuje).

### K02 — rozbrojenie postpone_sweeper (D1)
- **Cel:** duplikat propozycji przestaje być miną armed-on-flip.
- **Pliki:** `postpone_sweeper.py` (schemat: `orders_state.get(oid)` + pole `courier_id`), nowy test.
- **Testy przed (charakteryzujące):** test ODTWARZAJĄCY bug — płaski dict → `POSTPONE_RESOLVED` nieosiągalny (czerwony na fixie, zielony na starym? odwrotnie: test dokumentuje ZŁE zachowanie starego kodu, po fixie asercja poprawna) + test happy-path sweepera.
- **Kryterium:** `POSTPONE_RESOLVED` osiągalny w teście; pełna regresja; timer bez zmian (ścieżka nadal no-op na pustym `postponed_proposals.json` — zachowanie live IDENTYCZNE).
- **Rollback:** revert commita (ścieżka i tak uśpiona).

### K03 — kanon zapisu dla ścieżek uśpionych (D1/backlog)
- **Cel:** `telegram_approver.save_pending` → delta przez `PPS.locked_mutate`; `global_alloc_store` → mkstemp; `courier_last_pos` → fcntl.
- **Pliki:** `telegram_approver.py` (1 funkcja), `tools/global_alloc_store.py`, `courier_resolver.py` (zapis store).
- **Testy przed:** istniejący `test_pending_fcntl_concurrency_l75` + nowe unit (2 procesy równolegle na last_pos → brak lost-update).
- **Kryterium:** testy zielone; zachowanie live identyczne (Telegram OFF, resweep single-writer).
- **Rollback:** revert per plik.

### K04 — nagrywanie świata v0 (ADR-R04, fundament korpusu)
- **Cel:** od tego kroku KAŻDA decyzja nagrywa wejścia: hash+treść FlagSnapshotu (na razie: zrzut flags.json raz na tick), macierz czasów OSRM użytą w decyzji, `now`, snapshot floty/zlecenia (rozszerzenie istniejącego `obj_replay_capture` — format ADDITIVE, nowy plik jsonl + GC).
- **Pliki:** `obj_replay_capture.py`, `shadow_dispatcher.py` (hook), `route_simulator_v2.py`/`osrm_client.py` (przechwycenie macierzy — tylko odczyt wyników), nowy test.
- **Testy przed:** charakteryzujący format istniejącego capture (nic nie psujemy); po zmianie: smoke „nagraj→odtwórz 1 decyzję offline".
- **Kryterium:** ≥95% decyzji z ticku ma kompletne nagranie (metryka w shadow); rozmiar/GC pod kontrolą; **zero wpływu na decyzje** (czysty odczyt).
- **Rollback:** flaga `ENABLE_WORLD_RECORD=false`.
- ⏳ **Po K04: 3-5 dni zbierania korpusu** (w tym ≥1 peak) zanim K06+ — korpus = bramka parytetu dla wszystkich dalszych kroków. Kandydaci do golden-setu: happy-path, best_effort, czasówka, paczka, KOORD ×6 klas, carried-first, no-GPS.

**STOP pakietu 0** — pokaz: diff-summary, regresja, pierwsze nagrania korpusu.

## PAKIET 1 — „Determinizm wejść" (kroki 05-08; serce odcinania rdzenia od I/O)

### K05 — FlagSnapshot per tick (ADR-R01; D5+D6)
- **Cel:** flagi czytane RAZ na tick; koniec ~700 odczytów dysku/decyzję i niespójności flag w środku decyzji.
- **Pliki:** `common.py` (snapshot-first w `flag()`/`decision_flag()` + kontekst snapshotu), `shadow_dispatcher.py` (utworzenie snapshotu na starcie `_tick`), test.
- **Testy przed:** charakteryzujące: (a) decyzja na korpusie z dzisiejszą ścieżką = baseline; (b) test „zmiana flags.json mid-tick zmienia zachowanie" (dokumentuje DZISIEJSZĄ wadę — po flipie musi się ODWRÓCIĆ: snapshot izoluje → to jest dowód ON≠OFF).
- **Kryterium:** flaga OFF = bajt-parytet na korpusie; flaga ON = parytet na korpusie przy stabilnych flagach + izolacja mid-tick + **pomiar perf p50/p95 przed/po** (oczekiwany spadek; jeśli brak poprawy — fakt do D6, nie blokada).
- **Rollback:** `ENABLE_FLAG_SNAPSHOT=false` (hot).
- **Deploy:** restart dispatch-shadow za ACK, poza peakiem.

### K06 — TravelTimeProvider (wstrzyknięcie OSRM)
- **Cel:** rdzeń przestaje wołać sieć — macierze/route liczone w powłoce i podawane argumentem.
- **Pliki:** NOWY `core/travel_time.py` (interfejs + impl. domyślna = dzisiejszy osrm_client), `route_simulator_v2.py`, `dispatch_pipeline.py` (`:4077/:4321` przez provider), `feasibility_v2.py` (przekazanie), test.
- **Testy przed:** golden route-order L6.A 13/13 + replay parytet na korpusie K04.
- **Kryterium:** bajt-parytet (provider domyślny = te same wywołania); replay offline działa z providerem „z nagrania" (pierwszy PRAWDZIWY replay bit-w-bit).
- **Rollback:** revert (interfejs przezroczysty, default = stara ścieżka).

### K07 — pre-proposal recheck poza ocenę
- **Cel:** żywy HTTP fetch panelu (`:3913`) przenosiony do budowy snapshotu PRZED pętlą kandydatów.
- **Pliki:** `dispatch_pipeline.py`, `shadow_dispatcher.py`, test.
- **Testy przed:** charakteryzujący dzisiejszą semantykę (świeży czas_kuriera wpływa na wszystkich kandydatów tak samo — właśnie dlatego przeniesienie jest bezpieczne).
- **Kryterium:** parytet na korpusie; flaga OFF = stara ścieżka.
- **Rollback:** flaga.

### K08 — powłoka efektów (część 1: shadow-writy + load-governor)
- **Cel:** `_emit_*`/`_append_*`/`_loadgov_*`/`send_admin_alert` z wnętrza assess → bufor `effects` w wyniku, flush po decyzji w `shadow_dispatcher`.
- **Pliki:** `feasibility_v2.py` (`:368/:409`), `dispatch_pipeline.py` (`:223/:245/:2776/:1249/:3694-3706`), `shadow_dispatcher.py` (flush), test.
- **Testy przed:** charakteryzujące zawartość każdego shadow-logu na korpusie (te same rekordy).
- **Kryterium:** identyczne rekordy w logach (kolejność w obrębie decyzji może się różnić — dopuszczalne, udokumentować); brak zapisów gdy decyzja rzuci wyjątek PRZED flush (zmiana semantyki na LEPSZE — odnotować w dzienniku).
- **Rollback:** flaga.

**STOP pakietu 1** — od tego momentu rdzeń nie robi sieci ani zapisów; pokaz: pierwszy replay bit-w-bit + pomiar perf.

## PAKIET 2 — „Rdzeń jako moduł" (kroki 09-13)

### K09 — `core/decide.py`: fasada `decide(world, order)` (delegacja 1:1)
- **Cel:** jedno wejście do decyzji; `world` = dataclass z już-wstrzykiwanych wejść (flota, zlecenie, FlagSnapshot, TravelTimeProvider, kalibracje, now).
- **Pliki:** NOWY `core/decide.py` + `world_state.py`; wywołujący: `shadow_dispatcher`, `czasowka_scheduler`, `auto_assign_gate`, toole replay.
- **Testy przed:** korpus-parytet (fasada = czysta delegacja).
- **Kryterium:** wszystkie call-site'y przez fasadę; bajt-parytet.
- **Rollback:** revert (fasada przezroczysta).

### K10-K12 — `[PRZENOSINY]` wycinanie warstw z `_assess_order_impl` (po jednym PR):
- **K10** `core/gates.py` — geokod-defense + early-bird (+ rekurencyjny kontrfaktyk).
- **K11** `core/candidates.py` — pętla per-kurier (`_v327_eval_courier_inner`) z feasibility+scoring.
- **K12** `core/selection.py` — selekcja + tiering + best_effort + bramki werdyktu.
- **Per krok:** testy przed = korpus-parytet + istniejące testy klastra; kryterium = bajt-parytet + pełna regresja + `_assert_feasibility_first` przechodzi w nowym miejscu (+ NOWY re-assert na EMIT — domknięcie INV-LAYER-HARD-BEFORE-SOFT 🔴, wzorzec #10); rollback = revert przenosin (git).
- **Efekt:** `_assess_order_impl` z ~3785 l. → orkiestrator ~kilkuset linii; monolit rozbity bez zmiany zachowania.

### K13 — Scorer jako strategia (ADR-R06)
- **Cel:** interfejs `Scorer`; HeuristicScorer = dzisiejsza suma kar (konsolidacja kar z common/pipeline do modułu scoringu); LgbmScorer = wrapper istniejącej inferencji shadow z fallbackiem.
- **Testy przed:** charakteryzujące wartości `bonus_penalty_terms` na korpusie.
- **Kryterium:** bajt-parytet score'ów; metryka `scorer_fallback` w shadow; flip LGBM primary POZA zakresem programu.
- **Rollback:** flaga.

**STOP pakietu 2** — pokaz: mapa nowych modułów, delta rozmiaru monolitu, parytety.

## PAKIET 3 — „Jeden Planner + plan_recheck" (kroki 14-15; ADR-R03)

### K14 — bramka feasibility na wyjściu `plan_recheck._sweep` (short-term D4)
- **Cel:** re-sekwencja nie może pogorszyć R6/committed.
- **Pliki:** `plan_recheck.py`, test.
- **Testy przed:** charakteryzujący dzisiejsze `_sweep` (golden sekwencje) + przypadek „nowa sekwencja gorsza R6" (odtworzenie z komentarza `:1019`).
- **Kryterium:** measure-first: 3-5 dni SHADOW (metryka `would_reject_reseq` w jsonl) → flip za ACK gdy odsetek odrzuceń sensowny (nie zamraża planów masowo); po flipie: q2_drift obserwowany (oczekiwany spadek — dowód pozytywnego wpływu ETAP 5).
- **Rollback:** flaga.

### K15 — plan_recheck przez Planner rdzenia
- **Cel:** wspólny `Planner` (route-sim+feasibility, config z FlagSnapshot — nie env procesu) dla ticku i sweep; koniec drugiego rdzenia i znaczenia env-rozjazdu.
- **Pliki:** `core/planner.py` (wydzielenie z K11), `plan_recheck.py` (delegacja), test.
- **Testy przed:** golden parytet sekwencji stara↔nowa ścieżka na nagranych planach.
- **Kryterium:** shadow-parytet ≥2 dni na żywych sweepach (obie ścieżki liczą, stara wykonuje, diff w jsonl) → flip za ACK; env drop-iny recanon wyrównane albo jawnie udokumentowane per-proces (deploy-krok z FLIPMASTEREM).
- **Rollback:** flaga (stara ścieżka sweep nienaruszona).

**STOP pakietu 3.**

## PAKIET 4 — „Pozycja i domknięcia" (kroki 16-17)

### K16 — hierarchia źródeł pozycji w WorldState (D7, strona konsumenta)
- **Cel:** JEDNO miejsce łączenia gps/pwa/last_pos z jawnym `pos_source` (typ Known|Unknown minimalnie — F-3); konsumenci czytają WorldState. Konsolidacja samych store'ów = backlog/wariant C.
- **Testy przed:** charakteryzujące dzisiejszą rezolucję pozycji per źródło (courier_resolver).
- **Kryterium:** bajt-parytet snapshotu floty; strażnik INV-SRC-EQUAL-TREATMENT dostaje punkt zaczepienia (1 resolver).
- **Rollback:** flaga.

### K17 — golden corpus jako bramka CI (F-6, domknięcie)
- **Cel:** replay korpusu (z K04, bit-w-bit od K06) wpięty do night-guard: każda nocna regresja odtwarza N decyzji i porównuje z zapisem.
- **Kryterium:** night-guard zielony 3 noce z rzędu z bramką ON; dokumentacja „jak dodać case do korpusu".
- **Rollback:** wyłączenie bramki w night-guard (informacyjna → egzekwująca stopniowo).

**STOP pakietu 4 → Faza 6 (weryfikacja końcowa + raport + aktualizacja ARCHITECTURE/CODEMAP).**

---

## RYZYKA PLANU I SYGNAŁY „PRZERWIJ MIGRACJĘ"

| Sygnał | Reakcja |
|---|---|
| Bajt-parytet kroku niespełnialny mimo 2 podejść (ukryta zależność od I/O/kolejności) | STOP kroku → korekta planu (rozbić krok / zmienić szew), NIE forsować „prawie-parytetu" |
| Pełna regresja czerwona nie-z-naszego powodu (inne sesje zmieniają master) | STOP → rekoncyliacja baseline'u z autorem zmiany (C1), dopiero potem dalej |
| p95 assess po kroku ↑ >10% na 2-dniowym oknie | rollback flagi kroku, analiza, powrót z poprawką |
| night-guard czerwony po naszym deployu | rollback PRZED następnym krokiem (zero nakładania hipotez) |
| Kolizja plików z FLIPMASTEREM / innym sprintem (rdzeń = jeden właściciel, ADR-007/008) | serializacja: nasz krok czeka; NIGDY równoległy deploy tego samego obszaru |
| Zbliża się peak / So-Nd / ogłoszony flip FLIPMASTERA | zero deployów; kroki tylko na gałęzi |
| Wzrost metryki `would_reject_reseq` (K14) > ustalonego progu | nie flipować bramki; diagnoza dlaczego sweep generuje niefeasible sekwencje (to samo w sobie finding) |
| Korpus K04 niekompletny (<95% decyzji z nagraniem) | wstrzymać K06+ do naprawy nagrywania — parytet bez korpusu = deklaracja, nie dowód |

**Zależności zewnętrzne planu:** baseline testów ruchomy (inne sesje) — przed każdym krokiem świeży bieg kanoniczny; kolejka flipów FLIPMASTERA (O2-K1/K3/K4b/K5) ma pierwszeństwo w oknach deploy — nasze restarty wpinamy między nie, za ACK.

**Rytm STOP-ów (do uzgodnienia):** proponuję STOP po każdym PAKIECIE (0-4) z diff-summary + wynikami testów; w środku pakietu commity idą na pod-gałęziach bez przerywania Ci dnia. Na Twoje żądanie — STOP po każdym kroku.

---
*Artefakt Fazy 4. Po Twojej akceptacji: start K01 (pakiet 0) — pierwsza zmiana w kodzie nastąpi dopiero wtedy.*
