# AUDYT 2.0 — SWEEP „RĘCE PISZĄCE DO ŹRÓDŁA PRAWDY"

**Lane:** `gastro_edit.py` + `coordinator_time_recheck.py` + ścieżka `przypisz-zamowienie`/`edit-zamowienie`
**Data:** 2026-07-02 (recon na żywo 01.07 wieczór)
**Tryb:** READ-ONLY wobec produkcji. ZERO POST do gastro (nawet testowych), zero edycji kodu/flag/serwisów. Wolno: czytać, journalctl, systemctl show/status, analizy. Zapis: tylko ten deliverable + scratchpad.
**Baza:** audyt 1.0 (30.06) zinwentaryzował moduły, ale ich NIE sweepował. To pierwszy pełny sweep tej klasy.

---

## 0. INWENTARZ RĄK PISZĄCYCH (kanoniczny — kto realnie mutuje źródło)

Termin „źródło prawdy" ma tu DWA różne cele zapisu — to kluczowe rozróżnienie:

| # | Ręka | Endpoint / cel | Co pisze | Callerzy | Stan LIVE |
|---|------|----------------|----------|----------|-----------|
| 1 | `scripts/gastro_assign.py` | `przypisz-zamowienie` (gastro) | `id_kurier` + `time` (odbiór) | telegram_approver (tap ASSIGN), auto_koord, auto_assign_executor (gated OFF), czasowka_proactive/handlers, console `assign.py`, gastro_scoring | **LIVE** (tap człowieka + konsola) |
| 2 | `scripts/dispatch_v2/gastro_edit.py` | `update-zamowienie` (gastro) | pełny formularz modal_* (adres/uwagi/telefon/COD/status/kurier) | console `assign.py` (`edit_order`), `address_mismatch.py`, `tools/address_mismatch_review.py` | **LIVE** (konsola, `COORDINATOR_EDIT_LIVE=1`) |
| 3 | `courier_api_panelsync/panel_sync.py::_write_panel_status` | `update-zamowienie` (gastro) | pełny formularz modal_* + nadpisany `status` | console `assign.py` (`cancel_order`, status 9), `courier-panel-sync.service` (odbicie statusów apki 3/4/5/6/7) | **LIVE** (`COORDINATOR_CANCEL_LIVE=1`) |
| 4 | `coordinator_time_recheck.py` (kolejka) | **NIE gastro** → wyzwala watcher, który CZYTA gastro i pisze do **orders_state Ziomka** (`czas_kuriera_warsaw`/`pickup_at_warsaw`) | kolejka oid → drain → `_diff_czas_kuriera`/`_diff_pickup_time` → `state_machine.update_from_event` | panel `coordinator.py::/refresh-time` → bridge `integrations/ziomek/coordinator_time_recheck.py::request_refresh` | **LIVE** (flaga ON) ale **1 użycie od deployu** |

**Wniosek strukturalny:** endpoint gastro `update-zamowienie` ma **TRZECH** writerów (gastro_edit #2, panel_sync #3, a #1 to `przypisz-zamowienie`). To rodzina bliźniaków „wyślij CAŁY formularz" — rozjazd między nimi opisany w §5. Ręka #4 NIE pisze do gastro (pisze do orders_state) — nazwanie jej „ręką piszącą do źródła prawdy" jest półprawdą: jej źródło to state Ziomka, a gastro tylko czyta.

---

## 1. `gastro_assign.py` (przypisz-zamowienie) — sweep

**Co pisze:** `id_kurier`, `id_zamowienia`, `time`. Login świeży per wywołanie (fresh CSRF → 419 mało prawdopodobne, ale każde wywołanie = pełny login = koszt + rate).

**Semantyka `--time` (KLUCZOWY landmine, potwierdzony):**
- `':' in time` → traktowane jako **HH:MM** → przeliczane na **minuty-od-teraz** (`main` l.152-160).
- brak `':'` → **int minuty od teraz** (l.164-171).
- Panel API oczekuje MINUT (nie HH:MM, nie timestamp) — zgodne z CLAUDE.md.
- Callerzy wewnętrzni (telegram `round_up_to_5min`, auto_assign_executor `_time_minutes_from_record`, czasówki `5`) przekazują **zawsze int minuty** → gałąź HH:MM ich NIE dotyczy.
- **Gałąź HH:MM odpala TYLKO konsola** (ołówek czasu w OrderModal → `time_arg="HH:MM"`) i CLI. ORACLE: **34 live-assignów z HH:MM** w audycie (m.in. 484776 „17:30" dziś 15:07).

**🔴 `_WARSAW = timezone(timedelta(hours=2))` HARDCODE (l.11) — bomba sezonowa w ŻYWEJ ścieżce.** Gałąź HH:MM liczy `now_waw = datetime.now(_WARSAW)`. Latem (CEST=+2, teraz lipiec) poprawne. Zimą (CET=+1, po ost. niedzieli X.2026) `now_waw` będzie **1h do przodu** względem realnej ściany Warszawy → dla odbioru bliskiego (np. za 30 min) `target < now_waw` → guard `+1 dzień` (l.157-159) → do gastro poleci `time ≈ 1410 min` zamiast `30`. **Efekt zimą: near-future HH:MM-odbiory przeskakują na jutro.** Waga wysoka (live, 34 użycia/hist.), aktywuje się przy zmianie czasu.

**Idempotencja:** po stronie gastro (ponowny przydział = nadpisanie). Brak klucza idempotencji po stronie skryptu.

**Retry / 419 / timeout / 500:** **BRAK retry.** `urlopen(timeout=10)` → wyjątek łapany bare `except Exception` (l.210) → `ASSIGN_ERROR` + exit 1. ORACLE: PCZ-136948/136950/138073 (29.06) = `HTTP 500` (paczki poszły przez gastro_assign zamiast parcel_assign — patrz §6) — twarda porażka, brak ponowienia.

**Walidacja sukcesu (SŁABA):** `result.get('success') or result.get('status')=='ok' or 'error' not in str(result).lower()` (l.206). Trzeci człon = jeśli body panelu nie zawiera dosłownie „error" → uznane za sukces → drukuje `ASSIGN_OK`. Konsola ufa `ASSIGN_OK` (patrz §4) → fałszywy sukces się propaguje.

**bare-except / dopasowanie kuriera:** `get_kurier_id` — `except:` gołe (l.56). Dopasowanie częściowe: przy **niejednoznaczności bierze PIERWSZEGO** kandydata tylko z ostrzeżeniem (l.81-83) → ryzyko przypisania do ZŁEGO kuriera przy dwóch „Imię N." Cicha pomyłka celu.

**`--keep-time` re-fetch — wszystkie gałęzie:** `keep_time = args.keep_time or (args.koordynator and not args.time)` (l.145). Re-fetch `czas_odbioru` po loginie (l.184-198). Fail fetchu → `time_minutes=0` (l.198). **Sprzeczność semantyki `0`:** docstring l.91-92 „`0` = zostaw oryginalny", a CLAUDE.md landmine „sending 0 clears UI". Nierozstrzygnięte na żywej ścieżce (konsola bez time_arg → `--keep-time`).

---

## 2. `gastro_edit.py` (update-zamowienie) — sweep

**Co pisze:** pełny payload modal_* zrekonstruowany z `edit-zamowienie` (bezpieczny merge: nadpisz TYLKO podane `--opcje`, resztę odtwórz wiernie). Pola: restauracja/street/nr_domu/nr_mieszkania/city/phone/price/delivery_price/platnosc/uwagi/**status**/**kurier**. Czas odbioru NIE tędy (osobny endpoint). Domyślnie DRY-RUN, realny zapis tylko `--commit`.

**🔴 TOCTOU / lost-update — brak strażnika integralności (bliźniak `panel_sync` GO MA, gastro_edit NIE).** `build_payload` ZAWSZE wstawia `modal_status_zamowienia` i `modal_kurier_select` ze snapshotu `read_order` (l.210-211), a `post_update` wysyła CAŁY formularz. Okno read→(login)→write ≈ kilka s. Jeśli w tym oknie wpadnie assign/zmiana statusu (kurier, panel_sync, telegram tap), edycja **cofa** `id_kurier`/`status` do wartości sprzed odczytu. `panel_sync._write_panel_status` na tym samym endpoincie MA `PanelIntegrityError` (re-czyta i weryfikuje przed clobberem, l.229-236) — **gastro_edit takiej ochrony NIE ma.** To rozjazd bliźniaczy w rodzinie „wyślij cały formularz".

**🟠 Konkatenacja `delivery_address`→`--street` + rezydualny nr_domu (dowód w ORACLE).** Konsola mapuje CAŁY adres na `--street` (`_EDIT_FIELD_ARGS`, `assign.py` l.226). `regeocode_and_update` liczy `full_address = ' '.join((new_street, nr_domu))` (l.310) — a `nr_domu` z payloadu to STARY numer ze snapshotu. Audyt 29.06 oid **484269**: `changes={"delivery_address":"Mroźna 10/23"}` → `REGEOCODE_OK: 'Mroźna 10/23 10'` — numer „10" ze starego zlecenia doklejony do ulicy już zawierającej „10/23". Koordy się rozwiązały, ale adres/coords liczone z zlepka; do gastro `modal_nr_domu` idzie NIEzmieniony (duplikacja numeru: street ma numer + osobne pole nr_domu). Korekcja u źródła: przy delivery_address (cały adres) → wyczyść/zignoruj rezydualny nr_domu.

**Regeocode (fail-soft):** po zmianie adresu/miasta przelicza `delivery_coords` do orders_state przez `state_machine.upsert_order` (jedyny bezpieczny writer, flock+merge). Flaga `ENABLE_REGEOCODE_SYNC_TEXT=True` (LIVE) → zapisuje też `delivery_address`+`delivery_city`. Błąd importu/geokodu/zapisu → log + None (gastro już zapisane). OK — poprawnie fail-soft.

**Walidacja sukcesu (SŁABA, jak #1):** `code==200 and 'error' not in low and 'exception' not in low` (l.296). Body bez tych słów = „OK".

**Retry:** BRAK. `urlopen(timeout=15)` → bare except → EDIT_FAIL exit 1.

**city:** akceptuje id (cyfry) lub nazwę (rozwiązanie z opcji gastro); nieznane miasto → twardy `sys.exit(2)` (nie zgaduje — dobrze).

---

## 3. `coordinator_time_recheck.py` + konsument (panel_watcher) — sweep

**Cel:** kolejka WYMUSZONEGO re-checku czasów na klik koordynatora („Odśwież czas"). Świadomie omija automatowe strażniki (elastyk-forward-only, czasówka-passive) bo klik = decyzja człowieka, nie szum.

**Cykl życia kolejki:**
- **Pisze:** panel (subprocess venv Ziomka) → bridge `request_refresh` → pycode `ctr.enqueue(json)`; atomic (temp+fsync+rename) + flock + stempel UTC. Idempotentne (ponowny klik odświeża TTL).
- **Czyta/GC:** `panel_watcher._diff_and_emit` raz/tick → `_ctr.drain()` (l.2171). `drain` zwraca świeże (<TTL 5 min) i **czyści CAŁĄ kolejkę** (`_save({})`, l.114) — świeże skonsumowane, przeterminowane wyrzucone.
- **TTL:** `DEFAULT_TTL_MIN=5.0`. Klik starszy niż 5 min (watcher stał) → zignorowany. OK.

**🟠 drain DESTRUKCYJNY + oid poza `current_state` = CICHA STRATA.** Pętla force iteruje `current_state.items()` (orders_state), `_force = zid in _force_ids` (l.2181-2182). Jeśli forsowany oid:
- **nie ma na boardzie** (`zid not in html_order_ids`) → skip z logiem (l.2196-2198) — akceptowalne (terminal/zniknął),
- **nie ma w current_state w ogóle** (jeszcze nie zaingestowany) → NIGDY nie iterowany → **drain już wyczyścił kolejkę**, brak re-enqueue → klik przepadł bez śladu (żaden log). Wąskie okno, ale realne.

**Interakcja closest-day anchor (30.06) — ZWERYFIKOWANA:** force używa tego samego `normalize_order` → `panel_client._czas_kuriera_to_datetime` co automat (l.2214). Anchor = `min |candidate − pickup_at|` spośród {dziś, jutro, wczoraj} (zastąpił próg ±6h). Więc data HH:MM jest liczona identycznie na ścieżce force i pasywnej — spójne. To rozwiązuje wobble daty (case 484392/484410), ale NIE chroni przed śmieciową WARTOŚCIĄ (niżej).

**🟠 Elastyk force może ściągnąć wstecz gastro-śmieć (czasówka NIE, elastyk TAK).** `_diff_czas_kuriera`: czasówka → suppress ZAWSZE, także `deliberate` (l.847-860, kanał = pickup_at). Elastyk → forward-only tylko `and not deliberate` (l.867) → **klik omija guard wstecz**. Jeśli koordynator kliknie „Odśwież" NIE zmieniwszy rutcomu, a gastro w międzyczasie przestemplowało `czas_kuriera` na wcześniejszy (dokładnie ten śmieć z l.834-835: „16:22→15:04 5 s po assignie") → force propaguje śmieć wstecz dla elastyka. Anchor łata datę, nie wartość. Ryzyko latentne (klik = kontrakt „zmieniłem czas"; złamanie kontraktu → śmieć).

**Floor `pickup ≥ shift_start` — OMIJA (świadomie).** Ani `CZAS_KURIERA_UPDATED` ani `PICKUP_TIME_UPDATED` w state_machine NIE stosują floor do shift_start (l.672-799) — piszą surową wartość deklarowaną (tylko closest-day). Spójne z „czas_kuriera nietykalny/frozen R27". Ale znaczy: force/ck-update to KANAŁ, którym deklarowany czas może wylądować przed startem zmiany bez floor tutaj (floor to problem renderu/feasibility downstream — cross-ref [[preshift-pickup-floor-audit-2026-06-30]] + [[clamp-preshift-pickup-eta-2026-06-30]]). Nie luka tej ścieżki, ale styk do odnotowania.

**Frozen R27 (K-F z FAZA1_02) — interakcja.** `czas_kuriera` = wartość frozen (R27 ±5, [[frozen-pickup-eta-2026-06-19]]). Force-recheck NADPISUJE frozen `czas_kuriera` (przez `update_from_event` → `upsert_order`) i wywołuje `_invalidate_plan_on_committed_change` (l.2247) → silnik re-planuje z nowym oknem frozen. Czyli force = świadome PRZEMROŻENIE na nową wartość. To zgodne z intencją (ręczna decyzja), ale trzeba wiedzieć: **jedyna ścieżka, która legalnie rusza frozen `czas_kuriera` elastyka w OBIE strony to ten klik** (+ first_acceptance/mirror czasówki). Sanity `_verify_czas_kuriera_consistency` chroni przed niespójnym iso/hhmm (l.678).

**Wyścig z reconcile/panel_diff:** re-check dzieje się w tym samym `_diff_and_emit` co reconcile (jedna pętla, sekwencyjnie) — brak równoległości → brak wyścigu wewn. ticku. `_details` = prefetch per-tick (mapa budowana na starcie ticku, l.1157-1168); miss → live `fetch_order_details` (świeże). Forsowany oid dostaje dane świeże względem ticku który go przetwarza (klik→enqueue→NASTĘPNY tick prefetch fresh). Okno „prefetch zrobiony zanim gastro utrwaliło edycję koordynatora" = subsekundowe.

**Bliźniak strażnika czasówki (state_machine vs panel_watcher) — rozjazd latentny.** panel_watcher suppress czasówka+coordinator_force ZAWSZE; state_machine suppress czasówka tylko gdy `_src in _CK_PASSIVE_SOURCES` (`coordinator_force` NIE jest w zbiorze → PRZESZEDŁby). Spójne wynikowo TYLKO dlatego, że panel_watcher to jedyny emiter i nigdy nie wypuści czasówka+coordinator_force. Kruche: gdyby ktokolwiek wyemitował `CZAS_KURIERA_UPDATED src=coordinator_force` dla czasówki wprost do state_machine → utrwaliłby śmieciowy czas_kuriera czasówki. (`_CK_PASSIVE_SOURCES = {panel_re_check, pre_proposal_recheck}`, l.120.)

---

## 4. Most konsoli `integrations/ziomek/assign.py` — sweep

Owija #1/#2/#3 subprocessem, SHADOW-FIRST per flaga:
- `assign_courier` (gate `COORDINATOR_ASSIGN_LIVE`) → `_build_cmd`: `time_arg` → `--time HH:MM|min`, brak → `--keep-time`.
- `edit_order` (gate `COORDINATOR_EDIT_LIVE`) → `gastro_edit --commit`; `time_arg` świadomie NIE idzie (osobny endpoint) — OK.
- `cancel_order` (gate `COORDINATOR_CANCEL_LIVE`) → `panel_sync._write_panel_status` status 9.
- `assign_parcel` (gate `COORDINATOR_ASSIGN_LIVE`) → `dispatch_v2.parcel_assign` (orders_state, nie gastro).

**Detekcja sukcesu (ostrzejsza niż w skryptach):** wymaga tokenu `ASSIGN_OK`/`EDIT_OK`/`CANCEL_OK`/`PARCEL_ASSIGN_OK` w stdout (l.89/303 itd.). ALE token drukowany przez skrypty na SŁABEJ walidacji (§1/§2) → fałszywy `ASSIGN_OK` przechodzi przez konsolę jako sukces. Łańcuch słabości.

**Audyt:** każda próba (shadow+live) → `coordinator_assign_audit.jsonl` (append, fail-soft). Dobre pokrycie.

**Retry:** BRAK (pojedynczy `subprocess.run(timeout=ziomek_assign_timeout_s)`). Timeout → `subprocess_timeout`/`błąd wykonania`. Koordynator musi kliknąć ponownie.

---

## 5. ORACLE (stan na żywo)

**Efektywny stan flag per proces:**
- **Panel** `nadajesz-panel.service` (PID 316855, env `flags.systemd.env`): `PANEL_FLAG_COORDINATOR_ASSIGN_LIVE=1`, `COORDINATOR_EDIT_LIVE=1`, `COORDINATOR_CANCEL_LIVE=1`, `COORDINATOR_PLAN_LIVE=1`. **Wszystkie 3 ręce write = LIVE.** (Uwaga: `flags.py DEFAULT_FLAGS` = wszystkie False; env nadpisuje — „shadow-first" default jest w prod zdjęty.)
- **Silnik** `flags.json`: `ENABLE_COORDINATOR_FORCE_TIME_RECHECK=True`, `ENABLE_ELASTYK_CK_NO_BACKWARD=True`, `ENABLE_CZASOWKA_CK_PASSIVE_GUARD=True`, `ENABLE_PICKUP_TIME_MIRRORS_CK=True`, `ENABLE_REGEOCODE_SYNC_TEXT=True`.
- **common.py (frozen na starcie procesu, NIE hot-reload):** `ENABLE_V319G_CK_DETECTION` default „1"=ON, `ENABLE_PICKUP_TIME_DETECTION` default „1"=ON. Rozjazd hot-reload: force-recheck = `C.flag` (hot), detekcje = stałe modułu (wymagają restartu). Killswitch detekcji ≠ hot.
- `dispatch-panel-watcher.service`: active (restart 01.07 21:26 UTC), loguje do `scripts/logs/watcher.log` (NIE journal — dlatego journalctl grepy puste).

**Historia drenów kolejki (watcher.log, cały log):**
- `COORDINATOR_FORCE_TIME_RECHECK drained` = **1** wystąpienie: `2026-06-30 09:37:54 drained 1 oid ['484410']`. **Od deployu (30.06) button użyty DOKŁADNIE raz.**
- `V3.19g1 oid` (ck-update, głównie pasywny/first_acceptance) = **4930** — bardzo aktywne.
- `PICKUP_TIME_UPDATED oid` = **0** — kanał pickup_at (mirror czasówki) DORMANT w oknie logu.
- `CK_ELASTYK_BACKWARD_BLOCKED` = **2070**, `CK_PASSIVE_SUPPRESSED` = **4878** — strażniki anty-wobble pracują masowo.

**🟠 Rozjazd button↔audyt.** Audyt panelu `coordinator_assign_audit.jsonl`: **0 wpisów `kind=coordinator_time_recheck`.** Ale watcher zdrenował 484410 (30.06) → ktoś MUSIAŁ enqueue. Brak wpisu audytu = ten dren NIE przyszedł przez audytowany endpoint `request_refresh` (najpewniej ręczna weryfikacja przez bezpośredni `enqueue`, [[czas-kuriera-closest-day-anchor-2026-06-30]] „484410 force-drained"). **Ścieżka end-to-end button→request_refresh→audyt→drain jest NIEUDOWODNIONA na żywo** (feature świeży 30.06, realnie nietknięty przez UI).

**Historia edit-live (audyt, kind=edit=20):**
- do 27.06 08:5x — SHADOW (przed aktywacją `COORDINATOR_EDIT_LIVE` o 13:16).
- **28.06 18:15 LIVE** oid 484129 → Zaciszna 4/Olmonty → `REGEOCODE_OK`.
- **29.06 14:07 LIVE** oid 484269 → Mroźna 10/23 → `REGEOCODE_OK: 'Mroźna 10/23 10'` (dowód konkatenacji §2).

**Historia assign-live (audyt):** 34 z HH:MM `time_arg` (dowód aktywnej gałęzi HH:MM §1). Dziś 01.07 15:07 oid 484776 „17:30" → ASSIGN_OK. Paczki PCZ-* (29.06) → HTTP 500 (§6).

---

## 6. Poboczne (styk lane, do przekazania właściwym lane'om)

- **Routing paczek do gastro_assign → 500.** PCZ-136948/136950/138073 poszły przez `assign_courier`→gastro_assign (nie `assign_parcel`). `coordinator.py::assign` ma rozgałęzienie do `_assign_parcel` (l.198), ale najwyraźniej po kluczu, który nie łapie prefiksu `PCZ-` (memory: klucz paczki `900M+id`). Lane paczek.
- **panel_sync jako write-hand** (status) — pełny sweep NALEŻY do lane statusów; tu odnotowany jako trzeci bliźniak update-zamowienie z `PanelIntegrityError` (kontrast do gastro_edit).

---

## POKRYCIE

Zakres sweepowany (15 klas MAPY KOMPLETNOŚCI wg Przykazania #0):

| Klasa | Pokryte | Uwaga |
|---|---|---|
| Pola zapisywane | ✅ | #1 time+kurier; #2/#3 pełny modal_*; #4 czas_kuriera/pickup_at→orders_state |
| Walidacje przed zapisem | ✅ | city fail-loud (dobre); success-detection słaba (luka) |
| Idempotencja | ✅ | gastro-side (assign nadpisuje); kolejka idempotentna; #2 merge |
| Retry / 419 / timeout / 500 | ✅ | BRAK retry we WSZYSTKICH rękach; login fresh łagodzi 419; 500 twarde |
| Częściowy zapis / TOCTOU | ✅ | #2 lost-update (cały formularz, brak integrity guard vs panel_sync) |
| Semantyka `time` min vs HH:MM | ✅ | rozdzielona; HH:MM tylko konsola/CLI; `_WARSAW` hardcode (luka) |
| `--keep-time` re-fetch (wszystkie gałęzie) | ✅ | spójny; sprzeczność semantyki `0` |
| bare-except / parsing | ✅ | get_kurier_id `except:` + niejednoznaczny match→pierwszy |
| Callerzy (grep pełny) | ✅ | #1: 6 callerów; #2: 3; #3: 2; #4: panel bridge. auto_assign_executor gated OFF |
| Bliźniaki | ✅ | rodzina update-zamowienie (gastro_edit / panel_sync / build_payload); guard czasówki panel_watcher↔state_machine |
| Flagi efektywne per proces | ✅ | panel env (3× LIVE) + flags.json + common frozen |
| Cykl życia kolejki (pisz/czytaj/GC/wyścig) | ✅ | enqueue/drain/TTL; drain destrukcyjny (luka); brak wyścigu wewn. ticku |
| closest-day anchor (30.06) | ✅ | force używa tej samej ścieżki; data spójna; wartość niechroniona |
| floor pickup≥shift_start | ✅ | omija świadomie (declared time); cross-ref preshift-audit |
| frozen R27 (K-F) | ✅ | force = świadome przemrożenie; sanity iso/hhmm; jedyny legalny kanał wstecz elastyka |
| ORACLE (journal/ledger/flagi) | ✅ | watcher.log (nie journal); audyt jsonl; drain=1; edit-live=2; assign HH:MM=34 |

**NIE sweepowane (poza lane, wskazane):** pełny `panel_sync` (lane statusów), routing paczek (lane paczek), `address_mismatch.py`/`tools/address_mismatch_review.py` jako callerzy gastro_edit (przejrzane jako callerzy, nie jako moduły).

---

## JAWNE LUKI (priorytetyzacja)

1. **🔴 `gastro_assign._WARSAW=+2` hardcode — bomba zimowa w LIVE.** Gałąź HH:MM (34 użycia/hist., konsola) zimą policzy odbiór o 60 min źle → near-future przeskoczy na jutro (`+1 dzień` guard). Źródło: `datetime.now(_WARSAW)` zamiast `ZoneInfo("Europe/Warsaw")`.
2. **🔴 `gastro_edit` TOCTOU/lost-update bez integrity guard.** Wysyła CAŁY formularz (status+kurier ze snapshotu); concurrent assign/status w oknie read→write zostaje cofnięty. Bliźniak `panel_sync._write_panel_status` MA `PanelIntegrityError`, gastro_edit NIE — asymetria rodziny update-zamowienie.
3. **🟠 Konkatenacja adresu (dowód live 484269).** `delivery_address`(cały)→`--street` + rezydualny `nr_domu` → `full_address="Mroźna 10/23 10"`; do gastro duplikacja numeru. Fix u źródła: przy delivery_address zignoruj/wyczyść nr_domu.
4. **🟠 Słaba detekcja sukcesu → fałszywy OK.** `'error' not in body` w #1/#2; konsola ufa `ASSIGN_OK/EDIT_OK` → łańcuch propaguje fałszywy sukces. Brak retry pogłębia (jednorazowa próba, brak twardej weryfikacji zapisu).
5. **🟠 Force-recheck: kruchości kliku.** (a) `drain` destrukcyjny + oid poza `current_state` = cicha strata; (b) ścieżka button→audyt NIEUDOWODNIONA (0 wpisów audytu, 1 dren spoza UI); (c) elastyk `deliberate` omija forward-only → może ściągnąć gastro-śmieć wstecz gdy klik bez realnej zmiany rutcomu (czasówka chroniona, elastyk nie).

**Dalej (LOW):** #6 sprzeczność semantyki `time=0` w keep-time; #7 `get_kurier_id` niejednoznaczny match→pierwszy (cichy zły cel); #8 rozjazd strażnika czasówki panel_watcher↔state_machine (kruche, spójne tylko bo 1 emiter); #9 detekcje ck/pickup frozen-na-starcie (killswitch ≠ hot-reload, w przeciwieństwie do force-flagi); #10 `PICKUP_TIME_UPDATED=0` — kanał mirror czasówki dormant (efektywność/observability).
