# AUDYT 2.0 — SWEEP: EXECUTOR AUTONOMII (auto_assign_executor)
**Data:** 2026-07-01/02 · **Tryb:** READ-ONLY wobec produkcji (zero edycji kodu/flag/env, zero restartów, zero mutujących POST-ów) · **Lane:** audyt PRZED pierwszym `ENABLE_AUTO_ASSIGN=ON`

**Zakres:** `auto_assign_executor.py` (egzekutor) + `auto_assign_gate.py` (bramka) + `gastro_assign.py` (realny mechanizm) + wiring przycisk konsoli → `auto_assign_flag.py` → `flags_admin` → silnik + telemetria plastrów D/D' na żywo.

**Werdykt jednozdaniowy:** Warstwa bezpieczników wokół egzekutora (killswitch hot, bramka compute-zawsze, cooldown po PANEL_OVERRIDE realnie wpięty, propagacja blokady kuriera, single execution surface, fail-safe) jest **solidna i działa** — ale **realny mechanizm wykonania (`gastro_assign.py`) potrafi zgłosić FAŁSZYWY SUKCES** (exit 0 mimo nieudanego przypisania), a executor **przekazuje kuriera po NAZWIE** (re-resolucja name→cid) i **nie ma idempotencji per-zlecenie** — to trzy rzeczy do domknięcia PRZED pierwszym ON. Telemetria plastra D = **~35–50/dzień, NIE 125/dzień** (projekcja 125 niepotwierdzona, próbka cienka: 55 zdarzeń / 1,6 dnia).

---

## STAN NA ŻYWO (zweryfikowany)
- `flags.json`: `ENABLE_AUTO_ASSIGN = false` (obecny w pliku, kanoniczny). **Brak env-override** w `dispatch-shadow.service(.d)` dla tej flagi → `decision_flag` czyta wyłącznie flags.json → spójne.
- Profil bramki (default strict): `AUTO_ASSIGN_REQUIRE_CLASSIFIER_AUTO=True`, `AUTO_ASSIGN_REQUIRE_MARGIN=True`, `AUTO_ASSIGN_MIN_POOL_FEASIBLE=3`. `AUTO_ASSIGN_MAX_PER_HOUR=6`, `AUTO_ASSIGN_OVERRIDE_COOLDOWN_MIN=60`.
- `dispatch-shadow.service`: Type=simple, długodziałający (PID żywy) — jedyny proces z hookiem egzekutora.
- `auto_assign_state.json` (rate-cap): **nie istnieje** (executor nigdy nie wykonał — zgodne z „nigdy nie odpalony").
- Single execution surface **potwierdzony**: jedyny importer `auto_assign_executor` = `shadow_dispatcher.py:1328-1329`. `dispatch_pipeline` (czasówka/plan-recheck) liczy TYLKO telemetrię bramki, **nie woła** `maybe_execute`.

---

## TELEMETRIA PLASTRÓW D/D' — ORACLE na `scripts/logs/shadow_decisions.jsonl` (od 2026-06-30T10:13)
Skrypty: `scratchpad/oracle_dprime.py`, `oracle_repeat.py`, `oracle_time.py` (read-only, streaming).

| Metryka | Wartość |
|---|---|
| Rekordy po cutoff (30.06 10:13) | **448** (PROPOSE 430 / KOORD 18) |
| Mają pole `would_auto_assign_d` (≠None) | **430/448** (dokładnie te PROPOSE — pole liczone po klasyfikacji, KOORD go nie ma) |
| `would_auto_assign` STRICT = True | **1** (verdict PROPOSE) → **~0,6/dzień** |
| `would_auto_assign_d` = True (plaster D, pool≥2) | **55** (wszystkie PROPOSE) → **~34–50/dzień** |
| `would_auto_assign_dprime` = True (D', pool≥3) | **47** → **~29–34/dzień** |
| Re-decyzje tego samego order_id w D-true | **0** (55 unikalnych oid, 0 powtórek) |
| `target_pickup_at` obecny w `best` (D-true) | **55/55** (min czas do odbioru 15 min, mediana 29, max 60; **0** przypadków ≤0 min) |

Rozkład per-dzień D-true&PROPOSE: 30.06 = 12 (dzień częściowy od 10:13) · 01.07 = 43.

**Top powody blokady w D=False (grupowane):** `pos_not_informed` 229, `late_pickup_extension` 112, `scarcity_pool` 100, `late_pickup_redirect` 92, `score_distrust_ceiling` 91, `plan_sla_violations` 58, `best_effort` 36, `late_pickup_committed` 32, `pos_from_store` 28, `paczka_firmowe` 16, `shift_end_edge` 13, `new_courier_ramp` 12.

**Wniosek dot. „~125/dzień":** plaster D przepuszcza ~13% z ~270 propozycji/dzień = **~35/dzień** (D'), NIE 125. Projekcja 125/dzień jest **niepotwierdzona telemetrią** i ~3× zawyżona względem pomiaru; próbka (55 zdarzeń, 1,6 dnia) jest **za cienka** na decyzję o sizingu/rampie. Rozkład powodów blokady jest wewnętrznie spójny (13% pass-rate ≠ 46% wymagane dla 125). **Potrzeba ≥1 tydzień telemetrii + rekoncyliacja projekcji przed rampą.**

---

## FINDINGI

### 🔴 P0-1 — `gastro_assign.py` zgłasza FAŁSZYWY SUKCES (exit 0 mimo nieudanego przypisania)
**Gdzie:** `gastro_assign.py:206-209` + `auto_assign_executor.py:160-163` (`_default_assign_runner` ufa `returncode==0`).

Dwa mechanizmy fałszywego sukcesu:
1. **Gałąź „nieoczekiwana odpowiedź" nie robi `sys.exit(1)`.** `gastro_assign.py:208-209` drukuje `ASSIGN_ERROR: nieoczekiwana odpowiedź` ale **spada do końca `main()` → proces kończy się kodem 0**. (Kontrast: gałąź `except` na l.210-212 robi `sys.exit(1)` poprawnie.)
2. **Heurystyka sukcesu jest zbyt luźna.** `gastro_assign.py:206`: `result.get('success') or result.get('status')=='ok' or 'error' not in str(result).lower()`. Trzeci człon = „jeśli odpowiedź nie zawiera dosłownie słowa *error* → SUKCES". Gdy panel zwróci HTTP 200 z ciałem nie-JSON (strona logowania / komunikat sesji / redirect), `assign()` łapie `{'raw': <html>}` (l.114-117) i jeśli HTML nie ma słowa „error" → **ASSIGN_OK → exit 0**.

Executor (`_default_assign_runner`) na `returncode==0` zwraca `(True, stdout)` → `maybe_execute` zapisuje rate-cap, dopisuje `AUTO_ASSIGN_EXECUTED` do learning_log, wysyła Telegram **„✅ wykonane"** — **podczas gdy zlecenie NIE zostało przypisane**. Pod autonomią (bez człowieka weryfikującego wynik) = **cichy drop zlecenia**: system „wie" że rozdysponował, zlecenie leży.

**Uwaga o widoczności:** ta sama ścieżka (`returncode==0`) jest w `telegram_approver.run_gastro_assign:1987-1988` — ALE tam człowiek kliknął i widzi wynik. Autonomia usuwa tego człowieka → słabość staje się groźna. **NIE jest to więc ścieżka „przetestowana bojowo" dla trybu bez człowieka.**

*Rekomendacja:* executor musi wymagać jawnego sentinela `ASSIGN_OK:` w stdout dla `ok=True` (gastro już go drukuje), a nie samego exit-code; oraz `gastro_assign` powinien `sys.exit(1)` w gałęzi „nieoczekiwana odpowiedź". Zweryfikować E2E na kontrolowanym zleceniu, że panel FAKTYCZNIE odzwierciedla przypisanie.

### 🔴 P1-1 — realny assign NIGDY nie przeszedł E2E; executor przekazuje kuriera po NAZWIE (re-resolucja name→cid)
**Gdzie:** `auto_assign_executor.py:233,254` (`name = best.get("name")`, `runner(oid, str(name), time)`) → `gastro_assign.get_kurier_id:52-86`.

Executor ma `cid` (`best["courier_id"]`), ale do gastro przekazuje **NAZWĘ**; `gastro_assign` re-rozwiązuje name→cid z `kurier_ids.json` heurystyką „pierwsze słowo + inicjał nazwiska", a przy niejednoznaczności **„używam pierwszego"** (l.82-83). Round-trip cid→name→cid może trafić w **innego kuriera**. Docstring egzekutora sam przyznaje: „Realny assign NIGDY nie przeszedł E2E (matchowanie nazwy kuriera w panelu gastro)". Pod autonomią błędny match = przypisanie **nie temu człowiekowi**.

*Rekomendacja:* przed ON zweryfikować, że name→cid round-trip zgadza się dla WSZYSTKICH kurierów plastra D; docelowo przekazywać cid, nie nazwę.

### 🟠 P1-2 — brak idempotencji per-zlecenie (ryzyko podwójnego przypisania pod LIVE)
**Gdzie:** `auto_assign_executor.py` — jedyne bezpieczniki to rate-cap GLOBALNY (6/h) + cooldown per-kurier po PANEL_OVERRIDE. **Zero guardu per-order.**

- Reconcile lag panelu jest udokumentowany na **15–90 s** (CLAUDE.md V3.14/V3.15). Między auto-assignem a odzwierciedleniem w `orders_state` to samo, wciąż-„nieprzypisane" zlecenie może dostać kolejny event (np. ORDER_UPDATED z diff-a panelu, inny `event_id` → dedup event_bus nie chroni) → nowa decyzja PROPOSE → **drugi assign**. Rate-cap 6/h nie blokuje 2. strzału tego samego oid.
- Wariant crash: hook `maybe_execute` biegnie PRZED `event_bus.mark_processed(eid)` (`shadow_dispatcher.py:1329` vs `1335`). Crash między sukcesem gastro a `mark_processed` → event zostaje `pending` → po restarcie re-processing → **re-fire**.
- Empirycznie: **0 powtórek** w oknie shadow (55 unikalnych oid). ALE w shadow to KOORDYNATOR zdejmuje zlecenie z puli szybko; pod LIVE zdejmuje je dopiero własny assign egzekutora + reconcile lag — dynamika inna, więc 0-powtórek z shadow **nie przenosi się 1:1**.

*Rekomendacja:* dodać guard per-order „ostatnio auto-przypisane" (krótki TTL set / skan `AUTO_ASSIGN_EXECUTED` w learning_log) przed strzałem — ZANIM flip profilu D.

### 🟠 P1-3 — projekcja „~125/dzień" niepotwierdzona; próbka za cienka na decyzję
Patrz sekcja TELEMETRIA. Pomiar: **~35–50/dzień** (D'≈29–34/dzień), 55 zdarzeń / 1,6 dnia. Decyzji o sizingu/rampie **nie da się** oprzeć na tych danych; dodatkowo realny wolumen jest ~3× niższy niż zakładane 125. *Rekomendacja:* ≥7 dni telemetrii + rekoncyliacja skąd wzięło się 125, przed jakąkolwiek rampą.

### 🟡 P2-1 — podwójna prezentacja koordynatorowi (auto-assign + wciąż-widoczna propozycja)
Zapis do `pending_proposals` (`shadow_dispatcher.py:1320`) dzieje się PRZED hookiem egzekutora (l.1327), a rekord ma verdict=PROPOSE → propozycja trafia też normalną ścieżką (konsola + Telegram). Po auto-assignie koordynator nadal widzi propozycję dla **już-auto-przypisanego** zlecenia → może ją nadpisać (→ PANEL_OVERRIDE) albo się pogubić. Brak wygaszenia/oznaczenia propozycji przy auto-egzekucji. *Rekomendacja:* przy auto-egzekucji oznacz/wygaś wpis w `pending_proposals` i propozycję Telegram.

### 🟡 P2-2 — stan połowiczny „przypisane-ale-zgłoszone-jako-nieudane" (brak rollbacku/rekoncyliacji)
Jeśli POST `przypisz-zamowienie` dotrze do panelu, ale odpowiedź się urwie (urlopen timeout=10s w `assign()` / subprocess timeout=30s w runnerze) → executor `ok=False` (bez zapisu stanu, notify „❌"), a zlecenie JEST przypisane. Brak akcji kompensującej. Bezpieczniejsze niż podwójne przypisanie, ale monitoring pokazuje porażkę dla sukcesu. *Rekomendacja:* po `ok=False` z timeoutu — lekki recheck statusu zlecenia zamiast ślepej „porażki".

### 🟡 P2-3 — `time_minutes=0` = latentna mina (panel „clears UI" na czasie odbioru)
`_time_minutes_from_record` (`auto_assign_executor.py:188-200`) zwraca **0** gdy brak `target_pickup_at` lub gdy cel jest w PRZESZŁOŚCI (`max(0, ...)`). `gastro_assign` z `--time 0` = wysyła „0", co per dokumentacja panelu (CLAUDE.md) **czyści czas odbioru w UI**. Empirycznie **0/55** w plastrze D (wszystkie mają target, min 15 min do przodu) → **obecnie nie trafia**. ALE ścieżka kodu to latentne ryzyko korupcji danych, jeśli zmieni się kompozycja bramki lub serializacja `best`. *Rekomendacja:* podłoga (nigdy nie wysyłać 0 — clamp do min 1 min albo blokada gdy target brak/przeszły).

### 🟡 P2-4 — słaby trwały ślad auditowy egzekucji (false-success niewidoczny w learning_log)
Rekord `AUTO_ASSIGN_EXECUTED` (`auto_assign_executor.py:267-275`) zapisuje `ts/oid/action/cid/name/time_minutes/score` — **BEZ `runner_msg`/detalu wyniku**. W scenariuszu P0-1 (stdout zawiera „ASSIGN_ERROR" ale `ok=True`) learning_log powie „EXECUTED", Telegram powie „✅", a jedyne miejsce z `runner_msg` to linia journala shadow (`_log.info(... outcome=...)`). Post-hoc audyt JAKOŚCI (czy realnie się udało) jest więc słaby. *Rekomendacja:* dopisać `runner_msg`/`ok` do rekordu learning_log.

### 🔵 P3-1 — parametr `payload` przyjmowany, nieużywany
`maybe_execute(record, result, payload=None, ...)` — `payload` (order_event) nigdy nie użyty w ciele. Executor NIE re-waliduje `order_event` (EXCLUDED/manual_overrides/status późny) w chwili wykonania; ufa rekordowi. Decyzja→wykonanie to ten sam tick (~ms), więc luka realnie znikoma, ale martwy parametr myli. 

### 🔵 P3-2 — rate-cap globalny (6/h), nie per-kurier/per-restauracja
Przy ~35–50/dzień skoncentrowanych w peaku (~7/h) globalny cap 6/h **zdławi legalne przypisania w peaku** (nie jest to niebezpieczne, ale wygląda jak „autonomia stanęła" i zmniejsza przepustowość). Rozważyć cap świadomie pod docelowy wolumen.

### 🟢 P3-3 (POZYTYW) — killswitch jest realnie HOT i kanoniczny
`C.decision_flag("ENABLE_AUTO_ASSIGN")` (`common.py:357-370`) → `load_flags()` z hot-reload po mtime; sprawdzane **per-event** na 1. linii `maybe_execute` (OFF = `return None`, zero I/O). Zapis przez `flags_admin` atomowy (temp+rename) → mtime rośnie → następny tick widzi OFF. **Nie da się przerwać jednego assignu w locie** (subprocess ≤30 s) — po OFF max jeden assign dokończy. Akceptowalne.

---

## WIRING: przycisk konsoli → most → flags_admin → silnik
**Łańcuch (zweryfikowany po kodzie):**
1. `POST /api/coordinator/auto-assign` (`coordinator.py:795-808`) — body `{enabled: bool}`.
2. Auth: `Depends(_OperatorOnly)` = `require_roles("ziomek_admin")` → **401** brak tokenu / **403** zła rola (`deps.py:54,71`); `_gate()` → **404** gdy flaga `COORDINATOR_CONSOLE` OFF (`coordinator.py:56-58`). (Uwaga: opis „401-gated" jest nieprecyzyjny — to 401/403/404.)
3. `auto_assign_flag.set_state(enabled, actor=user.email)` (`auto_assign_flag.py:63-92`) — subprocess w venv Ziomka → `python -m dispatch_v2.flags_admin set ENABLE_AUTO_ASSIGN true|false`.
4. `flags_admin.cmd_set` (`flags_admin.py:186-194`) → `core.flags_io.update_flag` (atomic) + `_broadcast_flags_reload` (event_bus CONFIG_RELOAD, best-effort). **Panel NIGDY nie pisze flags.json bezpośrednio** (Z3: jedno źródło zapisu).
5. Silnik: `dispatch-shadow` czyta flags.json hot-reload → następny event widzi nową wartość.

**Gdzie mieszka stan:** wyłącznie `flags.json` (kanon). Brak env-override dla tej flagi → brak drift-u konsola↔proces. **Audyt:** każdy toggle → `audit_event({kind:auto_assign_toggle, actor, requested, ok, rc, value})` (`auto_assign_flag.py:79-80,90-91`). Fail-soft: błąd zapisu → 502 + stan czytany ponownie.

**⚠ DWA NIEZALEŻNE „włączniki" — kluczowe dla pierwszego ON:**
- **Przycisk konsoli** przełącza TYLKO `ENABLE_AUTO_ASSIGN` (executor wł/wył).
- **Który plaster** decyduje profil (`AUTO_ASSIGN_REQUIRE_CLASSIFIER_AUTO`/`_REQUIRE_MARGIN`/`_MIN_POOL_FEASIBLE`) — flipowany OSOBNO w flags.json, **NIE przyciskiem**.
- Executor czyta wyłącznie STRICT `result.would_auto_assign` (`executor:222`). Przy domyślnym profilu strict = **~0,6/dzień** (1 zdarzenie / 1,6 dnia!). Żeby autonomia realnie działała na plastrze D (~35/dzień), trzeba DODATKOWO flipnąć 3 flagi profilu (`common.py:861` „Flip = 3 flagi RAZEM").
- **Konsekwencja dla bezpieczeństwa:** pierwsze wciśnięcie „Włącz" przy DOMYŚLNYM profilu = niemal nic się nie dzieje (strict prawie pusty) — to najbezpieczniejszy możliwy pierwszy ON. Groźniejszy jest flip profilu D (osobna, świadoma czynność).

**Co widzi koordynator po włączeniu:** badge z `GET /auto-assign` → „autonomia WŁ". Po każdym auto-assignie: Telegram admin-alert „🤖 AUTO-ASSIGN ✅/❌ #oid → name". Zlecenie nadal pojawia się jako propozycja w konsoli/Telegramie (P2-1). Może nadpisać auto-assign ręcznie (→ PANEL_OVERRIDE) — samokorekta, ale auto-assign nie jest „ostateczny" z perspektywy koordynatora.

---

## INTERAKCJA Z EXCLUDED_CIDS / manual_overrides (zweryfikowana)
- **EXCLUDED_CIDS to pojęcie KSIĘGOWE** (`daily_accounting/config.py`), NIE dispatch — nie ma wpływu na feasibility. (Korekta ewentualnego nieporozumienia.)
- Realne wykluczenie z dispatchu = **`manual_overrides`** (`courier_resolver.py:1436-1446`: `get_excluded()`/`get_excluded_cids()`/`get_working()`), stosowane przy budowie snapshotu floty.
- **Blokada kuriera z konsoli** (`courier_block.py`) pisze przez subprocess w venv Ziomka do KANONICZNEGO `dispatch_v2.manual_overrides` → `dispatch_state/manual_overrides.json` (`excluded`/`working`), czyli **tego samego pliku, który czyta `courier_resolver`**. Zablokowany kurier nie wejdzie do puli feasible → nie będzie `best` → **nie zostanie auto-przypisany**. Propagacja: od następnej decyzji po zapisie pliku (sub-sekundy). Executor introdukuje ZERO nowego bypassu wykluczeń — działa na tej samej decyzji co propozycje.
- **cooldown po PANEL_OVERRIDE — REALNIE WPIĘTY:** `panel_watcher.py:245-258` pisze `action:PANEL_OVERRIDE` (z `proposed_courier_id`/`actual_courier_id`/`ts`) do `learning_log.jsonl`; `_recent_override_for_courier` (`executor:102-147`) skanuje ten sam plik po tym samym schemacie. Zweryfikowane: 48 świeżych rekordów PANEL_OVERRIDE, format zgadza się ze skanerem.

---

## TRACE E2E NA SUCHO

### HAPPY PATH (profil D flipnięty, ENABLE_AUTO_ASSIGN=true, kurier informed, pool≥2, nie-late, score≤90)
1. `panel_watcher` emituje event → event_bus.
2. `shadow_dispatcher.process_event` → `assess_order` → PipelineResult: verdict=PROPOSE, best=X (cid, name, target_pickup_at, score).
3. `_classify_and_set_auto_route` → klasyfikator; `evaluate_auto_assign(result, order_event, INFORMED_POS_SOURCES, flags=load_flags())` z profilem D → `would_auto_assign=True`, `auto_block_reasons=[]`; liczone też D/D'.
4. Rekord serializowany (would_auto_assign=true, D/D', block_reasons) → `_append_decision` → **shadow_decisions.jsonl** (post-hoc auditowalne).
5. `pending_proposals` upsert (l.1320) — propozycja też do koordynatora [P2-1].
6. Hook `maybe_execute(record, result, payload)`:
   a. `decision_flag(ENABLE_AUTO_ASSIGN)`=true → dalej. b. `would_auto_assign`=True. c. verdict==PROPOSE. d. oid/cid/name obecne.
   e. rate-cap: <6 w 3600 s → OK. f. cooldown: brak PANEL_OVERRIDE(cid) w 60 min → OK.
   g. `time_minutes`=round(target−now) np. 25. h. `_default_assign_runner` → subprocess `gastro_assign --id oid --kurier name --time 25` → login+CSRF, name→cid', POST przypisz → returncode 0 + „ASSIGN_OK:" → (True, stdout).
   i. `ok=True` → state.executed.append + `_save_state` (atomic) + `_append_learning_log(AUTO_ASSIGN_EXECUTED)` + log.info.
   j. notify Telegram „✅ wykonane". k. return outcome.
7. shadow: log „AUTO_ASSIGN outcome=..." → `event_bus.mark_processed(eid)`.
**Wynik:** zlecenie przypisane w panelu; Adrian dostaje Telegram; propozycja nadal w konsoli.

### FAILURE PATHS
- **F1 (panel 419 / błąd HTTP w połowie):** `assign()` `urlopen` **rzuca HTTPError na 4xx/5xx** → propaguje do `main` (l.210) → `sys.exit(1)` → executor `ok=False`, brak stanu, notify „❌". Panel prawdopodobnie NIE przypisał (POST odrzucony). **CZYSTO.** ⚠ Groźny jest wariant **HTTP 200 z ciałem błędu/logowania** (nie 4xx): `urlopen` nie rzuca → `{'raw':html}` → heurystyka może dać false-success (**P0-1**).
- **F2 (timeout):** POST wisi >10 s → `urlopen` rzuca → `main` exit 1 → `ok=False`. LUB subprocess >30 s → `TimeoutExpired` → (False,„timeout_30s"). POST mógł dotrzeć do panelu → **stan połowiczny** (przypisane-ale-zgłoszone-jako-nieudane, **P2-2**). Brak rollbacku.
- **F3 (zlecenie zniknęło — koordynator wziął / anulowane, między decyzją a wykonaniem):** executor NIE re-sprawdza statusu; POST przypisz dla już-przypisanego → panel może **przepisać zlecenie nadpisując koordynatora** (RACE, **P1-2**), albo odrzucić (→ możliwy false-success F1). Cooldown ochroni TYLKO jeśli PANEL_OVERRIDE zdążył zostać zapisany (lag panel_watchera) — w oknie 10–60 s może nie zdążyć.
- **F4 (kurier zniknął — offline/zablokowany po decyzji):** blokada z konsoli aktualizuje manual_overrides, ale decyzja policzona starym snapshotem → executor i tak przypisze (panel nie zna manual_overrides). Okno małe; następna decyzja już wyklucza. **P2.** Jeśli kurier zszedł ze zmiany po decyzji (G13 shift_end_edge nie złapał w chwili decyzji) → przypisanie schodzącemu. Okno małe.
- **F5 (podwójny tick — to samo zlecenie, dwa eventy, wciąż nieprzypisane):** Event A → decyzja → assign OK → state+llog+mark_processed(A). Reconcile lag 15–90 s: `orders_state` wciąż „nieprzypisane". Event B (inny event_id) tego samego oid → decyzja PROPOSE → **drugi assign** (rate-cap 6/h nie blokuje 2. strzału; brak guardu per-order) → **PODWÓJNE PRZYPISANIE** / przesunięcie czasu odbioru (re-send time=minutes). Wariant crash między assign a mark_processed → re-processing → re-fire. Empirycznie 0 powtórek w shadow, ale dynamika LIVE inna (**P1-2**).

---

## LISTA WARUNKÓW KONIECZNYCH PRZED 1. ON (checklist)
- [ ] **(P0-1) Naprawić detekcję sukcesu** — executor wymaga sentinela `ASSIGN_OK:` w stdout (nie samego exit-code); `gastro_assign` `sys.exit(1)` w gałęzi „nieoczekiwana odpowiedź" + zaostrzyć heurystykę (usunąć człon `'error' not in ...`). BEZ TEGO żaden realny ON.
- [ ] **(P1-1) Zwalidować name→cid E2E** dla wszystkich kurierów plastra D (executor przekazuje nazwę; gastro re-rozwiązuje) — potwierdzić brak niejednoznaczności/pomyłki; docelowo przekazywać cid.
- [ ] **PIERWSZY ON = tylko przycisk, profil STRICT (bez flipu 3 flag)** → plaster strict ≈ 0–1/dzień → zaobserwować JEDEN realny assign pod nadzorem, off-peak, potwierdzić że panel FAKTYCZNIE go odzwierciedla.
- [ ] **`AUTO_ASSIGN_MAX_PER_HOUR=1`** na pierwsze kontrolowane wykonanie (pojedynczy assign, nadzorowany).
- [ ] **(P1-2) Dodać guard idempotencji per-order** ZANIM flip profilu D (żeby reconcile-lag re-fire pod LIVE nie dublował przypisań).
- [ ] **Monitor + stop-loss PRZED ON** — breach-rate auto-assignów, detektor false-success (skan stdout „ASSIGN_ERROR" mimo EXECUTED), rate „override-po-auto". Obecnie taki monitor (odpowiednik carried_first_guard dla auto-assign) NIE istnieje.
- [ ] **(P2-1) Wygasić/oznaczyć propozycję** (pending_proposals + Telegram) przy auto-egzekucji — koniec podwójnej prezentacji.
- [ ] **(P1-3) Re-mierzyć telemetrię ≥7 dni** + rekoncyliacja „125 vs zmierzone ~35–50/dzień" przed sizingiem/rampą.
- [ ] **(P2-3) Podłoga `time_minutes`** — nigdy 0 (clamp≥1 lub blokada gdy target brak/przeszły).
- [ ] **Przećwiczyć rollback** — wciśnij „Wyłącz", potwierdź `ENABLE_AUTO_ASSIGN=false` w flags.json w ≤1 tick; backup `.bak-pre-auton02-20260630-093549` gotowy.
- [ ] **(P2-4) Dopisać `runner_msg`/`ok` do rekordu learning_log** — trwały ślad jakości egzekucji.

---

## POKRYCIE
- **15 klas przejrzanych:** rate-cap ✔ (`executor:93-97,241-243`) · override-guard/cooldown ✔ (`102-147,246-249` — realnie wpięty via panel_watcher) · idempotencja ✔ (**LUKA P1-2**, brak per-order) · porażka gastro w połowie ✔ (F1/F2, stan połowiczny P2-2, brak rollbacku) · kill-switch ✔ (hot, per-event, flags.json, bez env-drift) · logging/serializacja ✔ (shadow_decisions pełne; learning_log ubogie P2-4) · wyścig z koordynatorem ✔ (F3/P1-2, cooldown częściowy) · EXCLUDED_CIDS/manual_overrides ✔ (propagacja blokady OK) · wiring przycisk→most→flags_admin→silnik ✔ · auth endpointu ✔ (401/403/404 + audit) · co widzi koordynator ✔ · telemetria D/D' ✔ (oracle, 3 skrypty) · trace happy-path ✔ · 5 failure-paths ✔ · single execution surface ✔.
- **Bliźniacza ścieżka:** `telegram_approver.run_gastro_assign` — potwierdzony parytet słabości exit-code (P0-1 dotyczy obu; różnica = człowiek w pętli tylko w Telegramie).
- **Dowody:** oracle na `scripts/logs/shadow_decisions.jsonl` (448 rek. po cutoff), grep-em potwierdzone: writer PANEL_OVERRIDE, propagacja courier_block→manual_overrides, brak env-override, jedyny importer egzekutora, brak `auto_assign_state.json`.

## JAWNE LUKI
- **Nie wykonano żadnego realnego `gastro_assign`** (tryb read-only, zakaz mutujących POST) — E2E realnego przypisania i weryfikacja name→cid na żywym panelu pozostają NIEZROBIONE (to zadanie kontrolowanego 1. ON z Adrianem, nie audytu).
- **Nie sprawdzono, czy `panel_watcher` re-emituje eventy dla wciąż-nieprzypisanego zlecenia** (kluczowe dla realnego prawdopodobieństwa F5/P1-2) — 0 powtórek w shadow jest sugestią, nie dowodem pod LIVE.
- **Nie zmierzono realnego rozkładu w peaku** (czy ~7/h przekracza cap 6/h) — próbka 1,6 dnia za krótka.
- **`core/flags_io.update_flag` przeczytany tylko pośrednio** (przez flags_admin) — nie zweryfikowałem atomu zapisu w samym flags_io liniowo (deklarowany atomic; do potwierdzenia przy okazji).
- **Reconcile lag 15–90 s** wzięty z CLAUDE.md (historyczny) — nie re-zmierzony na bieżąco.
