# L08 — WYŚCIGI (klasa O) i KRAWĘDZIE CZASU (klasa L)

**Pas:** AUDYT 2.0 / PION 0.C — klasy bez metody runtime w audycie 1.0 (badane tylko lekturą → ta sama ślepota co przeoczone P0).
**Data:** 2026-07-02 (recon na żywo 01.07 ~22:50–23:05 UTC).
**Tryb:** READ-ONLY na produkcji. Zero edycji .py/.json/flag, zero systemctl mutującego, zero Telegrama. Diagnostyka: read/grep/systemctl is-active/cat/list-timers. Jedyny zapis = ten plik.
**Metoda:** kod (plik:linia) + stan LIVE (systemctl/flags.json/mtime) + PROJEKT reproduktora/harnessu (NIE odpalany na produkcji — opis jak na KOPII). Caveat prawdy stosowany: gdzie brak fizycznego dowodu odpalenia → „hipoteza z kodu".

---

## TL;DR (co realnie znalazłem)

| # | Klasa | Ustalenie | Sev | Materialność DZIŚ |
|---|---|---|---|---|
| O1 | wyścig | `pending_proposals.json` — **3 pisarzy, ZERO exclusive-lock**; RMW (load→merge→save) nieserializowany. Bezpieczne tylko przez UŚPIENIE 2 z 3 (telegram inactive, postpone queue pusty), nie strukturalnie. | **P2** | 0 (1 żywy writer); postpone-timer żyje co 60s, telegram 1× `start` od bycia 2. writerem |
| O2 | wyścig | `orders_state.json` — **dobrze chroniony** (LOCK_EX na CAŁY RMW). NIE luka; wzorzec do naśladowania dla O1. 1 caveat offline-tool + koszt whole-file O(n). | INFO/P3 | 0 |
| O3 | wyścig | `load_plan` read-with-side-effect — TOCTOU (read LOCK_SH→release→invalidate LOCK_EX) + **brak CAS** na rodzinie invalidate/advance (ma go tylko `save_plan`). Mityguje flaga, ale caller czyta ją bez bezpiecznego defaultu. | **P2** | 0 z flagą ON; WRACA gdy flaga zniknie/awaria flags.json |
| L1 | czas | Rozjazd konwencji parserów naive (`parse_panel_timestamp`=Warsaw vs `_parse_dt_utc`=UTC) + martwa-myląca obrona `.replace(tzinfo=utc)` na shift_start/end (w LIVE zawsze aware). | **P2** | 0 (producent emituje offset-ISO); latentne 2h na naive-leak |
| L2 | czas | Fixed-offset **CEST hardkodowany w ŻYWYM toolu** (`shadow_outcome_enricher`, timer active) → **1h błąd po DST (25.10.2026)**; +kilka uśpionych toolów tej samej klasy. | **P2** | 0 do DST; deterministyczne 1h po; DST ~4 mies. = mina |
| L3 | czas | Midnight / night-shift anchor — cross-ref L03 (producent datuje UTC → 00:00-02:00 Warsaw zły dzień). Potwierdzam mechanizm konstrukcji dt (ZoneInfo=DST-safe within-day, 24:00 handled, night-shift→future wrap). | P2 (cross-ref) | jak L03 |
| L4 | czas | Panel rollover 22:00 UTC — obsłużony strukturalnie (`parser_health` active_ids, fix 07.05); readerzy pending atomic-safe. Residual = L3. | INFO | brak nowej luki |

---

## CZĘŚĆ A — WYŚCIGI (klasa O)

### O1 — `pending_proposals.json`: 3 pisarzy bez exclusive-lock (P2)

**Mapa pisarzy (grunt, grep całego drzewa `.py` bez tests/eod_drafts):**

| Pisarz | plik:linia | Higiena | Serializacja | Stan LIVE |
|---|---|---|---|---|
| `telegram_approver.save_pending` | `telegram_approver.py:1759-1766` | atomic tmp→fsync→`os.replace` | **BRAK flock** | **inactive** (`dispatch-telegram` muted od 26.06) |
| `shadow` przez `pending_proposals_store` | `pending_proposals_store.py:43-51` (`save`) + `:87-107` (`upsert_proposals` = load→sweep→merge→save) wołane w `shadow_dispatcher.py:1349-1356` | atomic `os.replace` | **BRAK flock** | **active** (jedyny realny writer; `ENABLE_PENDING_PROPOSALS_WRITE=True` w flags.json; `shadow_dispatcher.py:1133`) |
| `postpone_sweeper` | `postpone_sweeper.py:150-158` (`_load_json_safe`→mutacja→`_atomic_write_json` `:32-47`) | atomic `os.replace`; read pod `LOCK_SH` (`:59`) | **write NIE trzyma LOCK_EX przez RMW** | timer **active** co 60s (`dispatch-postpone-sweeper.timer`), ale `postponed_proposals.json={}` → realnie idle |

Czytelnicy (nie piszą, atomic-safe): `panel_watcher.py:222/386/452` (PANEL_OVERRIDE / PANEL_AGREE / `_save_plan_from_pending`, wszystkie `open(...,"r")`), `tools/pending_global_resweep.py:60` (READ-ONLY per opis unitu). `panel_watcher` **NIE** jest 4. pisarzem (potwierdzone grepem — same odczyty).

**Istota wyścigu (grunt kodu):** wszyscy trzej robią **read-modify-write całego dicta** (`load()` → `pending[oid]=...` → `save()`), a `os.replace` gwarantuje TYLKO brak torn-read (czytelnik nigdy nie widzi połówki), **NIE** serializację cykli RMW. Klasyczny lost-update:
```
A.load()={X}   B.load()={X}   A.save({X,A})   B.save({X,B})   → wpis A GINIE
```
Żaden z trzech nie bierze `flock LOCK_EX` na plik pending (kontrast: `state_machine._locked_write` — patrz O2). `postpone_sweeper` bierze `LOCK_SH` tylko na odczyt i zwalnia przed zapisem → jego RMW też nieatomowy względem shadow.

**Dlaczego DZIŚ bezpieczne — i dlaczego to fragilne, nie strukturalne:**
- Docstring `pending_proposals_store.py:12-14` sam przyznaje warunek: *„Pisarz: shadow_dispatcher (jedyny **po wyłączeniu Telegrama/postpone** → brak wyścigu pisarzy)"*. Bezpieczeństwo = emergentne z uśpienia dwóch, nie z blokady.
- `dispatch-shadow` = `Type=simple`, jeden długo-żyjący proces, `POLL_INTERVAL_SEC=5` (`shadow_dispatcher.py:55`), pętla sekwencyjna → **brak self-race** (jeden `upsert_proposals` per tick). Shadow sam ze sobą jest OK.
- Ale: (1) `dispatch-postpone-sweeper.timer` **jest aktywny co 60 s** — w chwili gdy pojawi się choć jeden wpis w `postponed_proposals.json` (re-enable Telegrama; zaległy postpone; ręczny replay), `postpone_sweeper` przy verdykcie ASSIGN/PROPOSE wykona RMW pending (`:150-158`) **równolegle** z tickiem shadow (co 5 s) → lost-update. (2) `dispatch-telegram` jest 1× `systemctl start` od bycia 3. współbieżnym writerem (jego `save_pending` też bez flock).

**Skutek lost-update (hipoteza z kodu, nieobserwowana):** zgubiony wpis PROPOSE w pending → `panel_watcher` nie wykryje PANEL_OVERRIDE/PANEL_AGREE dla tego oid (`:222/:386`), `_save_plan_from_pending` nie zapisze kanonu trasy (`:452`), a fundament Fazy C / `pending_global_resweep` straci wiszące zlecenie z pomiaru. To CICHA strata sygnału, nie crash.

**Materialność:** **0/dzień dziś** (1 aktywny writer, `postponed={}`). Nie policzalne w zł — to „istnienie klasy + warunkowa fragilność". Pod autonomią (`auto_assign_executor` jako kolejny potencjalny konsument/writer stanu) lub po re-enable Telegrama staje się żywe.

**PROJEKT REPRODUKTORA (na KOPII, NIE na produkcji):**
1. `cp dispatch_state/pending_proposals.json /tmp/audit/pp_copy.json` (kopia).
2. Skrypt z 2 procesami (`multiprocessing`) celującymi w `/tmp/audit/pp_copy.json`:
   - P1 pętla 500× : `d=json.load(...); d["W1_%d"%i]={...}; atomic_replace(...)` (klon `pending_proposals_store.save`).
   - P2 pętla 500× : `d=json.load(...); d["W2_%d"%i]={...}; atomic_replace(...)` (klon `postpone_sweeper._atomic_write_json`).
   - oba z losowym `sleep(0..3ms)` między load a save (poszerza okno).
3. Oczekiwanie przy BRAKU locka: `len(keys(W1_*)) + len(keys(W2_*)) < 1000` (część nadpisana) → **udowodniony lost-update**. Porównawczo: wariant z `flock(LOCK_EX)` obejmującym cały RMW → 1000/1000 (brak straty).
4. Zero dotknięcia produkcyjnego pliku/serwisu — tylko `/tmp/audit`.

**Rekomendacja u źródła:** ujednolicić WSZYSTKICH trzech pisarzy na wspólny `flock(LOCK_EX)` obejmujący cały cykl load→merge→save (dokładnie wzorzec `state_machine._locked_write`, O2) — jeden helper `pending_proposals_store.locked_upsert(...)`, z którego korzystają telegram_approver i postpone_sweeper. Naprawa PRZED re-enable Telegrama i PRZED 1. flipem autonomii.

---

### O2 — `orders_state.json` multi-proces: DOBRZE CHRONIONY (INFO/P3, nie luka)

**Grunt kodu:** `state_machine.upsert_order` (`state_machine.py:454-474`) trzyma `LOCK_EX` przez **CAŁY** read-modify-write:
```
with _locked_write() as path:          # LOCK_EX na <state>.lock (:207-220)
    state = _read_state_strict()        # świeży odczyt WEWNĄTRZ locka (:459)
    merged = {**existing, **data, ...}  # merge polowy
    _guarded_write(path, state, old_count, op="upsert")   # zapis WEWNĄTRZ locka
```
- `_read_state_strict` czyta świeżo z dysku pod `LOCK_SH` (`:367-383`) → drugi proces czekający na `LOCK_EX` po wejściu widzi zmiany pierwszego → **brak lost-update** dla wzorca same-writer.
- Merge **polowy** (`{**existing, **data}`) → różni pisarze dotykający RÓŻNYCH pól serializują się poprawnie (drugi czyta świeże po pierwszym).
- `_guarded_write` (`:267-291`) blokuje regresję liczności (`new_count >= old_count`) + throttled alert (`:299-323`) → obrona przed clobberem kurczącym stan.
- **Zero bezpośrednich pisarzy z pominięciem locka:** grep `orders_state.json` + `dump/write/replace` = tylko komentarz w `courier_ground_truth.py:4` („jeden writer, żeby uniknąć wyścigu"). Wszystkie 30+ modułów piszą przez `state_machine`.

**To jest wzorzec do naśladowania dla O1**, nie luka. Dwa caveaty (niższa waga):
- `tools/rebuild_state_from_events.py:141` — offline recovery: monkeypatchuje `_atomic_write` na no-op (`:105`) i robi własne `os.replace` **z pominięciem `_locked_write`**. Odpalony współbieżnie z żywymi pisarzami → clobber. To narzędzie ręcznej rekonstrukcji (uruchamiane świadomie przy quiescencji); **P3/INFO**.
- **Skala:** komentarz `state_machine.py:935-943` sam ostrzega: „CAŁY plik pod LOCK_EX → koszt każdego upsertu = O(cały stan)… ~3500 pełnych zapisów 8 MB pod LOCK_EX = godziny". Poprawne dla korektności, ale przy ×2-×10 wolumenu `LOCK_EX` na whole-file JSON = kandydat na contention (pas 3.B load-replay to zmierzy). **P3, oś skala.**

---

### O3 — `load_plan` read-with-side-effect: TOCTOU + brak CAS na invalidate (P2)

**Grunt kodu (`plan_manager.py:121-160`):**
```
with _locked(exclusive=False):     # LOCK_SH
    plans = _read_raw()            # (:146-147)
# <-- LOCK ZWOLNIONY
...
if plan_oids and not plan_oids.issubset(active_bag_oids):
    if invalidate_on_mismatch:
        invalidate_plan(cid, "ORDER_DELIVERED_ALL")   # (:158) BIERZE NOWY LOCK_EX
    return None
```
Dwa problemy:
1. **TOCTOU:** odczyt (LOCK_SH) i skutek uboczny (`invalidate_plan` LOCK_EX) **NIE są jednym lockiem** — między nimi `advance_plan` z innego procesu może chirurgicznie wykreślić dostarczony stop (plan znów spójny), a `invalidate_plan` i tak go zarznie na podstawie NIEAKTUALNEGO snapshotu. To udokumentowany root oscylacji carried-first (`plan_manager.py:132-141`: „read widzi stop planu spoza worka i DRZE CAŁY plan… konsola mruga co tick").
2. **Asymetria CAS:** `save_plan` MA optymistyczny CAS (`expected_version`, `:178-186` → `ConcurrencyError`), ale `invalidate_plan` (`:213-226`), `advance_plan` (`:270-296`), `touch_plan` (`:245-255`), `remove_stops` NIE mają. `invalidate_plan` bezwarunkowo stempluje `invalidated_at` na tym, co zastanie (`:220-224`) — stale-decyzja klobruje plan świeżo zapisany (save_plan bumpnął wersję, invalidate ją ignoruje). Każda z tych funkcji sama w sobie robi pełny RMW pod `LOCK_EX` (spójna wewnętrznie) — dziura jest w **decyzji podjętej poza lockiem**.

**Stan mitygacji (LIVE):** `ENABLE_LOAD_PLAN_PURE_READ=True` (flags.json potwierdzone). Oba callery przekazujące `active_bag_oids` (jedyne mogące odpalić side-effect) używają pure-read:
- `dispatch_pipeline.py:2374-2376` (`_soon_free` probe): `invalidate_on_mismatch=not C.flag("ENABLE_LOAD_PLAN_PURE_READ")`
- `dispatch_pipeline.py:3804-3806` (base_sequence read): to samo.
- Pozostali callery (`plan_recheck.py:1777/1828`, `panel_watcher.py:578`, `tools/b_route_shadow.py:265`, `tools/bundle_calib_shadow.py:382`) wołają `load_plan(cid)` BEZ `active_bag_oids` → gałąź mismatch (`:153`) nieaktywna → zero side-effectu. OK.

**MINA (grunt):** callery czytają flagę jako `C.flag("ENABLE_LOAD_PLAN_PURE_READ")` **bez drugiego argumentu**, a `common.py:46` `def flag(name, default=False)` → **domyślnie False**. Więc: usunięcie klucza z flags.json, literówka, albo fail-soft `load_flags()→{}` przy uszkodzonym flags.json ⇒ `invalidate_on_mismatch = not False = True` ⇒ **oscylacja carried-first WRACA**. Bezpieczny „default" jest tu ustawiony na wartość NIEBEZPIECZNĄ. To dokładnie klasa „mina na flipie/awarii".

**Materialność:** 0 z flagą ON (dziś). Objaw przy powrocie = „konsola mruga co tick na carried-first" (udokumentowany, case Jakub W / Piotr K). Nie zł/dzień — sygnał UX + drtwienie planu.

**Rekomendacja u źródła:** (1) callery → `C.flag("ENABLE_LOAD_PLAN_PURE_READ", True)` (absencja=bezpiecznie); docelowo retire flagi i uczynić pure-read jedynym zachowaniem czytelników. (2) dodać `expected_version` (CAS) do `invalidate_plan`/`advance_plan` albo re-walidować warunek mismatch WEWNĄTRZ locka invalidate (read→check→write jednym `LOCK_EX`), by stale-decyzja nie klobrowała świeżego planu.

---

## CZĘŚĆ B — KRAWĘDZIE CZASU (klasa L)

### Fundament (grunt): łańcuch parsowania jest w większości SOLIDNY
Zweryfikowane, że producent panelu emituje **aware** datetime i serializuje z offsetem:
- `panel_client._parse_warsaw_naive` (`:561-566`): `czas_odbioru_timestamp` (wall-clock Warsaw) → `strptime(...).replace(tzinfo=WARSAW_TZ)` = **aware Warsaw**; fail-loud `_warn_once` na nieparsowalnym (Lekcja #32). Poprawne.
- `panel_client._czas_kuriera_to_datetime` (`:578-635`): HH:MM → `datetime.combine(..., tzinfo=WARSAW_TZ)` z closest-day anchor (fix 30.06); ZoneInfo → **DST-safe**, północ obsłużona. Poprawne.
- `panel_client._parse_utc` (`:638`): `created_at` (Z) → aware UTC. Poprawne.
- Serializacja do orders_state: `.isoformat()` na aware (`:737/744`) → string **niesie offset** (`+02:00`/`+01:00`). Downstream `datetime.fromisoformat` respektuje offset niezależnie od DST.
- `feasibility_v2.py:676-677` `pickup_ref = pickup_ref.replace(tzinfo=utc)` — **GUARDED** `if pickup_ref.tzinfo is None`; aware przechodzi bez zmian. OK.
- `_shift_start_dt/_shift_end_dt` (`courier_resolver.py:1276-1311`): `datetime.now(WAW).replace(hour=...)` → **aware Warsaw**; `.replace(hour)` zachowuje ZoneInfo, offset re-rozwiązywany per-wall-clock → DST-safe within-day; „24:00" → `now.replace(hour=0)+timedelta(days=1)` obsłużone.

Na tym tle realne krawędzie to L1–L4.

### L1 — rozjazd konwencji naive + martwa-myląca obrona `.replace(tzinfo=utc)` (P2)

**(a) Dwie przeciwne konwencje naive (root `naive-datetime-tz-convention-split`):**
- `common.parse_panel_timestamp`: „datetime naive → interpretowany jako **Warsaw**" (docstring + kod).
- `feasibility_v2._parse_dt_utc` (`:121-134`): „`if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)`" → naive = **UTC**.

Te same nazwy pól (`czas_kuriera_warsaw`, `pickup_at_warsaw`) przechodzą przez OBA parsery (`feasibility_v2.py:165` używa `_parse_dt_utc`, `dispatch_pipeline.py:3072` używa `parse_panel_timestamp`). **Bezpieczne TYLKO dopóki producent emituje offset-ISO** (patrz Fundament) — wtedy gałąź naive nie odpala. **Naive-leak** (jakikolwiek pisarz zapisze `czas_kuriera_warsaw` bez offsetu, albo test/replay wstrzyknie naive) do `_parse_dt_utc` = **2h błąd feasibility** (lato) na oknie pickup vs shift_end/shift_start (Gate 2/3, HARD). Hipoteza z kodu — nieobserwowana na żywo, ale konwencje SĄ sprzeczne (fakt, nie hipoteza).

**(b) Martwa-myląca obrona:** `.replace(tzinfo=timezone.utc)` na shift_start/shift_end w:
`courier_resolver.py:1400`, `feasibility_v2.py:729/753/775`, `dispatch_pipeline.py:1576/3882/5232`, `auto_proximity_classifier.py:176`.
Wszyscy settery `cs.shift_start/end` (`courier_resolver.py:1533/1537/1579/1580`) używają `_shift_start_dt/_shift_end_dt` → **zawsze aware** → gałąź `if tzinfo is None` **martwa w LIVE**. Ale gdyby przyszły producent (working-override, manual, drugie miasto) podał **naive-Warsaw** → stempel UTC = 2h błąd na HARD-gate. To „lying defensive branch" (stempluje ZŁĄ strefę, nie fail-loud). **P2 latentne.**

**Rekomendacja:** jeden kanoniczny parser TZ (jedna konwencja naive), fail-loud (nie ciche stemplowanie) na naive tam gdzie kontrakt mówi „aware"; wyrównać `parse_panel_timestamp` i `_parse_dt_utc` do wspólnej reguły „naive=Warsaw" (zgodnej z producentem panelu).

### L2 — fixed-offset CEST w ŻYWYCH toolach → 1h błąd po DST 25.10.2026 (P2)

**Grunt (grep fixed-offset + systemctl):**
- `tools/shadow_outcome_enricher.py:45` `WARSAW_OFFSET_HOURS = 2  # CEST summer` — **LIVE**: `dispatch-shadow-enrichment.timer` = **active** (potwierdzone). Enricher wzbogaca `decision_outcomes` (karmi ewaluację decyzji). Po ostatniej niedzieli października (CEST→CET) każdy stempel godzinowy będzie **+1h za późno** → skrzywiona atrybucja godzin peak/outcome DOKŁADNIE w oknie DST.
- Uśpione, ale tej samej klasy (mina gdy aktywowane po DST): `tools/freshness_shadow_monitor.py:32` `WARSAW=timezone(timedelta(hours=2))` (unit istnieje, inactive), `tools/reassignment_shadow.py:29` `WAR=…hours=2` (inactive; **UWAGA:** żywy jest osobny `reassignment_forward_shadow.py`, który nie ma fixed-offset — czysty), `tools/sequential_replay.py:69` `WARSAW_OFFSET="+02:00"` (fleet-position-snapshot inactive), `tools/monitor_refloor_peak_2026_05_31.py:75`, `sprint2_analysis/_common.py:12`.
- **Wzór POPRAWNY istnieje** w repo: `tools/ontime_lib.py:45-46` (`_WARSAW_STD`=CET, `_WARSAW_DST`=CEST) — pokazuje, że część kodu wie jak robić dobrze; reszta nie została ujednolicona.

**Materialność:** 0 do 25.10; potem **deterministyczne 1h** dla ~wszystkich stempli w logach/werdyktach tych toolów, dopóki ktoś nie poprawi. Feeds obserwability/kalibracji → skrzywi decyzje pochodne (nie sam silnik, który używa ZoneInfo). DST ~4 mies. → mina czasowa z pewną datą.

**Rekomendacja:** zamienić wszystkie `timezone(timedelta(hours=2))`/`"+02:00"`/`WARSAW_OFFSET_HOURS=2` na `ZoneInfo("Europe/Warsaw")` (jak w `ontime_lib`); dodać jednorazowy grep-strażnik „fixed CEST offset" do higieny toolów (0.G).

### L3 — midnight / night-shift anchor (P2, CROSS-REF L03, nie dubluję)

Producent grafiku datuje w UTC, konsumenci liczą godziny w Warsaw → **00:00-02:00 Warsaw ładuje grafik złego dnia** (L03 §P2, potwierdzone LIVE tam: serwer `Etc/UTC`, o 22:30 UTC `datetime.now().strftime('%d-%m-%y')`≠Warsaw-today). Moja komplementarna weryfikacja: konstrukcja `_shift_start_dt` kotwiczy do `datetime.now(WAW)` **TODAY** → dla zmiany przez północ start=today-HH:MM może wpaść w PRZYSZŁOŚĆ po północy (`pickup_floor_guard.py:61-64` sam dokumentuje pochodną „nocna zmiana zawija" przy `|now−shift_start|>12h`). ZoneInfo czyni samą arytmetykę DST-bezpieczną; problem = WYBÓR DOBY, nie strefa. **Właściciel = L03 (rekomendacja: liczyć `today` z `datetime.now(ZoneInfo('Europe/Warsaw'))` w `fetch_schedule.py`).** Tu tylko potwierdzam mechanizm i spójność z pas L08.

### L4 — panel rollover 22:00 UTC (INFO, brak nowej luki)

Rollover panelu (00:00 Warsaw = 22:00 UTC, reset listy id) obsłużony **strukturalnie** w `parser_health` (fix 07.05: `active_ids = order_ids − closed_ids`, nie goły count — CLAUDE.md/lessons). Czytelnicy `pending_proposals.json` są atomic-safe (`os.replace`), więc rollover nie tworzy torn-read. Jedyny residual rollover-owy = midnight schedule (L3). Nowej krawędzi nie znalazłem.

**PROJEKT HARNESSU KRAWĘDZI CZASU (na KOPII/replice, NIE na produkcji):**
1. Zamrozić czas przez `freezegun`/wstrzyknięcie `now` do `assess_order`/`_shift_start_dt`/`_czas_kuriera_to_datetime` — przejazd 22:00 → 00:00 → 02:00 Warsaw w krokach 5 min, na SYNTETYCZNYM zleceniu + kurierze z grafiku (start 22:00, koniec 24:00/06:00).
2. Inwarianty włączone: (a) `shift_start ≤ shift_end` po wybraniu doby; (b) `|now − shift_start| < 12h` (łapie night-wrap); (c) `pickup_ref` i `shift_*` po tej samej stronie północy; (d) `_czas_kuriera_to_datetime("00:15", pickup=23:45)` → jutro; `("23:45", pickup=00:15)` → wczoraj.
3. Osobny przebieg z `TZ` zamrożonym na **26.10.2026** (dzień po DST): to samo zlecenie przez `shadow_outcome_enricher`/fixed-offset tool → asercja, że stempel = wall-clock Warsaw (wykryje 1h dryf L2).
4. Zero produkcji: replika `dispatch_state` w tmp + monkeypatch `_state_path`.

---

## CO JEST SOLIDNE (nie ruszać / potwierdzenia)
- `orders_state.json`: `LOCK_EX` na cały RMW + guard liczności + brak bypass-writerów (O2) — wzorzec.
- `shadow_dispatcher`: jeden proces `Type=simple`, poll 5s sekwencyjny → brak self-race na pending.
- Łańcuch parsowania panelu (aware Warsaw + offset-ISO + ZoneInfo) — DST-safe w SILNIKU; fail-loud na nieparsowalnym.
- `_czas_kuriera_to_datetime` closest-day (30.06) + `_shift_end_dt` „24:00" — północ obsłużona.
- `save_plan` ma CAS (optymistyczna współbieżność) — brakuje go tylko rodzinie invalidate (O3).
- `reassignment_forward_shadow.py` (żywy) nie ma fixed-offset (czysty); fixed-offset ma tylko uśpiony `reassignment_shadow.py`.

## CAVEATY PRAWDY
- Wszystkie 3 wyścigi (O1 lost-update, O3 TOCTOU) i naive-leak (L1) = **mechanizmy z kodu**; realnego odpalenia na produkcji **nie zaobserwowano** (0 dowodów w logach) — miny latentne, nie żywe pożary. Oznaczam jako „hipoteza z kodu".
- L2 (1h po DST) = **deterministyczny** wniosek z fixed-offset + kalendarza (nie hipoteza), ale skutek materializuje się dopiero 25.10.2026.
- Stan LIVE (systemctl/flags.json/mtime) czytany 01.07 ~22:50-23:05 UTC — dryfuje; każdy flip/re-enable Telegrama zmienia liczbę aktywnych pisarzy O1.
- Materialność w zł/dzień **nie policzona** dla żadnego ustalenia — brak zdarzeń do zliczenia (dziś dormant); podaję „istnienie + warunki aktywacji".
