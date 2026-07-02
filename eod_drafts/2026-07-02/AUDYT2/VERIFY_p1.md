# AUDYT 2.0 — ADWERSARYJNA WERYFIKACJA kandydatów P1

**Data:** 2026-07-02 · **Tryb:** READ-ONLY (zero edycji kodu/flag/serwisów/git; jedyny zapis = ten plik).
**Metoda:** dla każdego findingu próba OBALENIA drugą, niezależną metodą — własny odczyt ŻYWEGO kodu (nie deliverable), grep bliźniaków, empiryczna symulacja, count w ledgerach. Deliverable źródłowy przeczytany PRZED weryfikacją, ale werdykt oparty na własnym gruncie.

**Wynik jednym zdaniem:** 4× CONFIRMED (z korektami severity), 1× CONFIRMED-ale-DOWNGRADE (P1→P2, zgodnie z własną oceną pasa), **1× REFUTED — już naprawione 28.06** (finding 6, guard żywy + bliźniak też załatany).

---

## 1. `gastro_assign.py:11` `_WARSAW=+2` hardcode — bomba zimowa na ścieżce HH:MM → **CONFIRMED** (latentny do DST)

**Weryfikacja drugą metodą (empiryczna symulacja, nie lektura):** odtworzyłem logikę `main()` l.152-160 na dwóch instantach:
```
LATO  2026-07-15 12:00Z:  hardcoded now=14:00  real_Warsaw=14:00  delta=0min
    koordynator wpisuje 14:30 -> time sent = 30 min   ✅ poprawnie
WINTER 2026-11-15 12:00Z:  hardcoded now=14:00  real_Warsaw=13:00  delta=+60min
    koordynator wpisuje 13:30 -> time sent = 1410 min  🔴 (skok na jutro)
    koordynator wpisuje 14:30 -> time sent = 30 min    🔴 (miał być 90; niedoszacowanie -60)
```
- **Hardcode realny:** `osrm_client.py`? nie — `gastro_assign.py:11` `_WARSAW = timezone(timedelta(hours=2))` (stały offset, NIE ZoneInfo). Odczytane wprost.
- **Ścieżka HH:MM żywa:** `if ':' in args.time` (l.152) → `datetime.now(_WARSAW)`. Callerzy wewnętrzni przekazują INT minuty (zweryfikowane wprost: `auto_assign_executor.py:158` `str(int(time_minutes))`, `auto_koord.py:143` `--koordynator` bez `--time`) → gałąź HH:MM ich NIE dotyczy. **HH:MM odpala TYLKO konsola/CLI**: `Ops13Console.tsx:842` `time_arg: best.eta_pickup_hhmm` i `:1717` `time_arg: czas` przekazują HH:MM (potwierdzone gruntem frontu). Proxy oracle: 34 live-assigny HH:MM (m.in. 484776 „17:30" dziś).
- **Zima faktycznie przesuwa:** TAK — `datetime.now(offset+2)` zawsze = UTC+2; realny Warsaw zimą = UTC+1 → `now_waw` o 60 min do przodu → near-future (<60 min) wpada w `if target < now_waw` (l.157) → `+1 dzień` → ~1410 min; far-future (>60 min) niedoszacowane o 60 min.
- **Czy inny clamp to łapie:** NIE. `gastro_assign` ma tylko dolny `max(1, ...)` (l.160), brak górnego. Most konsoli (`assign.py`, `Ops13Console.tsx`) — tryb minut liczy `String(mins)` PO stronie frontu z `warsawNowMin()` (poprawnie, omija bug), ale tryb HH:MM przekazuje surowy string do backendu z hardcodem. Zero clampu na 1410.

**Werdykt: CONFIRMED. Severity: P1 od ~2026-10-25 (ost. niedziela X, przejście CEST→CET); dziś P2/latentny — zero wpływu (lato).** Materialność: 0/dzień teraz; zimą dotyka KAŻDY HH:MM-assign konsoli (tryb minut niewrażliwy). Trigger pewny i datowany. Fix u źródła: `ZoneInfo("Europe/Warsaw")` zamiast stałego offsetu (izolowana 1-liniówka).

---

## 2. `gastro_edit.py` TOCTOU/lost-update — brak integrity-guard którego bliźniak MA → **CONFIRMED** (mechanizm), materialność ~0 dziś

**Weryfikacja drugą metodą (odczyt OBU plików + grep):**
- `gastro_edit.build_payload` l.210-211: ZAWSZE wstawia `modal_status_zamowienia` i `modal_kurier_select` ze snapshotu `read_order` (l.268); `post_update` l.291 POST-uje CAŁY formularz. Odczytane wprost — potwierdzone.
- `grep integrity|verify|re-read|PanelIntegrity` w gastro_edit = **PUSTE**. Brak jakiejkolwiek weryfikacji po zapisie.
- Bliźniak `panel_sync._write_panel_status` l.219-238: po zapisie re-read (bez cache) i **raise `PanelIntegrityError`** gdy zmienił się `id_kurier`/`id_location_to`/`id_address`; `run_once` l.282-285 łapie i liczy `integrity_breach`. Odczytane wprost — asymetria potwierdzona.
- **Korekta framingu (uczciwie, na moją niekorzyść):** guard panel_sync to POST-write DETEKCJA+alert-do-człowieka, NIE lock — sam też NIE zapobiega wyścigowi, tylko go WYKRYWA. gastro_edit nie ma nawet detekcji (cichy clobber). Asymetria = „bliźniak krzyczy, gastro_edit milczy", nie „bliźniak jest bezpieczny".
- **Okno wyścigu — grunt (koryguje SWEEP):** `login()` (l.264) jest PRZED `read_order` (l.268), więc read→write = `build_payload`+diff+print ≈ **sub-sekundowe** (SWEEP pisał „kilka s" — zawyżone). Kolizja wymaga concurrent writera (auto_assign/panel_sync/tap) w tej sub-sekundzie.
- **Materialność (grunt audytu):** edit-live = **2 zdarzenia total** (484129, 484269). Wyścig dziś skrajnie mało prawdopodobny; 0 dowodów fizycznego odpalenia w logach.

**Werdykt: CONFIRMED (asymetria + mechanizm clobberu = fakt kodu). Severity: P2 dziś / P1 latentnie pod autonomią+skalą** (executor = drugi concurrent writer do gastro → dokładnie ta klasa gryzie po flipie). NIE „żywy pożar" — mina. Fix: dołożyć `PanelIntegrityError`-równoważnik (re-read) do gastro_edit = domknięcie rodziny update-zamowienie.

---

## 3. FAŁSZYWY SUKCES na exit-code (puste ciało / HTML logowania → ASSIGN_OK) → **CONFIRMED**

**Weryfikacja drugą metodą (odczyt 3 miejsc + repro logiki):**
- `gastro_assign.py:206`: `... or 'error' not in str(result).lower()`. Repro: `{'raw': ''}` → `"{'raw': ''}"`.lower() nie zawiera „error" → **True → ASSIGN_OK**. Potwierdzone.
- `gastro_assign.py:208-209` (gałąź „nieoczekiwana odpowiedź"): drukuje `ASSIGN_ERROR`, **ale NIE `sys.exit(1)`** — spada na koniec `try`, `main()` wraca, proces kończy **exit 0**. Jedyny exit 1 to `except` (l.210-212, wyjątek sieci). Potwierdzone wprost.
- Konsumenci ufają returncode:
  - `auto_assign_executor._default_assign_runner:161` — `if r.returncode == 0: return True`. (executor OFF, ale to ścieżka po flipie)
  - `auto_koord.perform_auto_koord:148` — `if r.returncode == 0: return {"success": True}`. **LIVE** (flaga `AUTO_KOORD_ON_NEW_ORDER_ENABLED=true`, ostatni wpis 01.07 AUTO_KOORD_ASSIGNED).
- **Niuans (na moją niekorzyść vs finding):** most konsoli `assign.py` wymaga TOKENU `ASSIGN_OK` w stdout — więc gałąź „nieoczekiwana odpowiedź" (bez tokenu) konsola BY złapała. Ale pustka-jako-sukces (drukuje `ASSIGN_OK`) oszuka NAWET konsolę; a auto_koord+executor (returncode) oszuka OBIE gałęzie.

**Werdykt: CONFIRMED.** Dla auto_koord (LIVE): dziś **benigne** — puste ciało JEST sukcesem parkowania (1057 ok); latentny cichy-drop wymaga panelu zwracającego 200-z-błędem-bez-słowa-„error" (0 zaobserwowanych). Dla executora: **twardy blocker przed 1. ON**. **Severity: P1 jako bramka autonomii / P2 realny-wpływ-dziś.** Materialność: nie policzona, 0 zaobserwowanych dropów. Fix: wymóg sentinela `ASSIGN_OK:` + `sys.exit(1)` w gałęzi l.208 + usunąć człon `'error' not in`.

---

## 4. Pierwszy flip NIE jest no-opem; kurier po NAZWIE; brak idempotencji per-zlecenie → **CONFIRMED**

**Weryfikacja drugą metodą (count w ledgerze + odczyt executora + grep):**
- **10 strict `would_auto_assign=true`** w `scripts/logs/shadow_decisions.jsonl` — **potwierdzone własnym grepem** (`grep -c '"would_auto_assign": *true'` = 10; okno 2026-06-27→07-01, ~4,5 dnia ≈ ~2/dzień; `_d`=55, `_dprime`=47 — zgodne z L05). NIE no-op: po flipie strict odpali ~1-2 realne gastro_assign/dzień.
- **Executor nigdy nie odpalony:** `auto_assign_state.json` **nie istnieje**; `AUTO_ASSIGN_EXECUTED` w learning_log = **0**; `ENABLE_AUTO_ASSIGN=false`. Ścieżka executor→panel dziewicza E2E. Potwierdzone.
- **Kurier po NAZWIE:** `auto_assign_executor.py:254` `runner(oid, str(name), ...)` gdzie `name=best.get("name")` (l.232); `cid` jest w `best["courier_id"]` (l.231) ale **NIE przekazany** → gastro re-rozwiązuje name→cid heurystyką „pierwsze słowo+inicjał, przy niejednoznaczności PIERWSZY" (`gastro_assign.py:78-83`). Potwierdzone wprost.
- **Brak idempotencji per-order:** grep w executorze `dedup|idempot|already|per-order` = **PUSTE**; bezpieczniki tylko GLOBALNE (rate-cap 6/h l.241) + per-kurier cooldown (l.247). Dodatkowo `shadow_dispatcher.py:1332` `maybe_execute(...)` biegnie PRZED `event_bus.mark_processed(eid)` (l.1338) — crash między nimi → re-process → re-fire; 2. event (inny event_id) tego samego wciąż-nieprzypisanego oid → 2. PROPOSE → 2. assign (rate-cap nie chroni per-oid). Potwierdzone wprost.

**Werdykt: CONFIRMED.** Dziś INERTNE (executor OFF, killswitch zweryfikowany). Ryzyko całkowicie o 1. NADZOROWANY ON (ACK-gated). **Severity: P1 (ryzyko 1. flipu).** Materialność: ~1-2 realne assigny na 1. ON (post-30.06 slice cieńszy ~0,6/dzień wg SWEEP); zły-kurier dziś 0 (name→cid superset trafia), ale data-dependent na autopair. Rekomendacja: przekazywać cid nie nazwę + guard per-order + walidacja round-trip PRZED flipem; 1. ON `MAX_PER_HOUR=1` + nadzór.

---

## 5. Grafik: producent UTC vs konsumenci Warsaw (nocny zły dzień) + literówka→coupling kasuje wpis → **CONFIRMED mechanizmy, ale severity DOWNGRADE P1→P2**

**Weryfikacja drugą metodą (odczyt fetch_schedule + schedule_utils + serwer TZ):**
- **Serwer TZ = `Etc/UTC`** (potwierdzone `timedatectl`).
- **Date-mismatch realny:** `fetch_schedule.py:130` `today = datetime.now().strftime("%d-%m-%y")` — naiwne UTC; `:158-161` stempluje `date` naiwnym UTC. Konsumenci godzin w Warsaw (`courier_resolver._shift_start_dt` ZoneInfo). Przy Warsaw 00:00-02:00 (=UTC 22:00-24:00 lato / 23:00-24:00 zima) `datetime.now()` UTC pokazuje POPRZEDNI dzień Warszawy → ładowana kolumna złego dnia. L03 zweryfikował „DATE MISMATCH=True" LIVE o 22:30 UTC. **Uwaga:** `load_schedule.py:157-158` własny check daty TEŻ używa naiwnego UTC → oba UTC → check się NIE odpala (tylko `_log.warning`, dane serwowane). Potwierdzone wprost.
- **Literówka→coupling realny:** `parse_hour` (l.42-53) zwraca None dla `"11.00"`/`"11,00"`/`"do 19"`/`""` (obsługuje tylko `:` i czysty int). `:121` `entry = {...} if (start_fmt AND end_fmt) else None` → literówka w JEDNEJ godzinie kasuje CAŁY wpis → `dispatchable_fleet` `continue` → kurier znika z puli (grozi BRAK KANDYDATÓW jego zleceniom) LUB floor/cap martwy. Potwierdzone wprost.
- **Próba obalenia:** czy to P1? Date-mismatch = deterministyczny ale okno 2h/noc, wolumen nocny niski, nie policzony w zł. Literówka = 0 bieżących instancji (dziś 12/12 sparsowane czysto). SPOF „pusty grafik→cała flota fail-open" wymaga PODWÓJNEJ awarii (plik zniknął + Sheets down), 0× w 7 dni, częściowo fail-CLOSED przez `FAIL12_SCHEDULE_FAILOPEN`. **Pas L03 sam ocenił te 3 na P2, nie P1** — zgadzam się.

**Werdykt: CONFIRMED (oba mechanizmy realne i otwarte). Severity: P2 (NIE P1) — korekta zgodna z oceną pasa.** Materialność: sygnał bieżący luk producenta = `FAIL12_SCHEDULE_FAILOPEN` 31×/7d (~4-5/dzień aktywny kurier dociera do feasibility bez okna zmiany); date-mismatch nisko-wolumenowy; literówka bez instancji. Fix u źródła: `datetime.now(ZoneInfo("Europe/Warsaw"))` w producencie + log/alert `parse_failures` + guard content-staleness.

---

## 6. `osrm_client.py` fallback double-traffic → **REFUTED — JUŻ NAPRAWIONE 28.06** (finding był realny 27.06, zamknięty dzień później)

**Weryfikacja drugą metodą (odczyt ŻYWEGO kodu + grep wszystkich ścieżek + bliźniaki):**
- **Guard żywy i bezwarunkowy:** `osrm_client._apply_traffic_multiplier` l.321-326: `if result.get("osrm_fallback"): ... result["traffic_multiplier"]=1.0; return result` — **PRZED** `mult = get_traffic_multiplier()` (l.327) i przed mnożeniem LIVE (l.373). NIE flag-gated (bezwarunkowy `if` na górze funkcji). Komentarz l.316: „#12 audyt 28.06" — fix wszedł DZIEŃ po findingu 27.06.
- **Fallback faktycznie trafia w guard:** `_haversine_fallback` l.393 ustawia `"osrm_fallback": True`; WSZYSTKIE ścieżki fallbacku przechodzą przez `_apply_traffic_multiplier` (l.592, 604, 637, 800) → guard łapie każdą. Potwierdzone grepem.
- **Bliźniak też załatany (MAPA KOMPLETNOŚCI):** `dispatch_pipeline.py:4164-4168` — ten sam fallback (`fleet_speed_kmh`=bucket korkowy), komentarz „#12 audyt 28.06 ... NIE mnóż dodatkowo get_traffic_multiplier ... Bliźniak osrm_client._apply_traffic_multiplier" → i faktycznie NIE mnoży. Fix propagowany do bliźniaka.
- **Próba obalenia w drugą stronę (czy double-count żyje w innym twinie):** `chain_eta.py:100-108` = INNY fallback (`haversine_km`, baza BEZ korka) — komentarz l.104 „wcześniej tego nie robił → underestymacja" → dołożono `get_traffic_multiplier` RAZ dla parytetu z OSRM. To POJEDYNCZE, intencjonalne zastosowanie na nie-korkowej bazie, **NIE double-count**. `get_traffic_multiplier` poza osrm_client występuje tylko tu (grep potwierdza).

**Werdykt: REFUTED (mechanizm NIE istnieje w bieżącym kodzie).** Finding 27.06 był realny (P2 confirmed wtedy), naprawiony u źródła 28.06 z pokryciem bliźniaka. „Sierota nieujęta w roadmapie 30.06" = **awaria higieny rejestru** (nie oznaczono jako CLOSED w L01/REG_old), NIE żywy bug. **Meta-finding WAŻNY i osobny (P3-proces):** 4 nieskorelowane rejestry produkują widmowe sieroty — potwierdza tezę L01, że findingi potrzebują JEDNEGO rejestru ze statusem. Ale sam osrm-double-traffic = do zamknięcia w rejestrze, nie do naprawy.

---

## TABELA ZBIORCZA

| # | Finding | Verdict | Severity (skoryg.) | 1 zdanie |
|---|---------|---------|--------------------|----------|
| 1 | `gastro_assign._WARSAW=+2` HH:MM zima | **CONFIRMED** | P1 od ~25.10 / P2 dziś (latentny, pewny trigger) | Empirycznie: zimą near-future HH:MM konsoli → 1410 min (skok na jutro), far → −60 min; brak clampu; dziś lato = 0 wpływu. |
| 2 | `gastro_edit` TOCTOU bez integrity-guard | **CONFIRMED** (mechanizm) | P2 dziś / P1 latentnie pod autonomią | Asymetria realna (panel_sync WYKRYWA+alertuje, gastro_edit milczy), ale okno sub-sekundowe + edit-live=2 → materialność ~0 dziś. |
| 3 | Fałszywy sukces na exit-code | **CONFIRMED** | P1 jako bramka autonomii / P2 wpływ dziś | Puste ciało→ASSIGN_OK exit 0 i gałąź „nieoczekiwana"→exit 0; auto_koord (LIVE) benigny, executor po flipie = cichy drop. |
| 4 | 1. flip nie no-op + kurier po nazwie + brak idempotencji per-order | **CONFIRMED** | P1 (ryzyko 1. ON), inertne dziś | 10 strict would_auto w ledgerze (własny grep), name-passing l.254, brak per-order guardu, E2E nigdy nie przeszło. |
| 5 | Grafik UTC-vs-Warsaw + literówka→coupling | **CONFIRMED** (mechanizmy) | **P2** (downgrade z P1, zgodnie z L03) | Date-mismatch deterministyczny ale 2h/noc nisko-wolumenowy; literówka bez bieżącej instancji; SPOF wymaga podwójnej awarii. |
| 6 | `osrm_client` fallback double-traffic | **REFUTED** | — (zamknięty; meta P3-proces) | Guard l.321 żywy+bezwarunkowy, bliźniak dispatch_pipeline też załatany 28.06; „sierota" = błąd higieny rejestru, nie bug. |

**Wnioski przekrojowe:** (a) rodzina „ręce piszące do gastro" ma niespójną walidację sukcesu (`'error' not in body`) i niespójny integrity-guard — findingi 2+3 to ta sama klasa, jeden wspólny fix (sentinel + re-read) domyka oba + auto_koord. (b) Autonomia (4+3+2) = te trzy MUSZĄ być zamknięte RAZEM przed 1. ON (executor jest concurrent writerem, ufa returncode, nie ma idempotencji). (c) Finding 6 udowadnia że bez JEDNEGO rejestru ze statusem audyt produkuje fałszywe „otwarte P2" — priorytet dla scalenia rejestrów (L01).
