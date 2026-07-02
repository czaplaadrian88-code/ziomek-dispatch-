# AUDYT 2.0 — L05: BRAMKI WYKLUCZEŃ + AUTONOMIA
**Pas 0.E** · `manual_overrides.py` + `auto_koord.py` + `auto_assign_executor.py` (+`auto_assign_gate.py`, `gastro_assign.py`)
**Data:** 2026-07-02 · **Tryb:** READ-ONLY na produkcji (zero edycji kodu/flag/env, zero restartów, zero mutujących POST-ów). Jedyny zapis = ten plik.
**Backing (moja lane pokryta 2 wcześniejszymi sweepami — TU KONSOLIDACJA + niezależna weryfikacja + unikalne dowody):** `AUDYT2/SWEEP_auto_assign_executor.md`, `AUDYT2/SWEEP_pool_gates.md`.

---

## WERDYKT (1 zdanie)
Warstwa bezpieczników autonomii jest **solidna i realnie inertna** (killswitch hot flags.json-only, executor nigdy nie odpalony, reset dzienny ZDROWY — historyczny RC3 „martwy 4 dni" NAPRAWIONY), ALE **realny mechanizm wykonania `gastro_assign.py` zgłasza FAŁSZYWY SUKCES na exit-code** (potwierdzone dowodem NA ŻYWO), pierwszy flip **NIE jest no-opem** (strict `would_auto_assign=true` istnieje w ledgerze), a sklep wykluczeń nie ma locka ani alertu na pustą pulę — 3 rzeczy do domknięcia przed 1. ON.

## STAN NA ŻYWO (zweryfikowany 01/02.07)
| Element | Stan | Dowód |
|---|---|---|
| `ENABLE_AUTO_ASSIGN` | **false** (executor inert) | `flags.json:177`; **brak env-override** w `dispatch-shadow` (`systemctl show -p Environment` puste dla AUTO_ASSIGN) → killswitch = wyłącznie flags.json |
| Executor kiedykolwiek odpalony? | **NIE** | `auto_assign_state.json` nie istnieje; `learning_log.jsonl` `AUTO_ASSIGN_EXECUTED` = **0** |
| Sole execution surface | **potwierdzony** | jedyny importer `maybe_execute` = `shadow_dispatcher.py:1328-1329`; `dispatch_pipeline` (czasówka/plan-recheck) liczy TYLKO telemetrię bramki (`:2914-2943`), NIE woła egzekutora |
| Executor host żywy | active PID 1063748 | `systemctl is-active dispatch-shadow` |
| `AUTO_KOORD_ON_NEW_ORDER_ENABLED` | **true — LIVE** (wbrew docstringowi „default False") | `flags.json:23`; `auto_koord_log.jsonl` 1065 wpisów od 2026-05-05, ostatni 01.07T17:26 |
| Reset dzienny (RC3) | **ZDROWY** | `dispatch-overrides-reset.timer` active+enabled, `OnCalendar=06:00 Europe/Warsaw`, `Persistent=true`; ostatni przebieg exit **0/SUCCESS** 18h temu; live store pusty |
| Bramka strict profil | REQUIRE_CLASSIFIER_AUTO / REQUIRE_MARGIN = True, MIN_POOL=3, MAX_PER_HOUR=6, COOLDOWN=60 min | `common.py:905-921` |

---

## POMIAR PIERWSZEGO FLIPU (oracle `scripts/logs/shadow_decisions.jsonl`, okno 27.06→01.07, 1210 rek.)
| Metryka | Wartość |
|---|---|
| `would_auto_assign` STRICT = true | **10** (wszystkie verdict=PROPOSE) — okno 4,5 dnia ≈ **~2/dzień** (mieszany gate: pre/post-AUTON-02 30.06; post-30.06 gate cieńszy — patrz F2) |
| `would_auto_assign_d` (plaster D, pool≥2) = true | **55** |
| `would_auto_assign_dprime` (pool≥3) = true | **47** |
| 10× STRICT-true — jakość | wszystkie: verdict=PROPOSE, `ctx.pos_source_best=gps`, pool_feasible 8, tier=gold/std, `target_pickup_at` obecny (0/10 brak → 0 przypadków time=0). Kurierzy realni: Bartek Ołdziej ×6 (cid 123), Piotr Kulaszewski ×2 (531), Grzegorz Wysocki (289), Jakub Wysocki (492) |

**Wniosek:** pierwszy flip przy DOMYŚLNYM (strict) profilu **odpali realny `gastro_assign` na ~1-2 zleceniach/dzień** (nie 0), na najbezpieczniejszym możliwym slice (gold/gps/pool≥3), ale **ścieżka executor→panel NIGDY nie przeszła E2E**. To potwierdza ostrzeżenie z memory „1. wciśnięcie Włącz = NIEPRZETESTOWANE E2E" — i zarazem **koryguje** notatkę „would_auto=0/dzień" (ledger pokazuje realne STRICT-true, ostatnie 01.07 oid 484669).

---

## FINDINGI (najgroźniejsze pierwsze)

### P1 — `gastro_assign.py` zgłasza FAŁSZYWY SUKCES (exit 0 mimo nieudanego przypisania) — DOWÓD NA ŻYWO
**Surface:** `gastro_assign.py:206-209` + `auto_assign_executor.py:160-163` (`_default_assign_runner` ufa wyłącznie `returncode==0`) + `auto_koord.py:147-154` (ta sama ścieżka, LIVE).
**Dowód (ground-truth, nie hipoteza):** `auto_koord_log.jsonl` — realne wpisy pokazują `Odpowiedź panelu: {'raw': ''}` (PUSTE ciało) → `gastro_assign` drukuje `ASSIGN_OK`. Niezależna repro logiki `:206`: `{'raw':''}` → `'error' not in "{'raw': ''}"` = True → ASSIGN_OK exit 0; a także `{'raw':'<html>Sesja wygasła…</html>'}` (brak słowa „error") → **ASSIGN_OK exit 0 = FAŁSZYWY SUKCES**. Dodatkowo gałąź „nieoczekiwana odpowiedź" (`:208-209`) NIE robi `sys.exit(1)` → proces kończy 0.
**Materialność:** Dla auto_koord (LIVE) benigne — puste ciało JEST sukcesem panelu (1057 realnych parkowań u Koordynatora), ale kontrakt **nie odróżnia** pustki-na-sukcesie od pustki/HTML-na-błędzie → latentny **cichy drop czasówki** (nie trafi do id_kurier=26 → czasowka_scheduler jej nie obudzi). Dla executora PO FLIPIE: `ok=True` → rate-cap zapisany, `AUTO_ASSIGN_EXECUTED` do learning_log, Telegram „✅ wykonane" — **przy niewykonanym przypisaniu = cichy drop zlecenia bez człowieka w pętli**. Nie policzone ile/dzień (zależy od częstości 200-z-błędem panelu); istnienie ścieżki + dowód pustego ciała = pewne.
**Rekomendacja:** executor musi wymagać jawnego sentinela `ASSIGN_OK:` w stdout (gastro już drukuje), nie samego exit-code; `gastro_assign` `sys.exit(1)` w gałęzi „nieoczekiwana odpowiedź" + usunąć człon `'error' not in ...`. Zweryfikować E2E na kontrolowanym zleceniu że panel FAKTYCZNIE odzwierciedla przypisanie. **BEZ TEGO żaden realny ON.**

### P1 — pierwszy flip nie jest no-opem; executor przekazuje kuriera po NAZWIE, E2E nigdy nie przeszło
**Surface:** `auto_assign_executor.py:222` (`getattr(result,"would_auto_assign")`), `:233,254` (`name=best.get("name")` → runner) → `gastro_assign.get_kurier_id:52-86`.
**Dowód:** 10 STRICT-true w ledgerze (wyżej). Executor przekazuje NAZWĘ (ma cid w `best["courier_id"]`, nie używa go); gastro re-rozwiązuje name→cid heurystyką „pierwsze słowo+inicjał", przy niejednoznaczności **„używam pierwszego"** (`:82-83`). **Mitygacja obecna (moja weryfikacja):** wszystkie 49 nazw z `grafik_full_names.json` EXACT-matchują klucz w `kurier_ids.json` (109 kluczy = superset skrót+pełne imię) → round-trip dziś trafia. **Residual:** kurier na zmianie, którego pełnego imienia autopair jeszcze nie wpisał do kurier_ids → fallback fuzzy → możliwa pomyłka/niejednoznaczność (landmine „Jakub OL/W/L").
**Materialność:** ~1-2 realne assigny/dzień na pierwszym ON (strict); każdy = pierwsza w historii egzekucja ścieżki nietestowanej E2E. Nie policzone ile trafi w złego kuriera (dziś 0 wg superset, ale data-dependent).
**Rekomendacja:** przed ON zwalidować name→cid round-trip dla wszystkich kurierów slice; docelowo przekazywać cid, nie nazwę. Pierwszy ON: profil STRICT + `AUTO_ASSIGN_MAX_PER_HOUR=1` + nadzór off-peak.

### P1 — brak idempotencji per-zlecenie (podwójne przypisanie pod LIVE)
**Surface:** `auto_assign_executor.py` — bezpieczniki tylko GLOBALNE (rate-cap 6/h `:93-97`) + per-kurier cooldown (`:102-147`). **Zero guardu per-order.** Hook `maybe_execute` (`shadow_dispatcher.py:1329`) biegnie PRZED `event_bus.mark_processed(eid)` (`:1335`).
**Dowód/materialność:** reconcile-lag panelu 15-90 s (CLAUDE.md V3.14/V3.15) → to samo, wciąż-„nieprzypisane" zlecenie może dostać 2. event (inny event_id → dedup event_bus nie chroni) → 2. PROPOSE → **drugi assign** (rate-cap nie blokuje 2. strzału tego samego oid). Crash między gastro-OK a mark_processed → re-processing → re-fire. Empirycznie 0 powtórek w shadow (55 unikalnych oid), ALE w shadow to Koordynator szybko zdejmuje zlecenie; pod LIVE zdejmuje je dopiero własny assign + reconcile-lag → dynamika inna, 0-z-shadow nie przenosi się 1:1. Nie policzone /dzień.
**Rekomendacja:** guard per-order „ostatnio auto-przypisane" (krótki TTL set / skan `AUTO_ASSIGN_EXECUTED`) ZANIM flip profilu D.

### P2 — sklep wykluczeń bez locka: 3 żywych pisarzy → lost-update (klasa O)
**Surface:** `manual_overrides.py:59-67` (`save()` = temp+fsync+replace, **ZERO flock/fcntl** — potwierdzone grepem). Pisarze: Telegram (`telegram_approver.py:2570/2652/3011`), konsola (`courier_block.py` subprocess), reset (`manual_overrides_daily_reset.py`).
**Materialność:** klasyczny read-modify-write bez locka: A czyta {X}, B czyta {X}, A→{X,Y}, B→{} — ostatni wygrywa, wykluczenie ginie. Ta sama sygnatura co `pending_proposals.json 3-writer` z design-doc. Realne prawdopodobieństwo niskie (operator+konsola rzadko równocześnie), nie policzone.
**Rekomendacja:** `flock LOCK_EX` obejmujący cały read-modify-write (albo jedna kolejka zapisu).

### P2 — ciche usunięcie z puli: fail-OPEN bez alertu + BRAK KANDYDATÓW alertowane TYLKO dla czasówek
**Surface:** `manual_overrides.load():38-56` + `courier_resolver.py:1445-1449` (fail → `excluded=set()`, tylko `_log.warning`). Egzekucja wykluczenia `courier_resolver.py:1504-1512`; rejekcje idą do `_rejected_for_log` (TASK 3 observability, flag-gated `:1616`), **nie alertowane**. Alert „🚨 BRAK KANDYDATÓW" istnieje wyłącznie w `czasowka_scheduler.py:505` (czasówki).
**Materialność (odpowiedź na pytanie lane „widoczność"):** (1) uszkodzony store → WSZYSTKIE `/stop` znikają cicho → Ziomek proponuje ściągniętych kurierów (świadomy fail-open, ale bez sygnału operacyjnego). (2) Poprawne wykluczenie kurczące pulę zwykłych zleceń NIE ma proaktywnego alertu — objawia się dopiero werdyktem KOORD/hold; operator ma tylko potwierdzenie „🛑 STOP" w chwili wpisu, brak przeglądu „kto aktualnie excluded" ani sygnału „pula płytka". Nie policzone.
**Rekomendacja:** na TYM pliku (sklep bezpieczeństwa) fail-open → LOW-alert (wzór koord-cascade), nie sam warning; rozważyć sygnał „pool-health" gdy excluded kurczy feasible poniżej progu.

### P2 — podwójna prezentacja koordynatorowi (auto-assign + wciąż-widoczna propozycja)
**Surface:** `shadow_dispatcher.py:1320` (`pending_proposals` upsert, verdict=PROPOSE) PRZED hookiem `:1327`. Po auto-assignie propozycja nadal idzie normalną ścieżką (konsola+Telegram) dla już-auto-przypisanego zlecenia.
**Materialność:** koordynator może nadpisać auto-assign (→ PANEL_OVERRIDE) albo się pogubić; brak wygaszenia/oznaczenia. Dotyczy tylko trybu ON. Nie policzone.
**Rekomendacja:** przy auto-egzekucji oznacz/wygaś wpis w `pending_proposals` + propozycję Telegram.

### P2 — auto_koord LIVE wbrew docstringowi + współdzieli luźny kontrakt (latentny cichy drop czasówki)
**Surface:** `auto_koord.py:16-18` docstring „default False" vs `flags.json:23` True; wykonanie `perform_auto_koord:143-171` przez ten sam `gastro_assign` (P1 wyżej).
**Materialność:** dziś zdrowe (1057 ok, 8 „failów" = w rzeczywistości poprawne race-avoids, patrz niżej), always-propose + czasowka_scheduler łapią pominięte. Ryzyko = genuine-błąd-panelu-jako-sukces → czasówka nie trafia do id_kurier=26 → nie obudzona w T-60. Klasa niska (redundancja scheduler), ale niezerowa.
**Rekomendacja:** zaktualizować docstring do „LIVE"; sentinel-sukcesu (wspólny fix z P1) domyka też auto_koord.

### P2 — `time_minutes=0` = latentna korupcja czasu odbioru (panel „clears UI")
**Surface:** `auto_assign_executor.py:188-200` (`_time_minutes_from_record` → 0 gdy brak `target_pickup_at` lub cel w przeszłości) → `gastro_assign --time 0` (per CLAUDE.md czyści czas odbioru w UI).
**Materialność:** empirycznie **0/10** w strict-true (wszystkie mają target ≥15 min do przodu) → dziś nie trafia; latentne jeśli zmieni się kompozycja bramki/serializacja. Tylko tryb ON.
**Rekomendacja:** podłoga — nigdy nie wysyłać 0 (clamp≥1 albo blokada gdy target brak/przeszły).

### P3 — override lifecycle na JEDNYM cronie (SPOF), brak per-wpis TTL / drugiego bezpiecznika
**Surface:** `manual_overrides.py` (brak TTL per-wpis) — lifecycle „do końca dnia" egzekwuje wyłącznie `dispatch-overrides-reset` 06:00.
**Materialność:** dziś zdrowe (Persistent=true łapie missed run), ale to DOKŁADNIE historyczny incydent 03-07.05 (13 nazw persystowało 4 dni, w tym top-performerzy — udokumentowane w `manual_overrides_daily_reset.py:4-8`). Awaria/wyłączenie timera bez drugiego bezpiecznika = wpisy żyją bez końca. Nie policzone (obecnie 0).
**Rekomendacja:** drugi bezpiecznik — sanity „updated_at starszy niż X h → auto-clear/alert".

### P3 — `auto_koord` FAILED mis-labeluje zdrowe race-avoids (zanieczyszczona metryka)
**Surface:** `auto_koord.py:200-201` (`emit_event_log` → `AUTO_KOORD_FAILED` gdy `skipped=True`).
**Dowód:** 8/8 „failów" w `auto_koord_log.jsonl` = `race_avoided_assigned_to_26/21` (poprawny idempotentny skip pre-fetch guarda, attempts=0). Zero prawdziwych failów. `all_retries_exhausted=0`.
**Rekomendacja:** trzeci event `AUTO_KOORD_SKIPPED` dla `skipped=True`.

### INFO (POZYTYWY — potwierdzone niezależnie)
- **Killswitch realnie HOT + kanoniczny:** `decision_flag("ENABLE_AUTO_ASSIGN")` per-event, 1. linia `maybe_execute` (`:219`) → OFF = `return None` zero I/O. Brak env-override (zweryfikowane) → przycisk konsoli `POST /api/coordinator/auto-assign` → `flags_admin` → flags.json = autorytatywny, bez driftu.
- **Reset RC3 NAPRAWIONY:** timer active+enabled, Persistent, ostatni exit 0, `record_oneshot_success.sh` + `onfailure.conf` drop-iny obecne. Fail-loud na uszkodzonym pliku (`daily_reset:31-33 exit 1`).
- **Cooldown po PANEL_OVERRIDE realnie wpięty:** `learning_log.jsonl` ma **1124 świeże** PANEL_OVERRIDE (ostatni 01.07T20:06) z `proposed_courier_id`/`actual_courier_id`/`ts` — dokładnie schemat który skanuje `_recent_override_for_courier:128-147`.
- **Propagacja blokady kuriera:** konsola/Telegram/reset piszą do TEGO SAMEGO `manual_overrides.json` który czyta `courier_resolver` → zablokowany kurier nie wejdzie do puli → nie zostanie auto-przypisany (executor bez nowego bypassu).
- **auto_koord idempotencja:** dwuwarstwowa (`needs_auto_koord.is_unassigned` + `perform_auto_koord` pre-fetch race-guard) — dowód: 8× race_avoided.

---

## KONKRETNE RYZYKA PIERWSZEGO FLIPU (`ENABLE_AUTO_ASSIGN` OFF→ON) — synteza dla lane
1. **NIE no-op:** ~1-2 realne `gastro_assign` /dzień od razu (strict-true w ledgerze), na slice gold/gps/pool≥3 — ale ścieżka executor→panel dziewicza E2E.
2. **Fałszywy sukces (P1):** panel 200-z-błędem/pustka → „✅ wykonane" bez przypisania → cichy drop; brak monitora false-success.
3. **Zły kurier (P1):** name→cid re-resolucja fuzzy dla kuriera spoza aktualnego `kurier_ids.json` (dziś 0 wg superset, ale data-dependent na autopair).
4. **Podwójne przypisanie (P1):** reconcile-lag 15-90 s + 2. event bez guardu per-order.
5. **Czas=0 (P2, latentne):** dziś nie trafia (0/10), ale ścieżka żywa.
6. **Podwójna prezentacja (P2):** koordynator widzi propozycję już-auto-przypisanego → override.
7. **Cofnięcie:** JEDYNY hamulec = flaga (przycisk/flags.json), **brak stop-lossu/monitora** (odpowiednik carried_first_guard dla auto-assign NIE istnieje). Subprocess w locie (≤30 s) dokończy się po OFF.
8. **Sizing:** plaster D „~125/dzień" **niepotwierdzony** — telemetria ~35-50/dzień (D'≈29-34), próbka za cienka (≥7 dni przed rampą).

## ODPOWIEDZI NA PYTANIA LANE
- **(1) manual_overrides — kto wyklucza / reset / widoczność:** Wyklucza operator (Telegram `/stop`/„nie pracuje") + konsola (`courier_block`) → `excluded`/`excluded_cids`. Reset = **żywy** (`dispatch-overrides-reset` 06:00, Persistent, exit 0). **Ciche usunięcie NIE ma alertu** — tylko flag-gated observability log; BRAK KANDYDATÓW alertowane wyłącznie dla czasówek (P2 wyżej). RC3 „martwy 4 dni" = historyczny, naprawiony.
- **(2) auto_koord — eskalacja vs auto-hold:** LIVE, paruje czasówki (prep≥60) do Koordynatora (cid=26) na NEW_ORDER; T-60/50/40 budzi je `czasowka_scheduler` (komplementarne, brak konfliktu). Zdrowe; współdzieli luźny kontrakt gastro (P1/P2).
- **(3) auto_assign_executor — 1. flip:** cały kod przeczytany. Bezpieczniki: killswitch hot ✔, rate-cap 6/h ✔ (liczy tylko sukcesy — porażki bez backpressure), cooldown 60 min ✔ (dane realne), fail-safe ✔, override-guard ✔. **Braki krytyczne przed ON:** sentinel sukcesu (P1), guard per-order (P1), walidacja name→cid (P1), monitor+stop-loss (brak). Ścieżka gastro_assign E2E = `login()→get_kurier_id()→assign() POST przypisz-zamowienie` — nigdy nie odpalona z executora.

## POKRYCIE / JAWNE LUKI
- **Przeczytane 100%:** 3 pliki rdzeniowe + `auto_assign_gate.py` + `gastro_assign.py` + konsumenci (`courier_resolver:1420-1623`, `shadow_dispatcher:1300-1335`, `dispatch_pipeline:2895-2954`, `panel_watcher:1378-1394`) + 4 unity systemd + skrypt resetu. Oracle na żywo: shadow_decisions (1210 rek.), auto_koord_log (1065), learning_log PANEL_OVERRIDE (1124), reset-log + journal.
- **NIE wykonano** żadnego realnego `gastro_assign` (read-only) — E2E realnego przypisania + name→cid na żywym panelu = zadanie kontrolowanego 1. ON z Adrianem, nie audytu.
- **NIE uruchomiono** `pytest tests/{test_auto_assign_executor,test_auto_assign_gate,test_auto_koord,test_working_override*}.py` (read-only) — pliki istnieją, nie zweryfikowano zieleni na bieżącym kodzie.
- **Rate „~2/dzień strict"** miesza gate pre/post-AUTON-02 (30.06) — dokładny rozkład bieżącego gate wymaga ≥7 dni; post-30.06 slice cieńszy (zgodne z SWEEP_auto_assign_executor „~0,6/dzień post-cutoff").
- **Reconcile-lag 15-90 s** z CLAUDE.md (historyczny), nie re-zmierzony.
