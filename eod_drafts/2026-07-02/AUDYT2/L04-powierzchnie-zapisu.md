# L04 — POWIERZCHNIE ZAPISU do źródła prawdy (ręce piszące decyzję)

**Pas:** 0.E / L04 — `coordinator_time_recheck.py` + `gastro_edit.py` (ręce mutujące committed stan).
**Data:** 2026-07-02 (recon na żywo 01.07 wieczór).
**Tryb:** READ-ONLY na produkcji. Zero POST do gastro, zero edycji kodu/flag/serwisów. Wolno: czytać, grep, systemctl/journalctl/cat logów. Zapis: TYLKO ten deliverable.
**Relacja do `SWEEP_write_hands.md` (ten sam katalog):** SWEEP pokrył tę klasę szeroko, część twierdzeń „z lektury". L04 = **niezależna weryfikacja gruntem** (grep/kod/logi) + głębiej na 2 pytania zadania (floor shift_start; TOCTOU/callerzy) + **korekty** SWEEP (okno TOCTOU, callerzy address_mismatch). Gdzie potwierdziłem SWEEP — oznaczam „SWEEP potwierdzony gruntem".

---

## 0. MAPA CALLERÓW (grunt, nie lektura)

Grep całego workspace (`.py`, `.service`, `.timer`, `.sh`), z wyłączeniem `eod_drafts`/testów:

| Moduł | Realny caller | Ścieżka wywołania | Stan LIVE |
|---|---|---|---|
| `gastro_edit.py` (`update-zamowienie`) | **konsola** `nadajesz_clone/.../integrations/ziomek/assign.py::edit_order` | `subprocess.run(cmd + ["--commit"], cwd=ziomek_scripts_dir)`; sukces = `returncode==0 and "EDIT_OK" in out` (assign.py l.~46) | **LIVE** (`COORDINATOR_EDIT_LIVE=1`) |
| `gastro_edit.py` | `core/config.py:250` `ziomek_edit_script` | tylko stała ścieżki (nie wywołanie) | — |
| `coordinator_time_recheck.enqueue` | **konsola** `coordinator.py:391 POST /refresh-time` → `integrations/ziomek/coordinator_time_recheck.py:43 request_refresh` → subprocess `ctr.enqueue` | producent kolejki | **LIVE** (flaga ON) |
| `coordinator_time_recheck.drain` | `panel_watcher.py:2170-2171` `_diff_and_emit` (raz/tick) | konsument+GC | **LIVE** |

**Korekta SWEEP:** `address_mismatch.py:130` i `tools/address_mismatch_review.py:126` to **KOMENTARZE** o `gastro_edit.regeocode_and_update`, **nie callerzy** (grep import/subprocess/regeocode = 0 realnych wywołań). Jedynym realnym wywołującym `gastro_edit` jest konsola (`edit_order`). `offpeak_activation_reminder_2026-06-27.py:18` = string w przypominajce.
**Grunt:** `grep -rn systemd/cron gastro_edit|refresh-time` = **0** — żaden automat nie pisze tą ręką; wyłącznie klik koordynatora.
**Dwie kopie `gastro_edit.py`:** `scripts/gastro_edit.py` to **symlink** → `dispatch_v2/gastro_edit.py` (`ls -la` potwierdza). NIE rozjazd bliźniaczy — jeden plik.

---

## 1. `coordinator_time_recheck.py` — kolejka force-recheck (pytanie: floor shift_start? strażnik?)

**Czym JEST:** czysta kolejka `enqueue`/`drain` (oid→stempel UTC w `dispatch_state/coordinator_time_recheck.json`). **Sama NIC nie pisze do committed** — deleguje re-check do `panel_watcher` (drenuje) → `_diff_czas_kuriera`/`_diff_pickup_time` (`deliberate=True`) → `state_machine.update_from_event` (`CZAS_KURIERA_UPDATED`/`PICKUP_TIME_UPDATED`) → `upsert_order`. Higiena kolejki: atomic temp+fsync+rename (l.58-75), flock (l.32-44), TTL 5 min (l.29), idempotentny enqueue. **Ta warstwa jest solidna.**

### 1a. FLOOR shift_start — ODPOWIEDŹ: NIE MA strażnika w ścieżce ZAPISU (grunt)
- `grep -n "shift_start\|floor\|clamp" state_machine.py` = **PUSTE**. Handlery `CZAS_KURIERA_UPDATED` (state_machine.py:672-732) i `PICKUP_TIME_UPDATED` (l.734-799) piszą **surową wartość deklarowaną** (`czas_kuriera_warsaw`/`pickup_at_warsaw`) — jedyne kontrole to sanity iso↔hhmm (`_verify_czas_kuriera_consistency`, l.678) i parse-ISO (l.749). **Żadnego `max(declared, shift_start)`.**
- **Wniosek:** force-recheck (i każdy inny writer CK) **MOŻE zapisać committed `czas_kuriera`/`pickup_at` poniżej `shift_start`** bez floor tutaj. Floor istnieje TYLKO downstream (render `CLAMP_PRESHIFT_PICKUP_ETA` w `fleet_state`, feasibility) — dokładnie tracked issue [[preshift-pickup-floor-audit-2026-06-30]] („17 miejsc liczy czas odbioru, tylko 4 mają floor; brak jednego źródła"). Force-recheck = jeden z ~13 kanałów zapisu BEZ floor. Nie jest unikalnie groźny, ale JEST HARD-committed write bez floor. **Severity P2** (znane, tracked, downstream mityguje, materialność ~0).

### 1b. Force (deliberate=True) OMIJA strażniki anty-wobble — z jednym wyjątkiem (grunt kodu + logów)
Strażniki w `panel_watcher._diff_czas_kuriera` (l.843-873) i `state_machine` (l.695-719):
- **Czasówka:** suppress ZAWSZE, **także deliberate** (`panel_watcher.py:847-860` — „guard suppress ZAWSZE, także przy deliberate"). ✅ Force NIE wstrzyknie śmieciowego `czas_kuriera` czasówce wprost (idzie kanałem `pickup_at`→mirror). **Bezpieczne — SWEEP potwierdzony gruntem.**
- **Elastyk forward-only:** `if _eguard and delta_min < 0 and not deliberate` (`panel_watcher.py:867`) — **deliberate BYPASS**. Force MOŻE cofnąć `czas_kuriera` elastyka wstecz.
- **Aktywność strażnika (logi, cały `watcher.log`):** `CK_ELASTYK_BACKWARD_BLOCKED` = **2070**, `CK_PASSIVE_SUPPRESSED` = **4878**. Passive-path próbuje ściągać śmieciowy wcześniejszy `czas_kuriera` **2070×** i jest blokowany. **Force (deliberate) puściłby każdy z tych 2070.** Śmieć-źródło jest DOWIEDZIONE częste; jedyny warunek zapisu = koordynator kliknie „Odśwież" na elastyku, którego gastro w międzyczasie przestemplowało wcześniej, NIE zmieniwszy realnie rutcomu (złamanie kontraktu „klik = zmieniłem czas"). **Severity P2 latentne** — footgun, nie bug (bypass jest intencjonalny), ale bez sanity floor.

### 1c. `drain()` destrukcyjny — cicha strata kliku (grunt kodu)
`drain` czyści CAŁĄ kolejkę `_save({})` (l.114) i zwraca świeże oid. Konsument w `panel_watcher` iteruje `current_state.items()` (l.2181), `_force = zid in _force_ids`. Jeśli forsowany oid **jeszcze nie jest w `current_state`** (nie zaingestowany) → nigdy nie iterowany → kolejka już wyczyszczona → **klik przepada bez logu**. `zid not in html_order_ids` daje log (l.2196-2198), ale „w ogóle poza current_state" — nie. Okno wąskie (oid musi być świeży poza state). **Severity P3.** Dodatkowo drain owinięty bare-except (l.2177) fail-soft — automat leci dalej, klik ginie.

### 1d. Frozen R27 — interakcja (potwierdzenie intencji)
Force NADPISUJE frozen `czas_kuriera` (R27 ±5) i woła `_invalidate_plan_on_committed_change` (panel_watcher.py:2247) → silnik re-planuje z nowym oknem. To **jedyna legalna ścieżka rusząca frozen `czas_kuriera` elastyka w OBIE strony** (obok first_acceptance). Zgodne z intencją „ręczna decyzja = przemrożenie na nową wartość". OK.

### 1e. MATERIALNOŚĆ (grunt logów, cały `watcher.log` 16 MB)
- `COORDINATOR_FORCE_TIME_RECHECK drained` = **1** — jedyny dren `2026-06-30 09:37:54 [484410]`. **Button użyty DOKŁADNIE raz od deployu (30.06).**
- Kolejka na dysku: `{}` (pusta), perms `-rw-------` (0600), zdrowa, nie utknęła.
- Audyt konsoli `coordinator_assign_audit.jsonl`: **0** wpisów `kind=coordinator_time_recheck`. Dren 484410 był więc **spoza audytowanego UI** (ręczny `enqueue` przy weryfikacji [[czas-kuriera-closest-day-anchor-2026-06-30]]). **Ścieżka button→request_refresh→audyt→drain NIEUDOWODNIONA na żywo.** **Severity INFO/P3** (feature świeży, realnie nietknięty przez UI — 1. prawdziwe użycie odsłoni niesprawdzoną ścieżkę).

---

## 2. `gastro_edit.py` — realny zapis do gastro (`update-zamowienie`)

**Uwaga wstępna (rozdzielenie od zadania):** `gastro_edit.py` używa `update-zamowienie` (ZAPIS) + `edit-zamowienie` (ODCZYT). **NIE dotyka `czas_kuriera`** (docstring l.25: „Czas zmieniaj gastro_assign --time"). Landmine `--keep-time`/„sending 0 clears UI" i endpoint `przypisz-zamowienie` należą do **`gastro_assign.py`** (osobny plik, `scripts/gastro_assign.py`), nie do gastro_edit. Zadanie zlało dwa moduły — L04 rozdziela.

**Model bezpieczeństwa:** DRY-RUN domyślnie, realny zapis `--commit`. „Bezpieczny merge": czyta pełne zlecenie (`read_order`→`edit-zamowienie`), odtwarza KOMPLET pól formularza, nadpisuje TYLKO podane `--opcje` (build_payload l.189-227), POST całego formularza (l.230-237). Pojedynczy POST = atomic po stronie gastro.

### 2a. 🔴 TOCTOU / lost-update — brak integrity guard, którego bliźniak MA (grunt gruntowy)
`build_payload` ZAWSZE wstawia `modal_status_zamowienia` i `modal_kurier_select` ze snapshotu `read_order` (gastro_edit.py:210-211), a `post_update` wysyła CAŁY formularz. Jeśli między `read_order` (l.268) a `post_update` (l.291) ktokolwiek zmieni zlecenie na gastro (auto_assign, tap kuriera, `panel_sync` odbicie statusu apki, drugi koordynator) → edycja **COFA `id_kurier`/`status` do wartości sprzed odczytu**. Konkretny skutek: kurier przypisany w oknie → **odprzypisany**; `picked_up`(5) w oknie → **cofnięty do `dojazd`(3)**. To korupcja HARD-stanu, desync gastro↔rzeczywistość.

**Asymetria rodziny „wyślij cały formularz" (dowiedziona gruntem):**
- `courier_api_panelsync/panel_sync.py:55` `class PanelIntegrityError` + l.219-236 „**Twarda weryfikacja: re-read (bez cache) i sprawdź, że zmienił się WYŁĄCZNIE status**" → raise przy clobberze; l.282-284 łapie i liczy `integrity_breach`. **Ma strażnik.**
- `gastro_edit.py`: `grep integrity|verify|re-read|conflict|version` = **PUSTE**. **Zero ochrony.**
- Oba piszą do TEGO SAMEGO `update-zamowienie`. Wzorzec fixu istnieje w bliźniaku, świadomie zastosowany do panel_sync, **pominięty w gastro_edit** = niespójność rodziny.

**Korekta SWEEP (uczciwie):** SWEEP pisał „okno read→(login)→write ≈ kilka s". Grunt: `login()` jest PRZED `read_order` (main l.264→268→291), więc okno read→write = `build_payload`+diff+print ≈ **sub-sekundowe**, nie „kilka s". To OBNIŻA prawdopodobieństwo kolizji vs framing SWEEP. Okno nadal >0 (concurrent auto_assign/panel_sync w tej sub-sekundzie klobruje), ale realnie małe.

**Severity P1** (mina na flipie/skali): impact HARD (odprzypisanie/cofnięcie statusu), bliźniak dowodzi że fix jest znany, **eskaluje wprost pod autonomią** (`auto_assign_executor` = concurrent writer do gastro). **Materialność DZIŚ ~0:** edit-live = **2** zdarzenia total (§2e), okno sub-sekundowe → kolizja dziś skrajnie mało prawdopodobna. Latentna mina, nie żywy pożar — ale to dokładnie klasa, która gryzie przy współbieżnych piszących.

### 2b. Detekcja sukcesu SŁABA → fałszywy EDIT_OK w łańcuchu (grunt)
`post_update` sukces = `code == 200 and 'error' not in low and 'exception' not in low` (l.296). Body 200 bez słów „error"/„exception" (np. odbicie na stronę logowania po wygaśnięciu sesji, HTML listy zleceń) → drukuje `EDIT_OK`. Konsola `edit_order` ufa: `returncode==0 and "EDIT_OK" in out` (assign.py) → **fałszywy sukces propaguje do koordynatora jako „zapisano"**. Brak retry (`urlopen(timeout=15)`→bare except→exit 1) pogłębia: jedna próba, brak twardej weryfikacji zapisu (re-read). **Severity P2.**

### 2c. CookieJar / install_opener — BEZPIECZNE dzięki izolacji subprocess (rozwianie obawy zadania)
Zadanie pytało o thread-safety CookieJar (CLAUDE.md landmine: `install_opener` z nowym CookieJar inwaliduje sesję → 419). `gastro_edit.login()` woła `urllib.request.install_opener(opener)` z NOWYM CookieJar (l.63-66) — GLOBALNY opener. **ALE gastro_edit biega jako ODDZIELNY subprocess (CLI)** → jego `install_opener` dotyka tylko własnego procesu, **NIE inwaliduje sesji watchera/panelu** (inny interpreter, inne globalsy). Sekwencyjny (jeden login→jeden POST). **Obawa zadania = nieaktualna dla gastro_edit; potwierdza słuszność architektury subprocess-isolation.** (Landmine dotyczy piszących IN-PROCESS, nie tego subprocessu.) **INFO — nie luka.**

### 2d. Poboczne (niższa waga)
- **Lossy rebuild `modal_platnosc`** (build_payload l.193-196): `platnosc` przeliczany z `price` (`if price: karta/gotowka else brak`). Zlecenie z `price` falsy (COD=0, np. czasówki Mali Wojownicy COD=0) → `platnosc` reset do `"brak"` przy KAŻDEJ edycji dowolnego pola. **Hipoteza z lektury** — mapping gastro JS niezweryfikowany; impact niski (COD=0 = brak płatności). **Severity P3.**
- **Konkatenacja adresu** (SWEEP potwierdzony, dowód live 484269 „Mroźna 10/23 10"): `regeocode_and_update` full_address = `' '.join((new_street, nr_domu))` (l.310), gdzie `nr_domu` = STARY numer ze snapshotu, a `new_street` (z konsoli mapuje cały `delivery_address`) już zawiera numer → duplikacja. Koordy się rozwiązują, ale liczone ze zlepka; do gastro `modal_nr_domu` idzie niezmieniony. **Severity P3**, fix u źródła: przy `delivery_address` (cały adres) wyzeruj rezydualny nr_domu.
- **Regeocode fail-soft** (l.141-173): błąd importu/geokodu/zapisu → log+None, gastro już zapisane. Częściowy sukces = gastro nowy adres, orders_state stare coords do następnego ticku. Poprawnie fail-soft, `ENABLE_REGEOCODE_SYNC_TEXT=True` LIVE synchronizuje też tekst → spójny stale (nie split). **OK.**
- **city fail-loud** (l.223-225): nieznane miasto → `sys.exit(2)`, nie zgaduje. **Dobre.**

### 2e. MATERIALNOŚĆ (grunt audytu)
`coordinator_assign_audit.jsonl`: **20** wpisów `kind=edit`, z czego LIVE = **2** (`484129` 28.06 Zaciszna/Olmonty; `484269` 29.06 Mroźna 10/23 — dowód konkatenacji); reszta SHADOW (przed aktywacją `COORDINATOR_EDIT_LIVE` 27.06 13:16). **edit-live ≈ 1/dzień gdy używane, ~0 bazowo.**

---

## 3. CO JEST SOLIDNE (nie ruszać / potwierdzenia)
- Kolejka `coordinator_time_recheck`: atomic+flock+TTL, idempotentna, stan na dysku zdrowy (0600, pusty).
- Czasówka passive guard suppresuje CK ZAWSZE, także deliberate → force nie skorumpuje `czas_kuriera` czasówki wprost (kanał = pickup_at mirror).
- gastro_edit: pojedynczy POST atomic po stronie gastro; bezpieczny merge (odtwarza komplet pól); dry-run domyślny; city fail-loud; regeocode fail-soft; subprocess-isolation CookieJar.
- Dwie kopie gastro_edit = symlink (nie rozjazd).
- closest-day anchor (30.06): force używa tego samego `normalize_order`/`_czas_kuriera_to_datetime` co automat → data spójna force↔passive (SWEEP potwierdzony).

## 4. CAVEATY PRAWDY
- Materialność mierzona z `watcher.log` (cały, 16 MB, od ~ostatniej rotacji) i `coordinator_assign_audit.jsonl` — **proxy** button-truth, nie fizyczna. „drained=1" / „edit-live=2" = liczba zdarzeń w tym oknie logu; jeśli log rotował, historia sprzed rotacji nieujęta (ale kolejka pusta + audyt od 27.06 spójne → wolumen realnie znikomy).
- TOCTOU (2a), lossy platnosc (2d), false-success przy session-bounce (2b) = **mechanizmy z kodu**; ich REALNE odpalenie na produkcji **nie zaobserwowane** (0 dowodów w logach) — to miny latentne, nie zaobserwowane pożary. Oznaczam jako hipotezy-z-kodu tam gdzie brak fizycznego dowodu.
- `PICKUP_TIME_UPDATED` = **0** w oknie logu → kanał mirror czasówki (legalna zmiana committed czasówki) DORMANT; osobna obserwowalność (LOW), poza rdzeniem L04.
